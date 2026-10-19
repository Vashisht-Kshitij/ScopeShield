"""Offline tests of the live runner's global budget, preflight and result oracle."""
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from app.gemini import API_BASE, DEFAULT_MODELS
from scripts.verify_gemini_fallback import InferenceBudget, preflight, SOURCE, verify_result


def test_live_budget_forwards_once_per_model_in_order_and_writes_before_network(tmp_path):
    path = tmp_path / "journal.json"; calls = []
    def actual(session, method, url, **kwargs):
        assert len(json.loads(path.read_text(encoding="utf-8"))["attempts"]) == len(calls) + 1
        calls.append(url); return SimpleNamespace(status_code=503)
    budget = InferenceBudget(DEFAULT_MODELS, 2, path, actual)
    for model in DEFAULT_MODELS: budget.request(None, "POST", f"{API_BASE}/{model}:generateContent")
    for method, url in [("POST", f"{API_BASE}/{DEFAULT_MODELS[0]}:generateContent"),
                        ("GET", API_BASE), ("POST", "https://api-m.sandbox.paypal.com/v2/invoicing/invoices")]:
        with pytest.raises(AssertionError): budget.request(None, method, url)
    assert len(calls) == 2 and budget.blocked_requests == 3
    assert all(row["http_status"] == 503 for row in budget.attempts)


def test_secondary_cannot_be_attempted_before_primary(tmp_path):
    budget = InferenceBudget(DEFAULT_MODELS, 2, tmp_path / "journal.json", lambda *a, **k: pytest.fail("Wrong order reached network"))
    with pytest.raises(AssertionError): budget.request(None, "POST", f"{API_BASE}/{DEFAULT_MODELS[1]}:generateContent")
    assert budget.attempts == []


def metadata():
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "api_base": API_BASE,
        "list_http_status": 200, "list_complete": True, "generate_content_models": ["models/" + model for model in DEFAULT_MODELS],
        "models": {model: {"http_status": 200, "methods": ["generateContent"]} for model in DEFAULT_MODELS}}


@pytest.mark.parametrize("change", ["stale", "wrong_api", "missing_method", "missing_list", "budget"])
def test_preflight_rejects_unverified_models_endpoint_or_budget(change):
    data = metadata(); cap = 2
    if change == "stale": data["checked_at"] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    if change == "wrong_api": data["api_base"] = "https://wrong.example.com"
    if change == "missing_method": data["models"][DEFAULT_MODELS[0]]["methods"] = []
    if change == "missing_list": data["generate_content_models"] = []
    if change == "budget": cap = 3
    with pytest.raises(ValueError): preflight(DEFAULT_MODELS, cap, data)


def test_preflight_accepts_only_compatible_fresh_metadata():
    preflight(DEFAULT_MODELS, 2, metadata())


def synthetic_response():
    source = SOURCE.read_text(encoding="utf-8")
    def fact(value, line):
        start = source.index(line)
        return dict(value=value, source_excerpt=line, source_start=start, source_end=start+len(line))
    rows = []
    for index, amount in enumerate(["60.00", "90.00", "150.00"]):
        rows.append({"amount": fact(amount, f"Amount: USD {amount}."), "currency": fact("USD", "Currency: USD"), "deadline": {"value": None}})
    return {"text": source, "status": "REVIEW_REQUIRED", "milestones": [{"status": "REVIEW_REQUIRED"} for _ in rows],
        "extraction": {"provider": "google_gemini", "live_ai": True, "mode": "configured", "requires_human_review": True,
            "data": {"currency": fact("USD", "Currency: USD"), "total_value": fact("300.00", "Total project value: USD 300.00."), "milestones": rows},
            "totals_match": True, "review_issues": [{"field": "milestones[2].payment_conditions", "code": "PROVIDER_REQUESTED_REVIEW"}]}}


def test_verification_oracle_requires_exact_amounts_source_ambiguity_and_unapproved_status():
    body = synthetic_response(); assert verify_result(body)["all_checks_pass"]
    body["extraction"]["data"]["milestones"][2]["amount"]["value"] = "151.00"
    body["extraction"]["review_issues"] = []
    body["milestones"][0]["status"] = "APPROVED"
    checks = verify_result(body)["checks"]
    assert not checks["three_exact_milestones"] and not checks["ambiguous_condition_flagged"] and not checks["human_approval_required"]
