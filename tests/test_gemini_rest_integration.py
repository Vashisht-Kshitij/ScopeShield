"""Verified JSON-mode REST shape and multiline grounding; all HTTP is mocked."""
import json
from pathlib import Path

import pytest

from app.evidence import source_match
from app.extraction import ExtractionError, validate_extraction, get_extraction_provider
from app.gemini import DEFAULT_MODEL, GeminiModelRouter
from app.scope_comparison import validate_comparison
from test_gemini import ProviderResponse, envelope, http
from test_scope_changes import api

SOURCE = Path(__file__).resolve().parents[1] / "examples/gemini_verified_contract.txt"


def reference_output():
    """Synthetic structured test data, NOT the user's/live Gemini response."""
    def fact(value, quote): return {"value": value, "source_excerpt": quote}
    quotes = ["Milestone 1: USD 300 is payable upon approval of homepage designs.",
              "Milestone 2: USD 700 is payable when the responsive frontend is completed.",
              "Milestone 3: USD 500 is payable upon final delivery and client acceptance."]
    conditions = ["payable upon approval of homepage designs", "payable when the responsive frontend is completed",
                  "payable upon final delivery and client acceptance"]
    data = {
        "project_title": fact("Website Development Agreement", "Website Development Agreement"),
        "currency": fact("USD", "for a total price of USD 1500."),
        "total_value": fact("1500.00", "for a total price of USD 1500."),
        "deliverables": [fact("build a six-page website", "The freelancer will build a six-page website for a total price of USD 1500.")],
        "milestones": [], "review_required_fields": ["deliverables"],
    }
    for index, (amount, quote, condition) in enumerate(zip(["300.00", "700.00", "500.00"], quotes, conditions), 1):
        data["milestones"].append({"name": fact(f"Milestone {index}", quote), "amount": fact(amount, quote),
            "currency": fact("USD", quote), "payment_conditions": fact(condition, quote),
            "deadline": {"value": None}, "deliverables": [], "acceptance_criteria": []})
    return data


@pytest.mark.parametrize("source,quote", [
    ("USD 300 is payable upon approval\nof homepage designs.", "USD 300 is payable upon approval of homepage designs."),
    ("USD 300 is payable upon approval\r\n\tof homepage designs.", "USD 300 is payable upon approval of homepage designs."),
    ("USD 300 is payable upon approval\u00a0of homepage designs.", "USD 300 is payable upon\napproval of homepage designs."),
])
def test_whitespace_only_match_retains_exact_original_slice(source, quote):
    match = source_match(source, quote)
    assert match and source[match.start():match.end()] == source


@pytest.mark.parametrize("quote", [
    "USD 900 is payable upon approval of homepage designs.",
    "USD 300 is not payable upon approval of homepage designs.",
    "USD 300 is payable ... of homepage designs.",
    "USD 300 is payable of homepage designs.",
    "usd 300 is payable upon approval of homepage designs.",
    "USD 300 is payable upon approval of homepage designs!",
])
def test_normalization_does_not_accept_changed_numbers_words_case_or_punctuation(quote):
    assert source_match("USD 300 is payable upon approval\nof homepage designs.", quote) is None


def test_matching_cannot_skip_intervening_contract_words():
    assert source_match("The price is USD 300. An additional condition applies. Payment is due.", "The price is USD 300. Payment is due.") is None


@pytest.mark.parametrize("source,quote", [
    ("Milestone 1: USD 300 is payable.", "Milestone 1: USD 30"),
    ("The price is USDT 300.", "The price is USD"),
    ("A nonrefundable deposit.", "refundable deposit."),
    ("The price is USD 300.50.", "The price is USD 300"),
    ("The price is USD 300.50.", "The price is USD 300."),
    ("The price is USD 300.50.", "50"),
    ("The price is USD 300,000.", "The price is USD 300"),
])
def test_quotes_cannot_cut_word_or_number_boundaries(source, quote):
    assert source_match(source, quote) is None


def test_json_mode_pipeline_persists_three_review_required_rows_and_canonical_evidence(api, http):
    app, client, _ = api; queue, calls = http
    source = SOURCE.read_text(encoding="utf-8")
    data = reference_output(); data["milestones"][0]["amount"].update(source_start=9999, source_end=99999)
    body = envelope(data); body["modelVersion"] = DEFAULT_MODEL
    queue.append(ProviderResponse(body=body))
    adapter = GeminiModelRouter("dummy_key", models=(DEFAULT_MODEL,), free_tier_models=(DEFAULT_MODEL,), allow_live=True, max_model_calls=1)
    app.dependency_overrides[get_extraction_provider] = lambda: adapter
    upload = client.post("/api/contracts", files={"file": (SOURCE.name, source.encode(), "text/plain")}).json()
    result = client.post(f"/api/contracts/{upload['id']}/extract", json={"mode": "configured"})
    assert result.status_code == 200, result.text
    saved = client.get(f"/api/contracts/{upload['id']}").json()
    assert saved["status"] == "REVIEW_REQUIRED" and all(row["status"] == "REVIEW_REQUIRED" for row in saved["milestones"])
    extraction = saved["extraction"]
    assert extraction["model"] == "gemini-3.5-flash-lite" and extraction["totals_match"] is True
    assert [row["amount"] for row in saved["milestones"]] == ["300.00", "700.00", "500.00"]
    for row in extraction["data"]["milestones"]:
        evidence = row["amount"]
        assert "\n" in evidence["source_excerpt"]
        assert saved["text"][evidence["source_start"]:evidence["source_end"]] == evidence["source_excerpt"]
        assert row["deadline"]["value"] is None
    assert len(calls) == 1 and set(calls[0][2]["json"]["generationConfig"]) == {"responseMimeType", "maxOutputTokens"}


@pytest.mark.parametrize("amount,quote", [
    ("300.00", "Milestone 1: USD 300 is payable immediately without approval."),
    ("30.00", "Milestone 1: USD 30"),
])
def test_fabricated_multiline_evidence_stops_without_fallback(http, amount, quote):
    queue, calls = http; data = reference_output()
    data["milestones"][0]["amount"].update(value=amount, source_excerpt=quote)
    queue.append(ProviderResponse(body=envelope(data)))
    adapter = GeminiModelRouter("dummy_key", free_tier_models=(DEFAULT_MODEL,), allow_live=True)
    with pytest.raises(ExtractionError) as caught: adapter.extract(SOURCE.read_text(encoding="utf-8"))
    assert caught.value.code == "AI_UNGROUNDED_OUTPUT" and len(calls) == 1


@pytest.mark.parametrize("raw", ['{"milestones":[],"milestones":[]}', '{"total_value":NaN}', '{"total_value":Infinity}'])
def test_duplicate_or_nonfinite_json_is_rejected_before_pydantic(raw):
    with pytest.raises(ExtractionError) as caught:
        validate_extraction(raw, SOURCE.read_text(encoding="utf-8"), provider=type("P", (), {"name":"test", "live_ai":False})(), mode="configured")
    assert caught.value.code == "AI_INVALID_OUTPUT"


def test_simple_manual_response_is_not_silently_treated_as_full_application_schema(http):
    queue, calls = http
    queue.append(ProviderResponse(body=envelope({"currency":"USD", "total_amount":"1500", "milestones":[]})))
    adapter = GeminiModelRouter("dummy_key", free_tier_models=(DEFAULT_MODEL,), allow_live=True)
    with pytest.raises(ExtractionError) as caught: adapter.extract(SOURCE.read_text(encoding="utf-8"))
    assert caught.value.code == "AI_INVALID_OUTPUT" and len(calls) == 1


def test_scope_canonicalizes_multiline_evidence_without_rewriting_source():
    source = "Interactive booking forms\nand reminder emails are excluded."
    work = "Add a booking form."
    data = {"findings":[{"ordinal":1,"requested_work":work,"classification":"OUT_OF_SCOPE",
        "explanation":"The source explicitly excludes this requested work.",
        "contract_evidence":[{"source_excerpt":"Interactive booking forms and reminder emails are excluded.","relationship":"EXCLUSION"}],
        "uncertainties":[],"recommended_action":"DRAFT_CHANGE_ORDER","requires_human_review":True}]}
    result = validate_comparison(json.dumps(data), source, [work], provider=type("P", (), {"name":"test", "live_ai":False})(), mode="configured")
    evidence = result.findings[0].contract_evidence[0]
    assert evidence.source_excerpt == source and evidence.source_start == 0 and evidence.source_end == len(source)
    assert result.requires_human_review
