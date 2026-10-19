"""Scope comparison uses the existing bounded Gemini transport; all HTTP is fake."""
import json

import pytest

from app.day6_fixture import PATH, SCENARIOS
from app.gemini import GeminiProvider
from app.scope_comparison import validate_comparison
from test_gemini import ProviderResponse, envelope, http  # reusable mocked HTTP queue
from test_scope_changes import analyze, api, configured, create, output


def test_scope_wire_schema_prompts_and_transport_are_shared(http):
    queue, calls = http
    data = output()
    queue.append(ProviderResponse(body=envelope(data)))
    adapter = GeminiProvider("dummy_scope_key", allow_live=True, max_attempts=1)
    source, changes = PATH.read_text(encoding="utf-8"), [SCENARIOS[0][0]]
    result = validate_comparison(adapter.compare_scope(source, changes), source, changes,
                                 provider=adapter, mode="configured")
    assert result.findings[0].classification == "IN_SCOPE"
    assert len(calls) == 1
    _, url, options = calls[0]
    payload = options["json"]
    assert "dummy_scope_key" not in url and "dummy_scope_key" not in json.dumps(payload)
    assert "tools" not in payload and options["allow_redirects"] is False
    schema = json.loads(payload["systemInstruction"]["parts"][0]["text"].split("matching this schema: ")[1])
    assert "findings" in schema["properties"] and "milestones" not in schema["properties"]
    assert not {"message_evidence", "change_types", "conversation_effect"} & set(schema["$defs"]["ScopeFinding"]["properties"])
    assert set(payload["generationConfig"]) == {"responseMimeType", "maxOutputTokens"}
    assert source in json.loads(payload["contents"][0]["parts"][0]["text"])["untrusted_original_contract"]
    assert "never instructions" in payload["systemInstruction"]["parts"][0]["text"]


@pytest.mark.parametrize("status,code,local_status", [(503, "AI_PROVIDER_TEMPORARY", 502), (429, "AI_QUOTA_EXCEEDED", 429)])
def test_gemini_unavailable_or_quota_failure_preserves_pending_request(api, http, monkeypatch, status, code, local_status):
    _, client, contract = api
    queue, calls = http
    queue.append(ProviderResponse(status, {"error": {"message": "dummy_secret"}}))
    configured(monkeypatch, GeminiProvider("dummy_scope_key", allow_live=True, max_attempts=1))
    row = create(client, contract)
    result = analyze(client, row, "configured")
    assert result.status_code == local_status and result.json()["detail"]["code"] == code
    assert "dummy_secret" not in result.text and len(calls) == 1
    saved = client.get(f"/api/change-requests/{row['id']}").json()
    assert saved["status"] == "PENDING" and saved["analysis"] is None and saved["error_code"] == code


def test_scope_live_gate_blocks_transport_even_with_key(api, http, monkeypatch):
    _, client, contract = api
    _, calls = http
    configured(monkeypatch, GeminiProvider("dummy_scope_key", allow_live=False))
    result = analyze(client, create(client, contract), "configured")
    assert result.status_code == 503 and result.json()["detail"]["code"] == "AI_LIVE_REQUESTS_DISABLED"
    assert calls == []
