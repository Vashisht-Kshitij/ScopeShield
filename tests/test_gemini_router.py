"""Deterministic routing, grounding, attribution and persistence; no network."""
import json
import logging

import pytest
import requests

from app.extraction import ExtractionError, validate_extraction
from app.gemini import API_BASE, DEFAULT_MODELS, GeminiModelRouter, configured_provider
from app.scope_comparison import validate_comparison
from app.day6_fixture import PATH, SCENARIOS
from contract_fixtures import sample_output, sample_text
from test_gemini import ProviderResponse, envelope, http
from test_scope_changes import api

PRIMARY, SECONDARY = DEFAULT_MODELS


def router(**changes):
    return GeminiModelRouter("dummy_router_key", allow_live=True,
                             **{"models": DEFAULT_MODELS, "free_tier_models": DEFAULT_MODELS, **changes})


def unavailable(model=PRIMARY):
    return ProviderResponse(404, {"error": {"status": "NOT_FOUND", "message": f"models/{model} is not found for API version v1beta or is not supported for generateContent"}})


def success(output=None, model=PRIMARY):
    body = envelope(output)
    body.update(modelVersion=model, usageMetadata={"promptTokenCount": 120, "candidatesTokenCount": 300, "totalTokenCount": 420})
    return ProviderResponse(body=body)


def test_primary_success_validates_once_and_records_safe_metadata(http, caplog):
    queue, calls = http; queue.append(success())
    adapter = router()
    with caplog.at_level(logging.INFO, logger="app.gemini"):
        result = validate_extraction(adapter.extract(sample_text()), sample_text(), provider=adapter, mode="configured")
    assert len(calls) == 1 and result.model == PRIMARY and result.live_ai
    assert result.model_attempts[0].model == PRIMARY and result.model_attempts[0].http_status == 200
    assert result.model_attempts[0].elapsed_ms >= 0 and result.token_usage["totalTokenCount"] == 420
    assert result.requires_human_review and any(issue.code == "HUMAN_APPROVAL_REQUIRED" for issue in result.review_issues)
    assert "dummy_router_key" not in caplog.text and "Outline" not in caplog.text


def test_provider_cannot_echo_the_key_through_reported_model_metadata(http, caplog):
    queue, _ = http; key = "dummy-router-key"
    queue.append(success(model="gemini-" + key))
    adapter = GeminiModelRouter(key, allow_live=True, models=DEFAULT_MODELS, free_tier_models=DEFAULT_MODELS)
    with caplog.at_level(logging.INFO, logger="app.gemini"):
        result = validate_extraction(adapter.extract(sample_text()), sample_text(), provider=adapter, mode="configured")
    assert key not in caplog.text and result.model == PRIMARY and result.model_attempts[0].response_model is None


def test_optional_third_model_requires_confirmation_and_stays_within_three_calls(http):
    queue, calls = http; third = "gemini-3.8-flash"
    queue.extend([ProviderResponse(503), ProviderResponse(503), success(model=third)])
    adapter = router(models=(*DEFAULT_MODELS, third), free_tier_models=(*DEFAULT_MODELS, third), max_model_calls=3)
    result = validate_extraction(adapter.extract(sample_text()), sample_text(), provider=adapter, mode="configured")
    assert result.model == third and len(calls) == 3 and len(result.model_attempts) == 3


@pytest.mark.parametrize("first", [503, 502, 504, 404])
def test_definite_model_availability_falls_back_to_next_once(http, first):
    queue, calls = http
    queue.extend([unavailable() if first == 404 else ProviderResponse(first), success(model=SECONDARY)])
    adapter = router()
    result = validate_extraction(adapter.extract(sample_text()), sample_text(), provider=adapter, mode="configured")
    assert [call[1] for call in calls] == [f"{API_BASE}/{model}:generateContent" for model in DEFAULT_MODELS]
    assert result.model == SECONDARY and len(result.model_attempts) == 2
    assert result.model_attempts[0].http_status == first and result.model_attempts[1].error_code is None


@pytest.mark.parametrize("status,body,code", [
    (401, {}, "AI_AUTH_FAILED"), (403, {}, "AI_AUTH_FAILED"),
    (429, {"error": {"status": "RESOURCE_EXHAUSTED"}}, "AI_QUOTA_EXCEEDED"),
    (400, {"error": {"status": "INVALID_ARGUMENT"}}, "AI_REQUEST_INVALID"),
    (400, {"error": {"details": [{"reason": "API_KEY_INVALID"}]}}, "AI_AUTH_FAILED"),
    (404, {"error": {"message": "Unknown API route", "status": "NOT_FOUND"}}, "AI_MODEL_UNAVAILABLE"),
    (500, {}, "AI_PROVIDER_FAILED"), (302, {}, "AI_PROVIDER_FAILED"),
])
def test_global_credential_quota_request_or_unknown_endpoint_errors_stop(http, status, body, code):
    queue, calls = http; queue.append(ProviderResponse(status, body))
    adapter = router()
    with pytest.raises(ExtractionError) as caught: adapter.extract(sample_text())
    assert caught.value.code == code and len(calls) == 1 and len(adapter.last_attempts) == 1


@pytest.mark.parametrize("status,rpc,message,code,diagnostic", [
    (400, "INVALID_ARGUMENT", "Unknown name responseJsonSchema", "AI_REQUEST_INVALID", "UNKNOWN_REQUEST_FIELD"),
    (400, "INVALID_ARGUMENT", "The response schema is too complex", "AI_REQUEST_INVALID", "RESPONSE_SCHEMA_REJECTED"),
    (503, "FAILED_PRECONDITION", "Billing is required", "AI_REQUEST_INVALID", "BILLING_OR_PROJECT_PRECONDITION"),
    (503, "RESOURCE_EXHAUSTED", "Project quota exhausted", "AI_QUOTA_EXCEEDED", "PROJECT_QUOTA_EXHAUSTED"),
    (400, "INVALID_ARGUMENT", "API key not valid", "AI_AUTH_FAILED", "INVALID_CREDENTIALS"),
])
def test_rpc_diagnostics_are_sanitized_and_global_errors_never_fall_back(http, caplog, status, rpc, message, code, diagnostic):
    queue, calls = http; queue.append(ProviderResponse(status, {"error": {"status": rpc, "message": message + " dummy_router_key"}}))
    adapter = router()
    with caplog.at_level(logging.INFO, logger="app.gemini"), pytest.raises(ExtractionError) as caught:
        adapter.extract(sample_text())
    assert caught.value.code == code and len(calls) == 1
    assert adapter.last_attempts[0]["provider_status"] == rpc and adapter.last_attempts[0]["diagnostic_code"] == diagnostic
    assert "dummy_router_key" not in caplog.text and "responseJsonSchema" not in caplog.text


@pytest.mark.parametrize("failure,code", [(requests.Timeout("secret"), "AI_TIMEOUT"), (requests.ConnectionError("secret"), "AI_PROVIDER_FAILED")])
def test_ambiguous_network_outcomes_do_not_fall_back(http, failure, code):
    queue, calls = http; queue.append(failure)
    with pytest.raises(ExtractionError) as caught: router().extract(sample_text())
    assert caught.value.code == code and len(calls) == 1 and "secret" not in str(caught.value)


def test_all_models_unavailable_stops_at_budget_and_never_repeats(http):
    queue, calls = http; queue.extend([ProviderResponse(503), unavailable(SECONDARY)])
    adapter = router(models=(PRIMARY, SECONDARY, PRIMARY))
    with pytest.raises(ExtractionError) as caught: adapter.extract(sample_text())
    assert caught.value.code == "AI_MODELS_UNAVAILABLE"
    assert len(calls) == 2 and len({call[1] for call in calls}) == 2


def test_total_call_cap_is_enforced_even_with_more_eligible_models(http):
    queue, calls = http; queue.append(ProviderResponse(503))
    with pytest.raises(ExtractionError): router(max_model_calls=1).extract(sample_text())
    assert len(calls) == 1


@pytest.mark.parametrize("output", [{}, {"milestones": "not an array"}])
def test_invalid_structured_output_stops_without_another_model(http, output):
    queue, calls = http; queue.append(ProviderResponse(body=envelope(parts=[{"text": json.dumps(output)}])))
    with pytest.raises(ExtractionError) as caught: router().extract(sample_text())
    assert caught.value.code == "AI_INVALID_OUTPUT" and len(calls) == 1


def test_missing_or_invented_non_null_evidence_is_not_success(http):
    queue, calls = http
    data = sample_output(); data["milestones"][0]["amount"]["source_excerpt"] = "Invented source text"
    queue.append(success(data))
    with pytest.raises(ExtractionError) as caught: router().extract(sample_text())
    assert caught.value.code == "AI_UNGROUNDED_OUTPUT" and len(calls) == 1


def test_scope_uses_same_router_and_preserves_review_and_attribution(http):
    from test_scope_changes import output
    queue, calls = http; queue.extend([ProviderResponse(503), success(output(), SECONDARY)])
    adapter = router(); source = PATH.read_text(encoding="utf-8"); changes = [SCENARIOS[0][0]]
    result = validate_comparison(adapter.compare_scope(source, changes), source, changes, provider=adapter, mode="configured")
    assert result.model == SECONDARY and result.requires_human_review and len(result.model_attempts) == 2
    payload = calls[1][2]["json"]
    assert "responseJsonSchema" not in payload["generationConfig"]
    assert '"findings"' in payload["systemInstruction"]["parts"][0]["text"]


def test_scope_missing_evidence_is_rejected_without_fallback(http):
    from test_scope_changes import output
    queue, calls = http; data = output(); data["findings"][0]["contract_evidence"] = []
    queue.append(success(data))
    with pytest.raises(ExtractionError) as caught: router().compare_scope(PATH.read_text(encoding="utf-8"), [SCENARIOS[0][0]])
    assert caught.value.code == "AI_UNGROUNDED_OUTPUT" and len(calls) == 1


def test_unconfirmed_models_are_excluded_and_optional_model_is_not_automatic(http):
    queue, calls = http; queue.append(success(model=SECONDARY))
    adapter = router(models=(PRIMARY, SECONDARY, "gemini-3.8-flash"), free_tier_models=(SECONDARY,))
    adapter.extract(sample_text())
    assert adapter.models == (SECONDARY,) and len(calls) == 1 and SECONDARY in calls[0][1]
    with pytest.raises(ExtractionError) as caught: router(free_tier_models=()).extract(sample_text())
    assert caught.value.code == "AI_MODEL_ACCESS_UNCONFIRMED" and len(calls) == 1


@pytest.mark.parametrize("changes", [{"models": ("gemini-unverified-model",)}, {"models": ()}, {"max_model_calls": 4}, {"max_model_calls": True}, {"timeout_seconds": 100}])
def test_invalid_configuration_never_reaches_transport(http, changes):
    with pytest.raises(ExtractionError): router(**changes)
    assert not http[1]


def test_factory_priority_free_confirmation_and_live_gate(monkeypatch, http):
    monkeypatch.setenv("SCOPESHIELD_AI_PROVIDER", "gemini"); monkeypatch.setenv("GEMINI_API_KEY", "dummy_key")
    monkeypatch.setenv("SCOPESHIELD_AI_MODELS", ",".join(DEFAULT_MODELS))
    monkeypatch.setenv("SCOPESHIELD_AI_FREE_TIER_MODELS", SECONDARY)
    monkeypatch.setenv("SCOPESHIELD_AI_ALLOW_LIVE", "false")
    adapter = configured_provider()
    assert adapter.priority == DEFAULT_MODELS and adapter.models == (SECONDARY,)
    with pytest.raises(ExtractionError) as caught: adapter.extract(sample_text())
    assert caught.value.code == "AI_LIVE_REQUESTS_DISABLED" and not http[1]


def test_requests_default_transport_has_no_automatic_retries():
    with requests.Session() as session:
        assert session.get_adapter(API_BASE).max_retries.total == 0


def test_extraction_persists_fallback_model_attempts_and_leaves_all_rows_unapproved(api, http):
    from app.extraction import get_extraction_provider
    from app.storage import Contract, InvoiceLink
    from sqlalchemy import func, select
    from sqlalchemy.orm import Session
    app, client, _ = api; queue, calls = http
    adapter = router(); app.dependency_overrides[get_extraction_provider] = lambda: adapter
    queue.extend([ProviderResponse(503), success(model=SECONDARY)])
    uploaded = client.post("/api/contracts", files={"file": ("synthetic.txt", sample_text().encode(), "text/plain")}).json()
    result = client.post(f"/api/contracts/{uploaded['id']}/extract", json={"mode": "configured"})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["extraction"]["model"] == SECONDARY and len(body["extraction"]["model_attempts"]) == 2
    assert all(row["status"] == "REVIEW_REQUIRED" for row in body["milestones"])
    with Session(app.state.engine) as session:
        assert session.get(Contract, uploaded["id"]).extraction["model"] == SECONDARY
        assert session.scalar(select(func.count()).select_from(InvoiceLink)) == 0
    assert len(calls) == 2


def test_scope_persists_same_router_attribution_without_approval(api, http, monkeypatch):
    from test_scope_changes import analyze, configured, create, output
    _, client, contract = api; queue, _ = http
    queue.extend([ProviderResponse(503), success(output(), SECONDARY)])
    configured(monkeypatch, router())
    row = create(client, contract); result = analyze(client, row, "configured")
    assert result.status_code == 200, result.text
    saved = client.get(f"/api/change-requests/{row['id']}").json()
    assert saved["status"] == "REVIEW_REQUIRED" and saved["analysis"]["requires_human_review"]
    assert saved["analysis"]["model"] == SECONDARY and len(saved["analysis"]["model_attempts"]) == 2


def test_router_deadline_prevents_another_model_request(http, monkeypatch):
    queue, calls = http; queue.append(ProviderResponse(503))
    moments = iter([0, 0, 0, 0, 0, 91])
    monkeypatch.setattr("app.gemini.time.monotonic", lambda: next(moments, 91))
    with pytest.raises(ExtractionError) as caught: router().extract(sample_text())
    assert caught.value.code == "AI_TIMEOUT" and len(calls) == 1
