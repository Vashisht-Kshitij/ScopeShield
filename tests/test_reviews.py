"""Persistent human decisions, optimistic locking, audit history, and safety."""
import copy
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.day4_fixture import PATH
from app.main import create_app, get_paypal
from app.storage import Contract, Milestone, ReviewEvent


@pytest.fixture
def api(tmp_path):
    application = create_app(storage_url="sqlite:///" + (tmp_path / "reviews.db").as_posix())
    def forbidden_paypal():
        raise AssertionError("Human review must never call PayPal")
    application.dependency_overrides[get_paypal] = forbidden_paypal
    with TestClient(application, base_url="http://127.0.0.1:8000") as client:
        uploaded = client.post("/api/contracts", files={"file": (PATH.name, PATH.read_bytes(), "text/plain")})
        assert uploaded.status_code == 201
        contract_id = uploaded.json()["id"]
        extracted = client.post(f"/api/contracts/{contract_id}/extract", json={"mode": "offline_demo"})
        assert extracted.status_code == 200
        yield application, client, contract_id


def get_rows(client, contract_id):
    result = client.get(f"/api/contracts/{contract_id}/milestones")
    assert result.status_code == 200
    return result.json()


def context(row):
    return {"expected_revision": row["revision"], "expected_contract_revision": row["contract_review_revision"],
            "reviewer_label": "Synthetic local reviewer", "note": "Reviewed the synthetic contract and source excerpts"}


def decide(client, row, action, **overrides):
    body = {**context(row), "confirmed": True, "acknowledge_review_flags": True, **overrides}
    return client.post(f"/api/milestones/{row['id']}/{action}", json=body)


def test_retrieve_grounded_milestones_with_ambiguity_and_original_output(api):
    _, client, contract_id = api
    result = get_rows(client, contract_id)
    assert result["summary"]["declared_total"] == "2400.00"
    assert result["summary"]["milestone_total"] == "2400.00"
    assert result["summary"]["totals_match"] is True
    assert all(row["revision"] == 1 and row["status"] == "REVIEW_REQUIRED" for row in result["milestones"])
    saved = client.get(f"/api/contracts/{contract_id}").json()
    assert saved["extraction"]["live_ai"] is False
    assert saved["original_output_kind"] == "provider_raw"
    assert saved["original_ai_output"]["milestones"][2]["deadline"]["value"] is None
    assert any(issue["field"] == "milestones[2].payment_conditions" and issue["code"] == "PROVIDER_REQUESTED_REVIEW" for issue in saved["extraction"]["review_issues"])


def test_edit_keeps_ai_evidence_immutable_and_records_money_discrepancy(api):
    application, client, contract_id = api
    original = client.get(f"/api/contracts/{contract_id}").json()
    row = get_rows(client, contract_id)["milestones"][0]
    result = client.patch(f"/api/milestones/{row['id']}", json={**context(row), "changes": {"amount": "650.00", "description": "Planning plus a human-reviewed styling checklist"}})
    assert result.status_code == 200
    updated = result.json()
    assert updated["amount"] == "650.00" and updated["revision"] == 2
    assert updated["contract_review_revision"] == 1 and updated["has_human_edits"] is True
    assert updated["evidence"]["amount"]["value"] == "600.00"
    after = client.get(f"/api/contracts/{contract_id}").json()
    assert after["original_ai_output"] == original["original_ai_output"]
    assert after["extraction"] == original["extraction"]
    totals = get_rows(client, contract_id)["summary"]
    assert totals["milestone_total"] == "2450.00" and totals["declared_total"] == "2400.00"
    assert totals["totals_match"] is False and "MILESTONE_TOTAL_MISMATCH" in totals["discrepancies"]
    history = client.get(f"/api/milestones/{row['id']}/history").json()
    assert len(history) == 1 and history[0]["action"] == "EDITED"
    assert history[0]["before"]["amount"] == "600.00" and history[0]["after"]["amount"] == "650.00"
    with Session(application.state.engine) as session:
        assert session.get(Milestone, row["id"]).amount == Decimal("650.00")


def test_two_approvals_and_one_rejection_are_audited_without_paypal(api):
    _, client, contract_id = api
    for index, action in [(0, "approve"), (1, "approve"), (2, "reject")]:
        row = get_rows(client, contract_id)["milestones"][index]
        result = decide(client, row, action)
        assert result.status_code == 200
        assert result.json()["status"] == ("APPROVED" if action == "approve" else "REJECTED")
        assert result.json()["reviewed_at"] and result.json()["reviewer_label"]
    result = get_rows(client, contract_id)
    assert [row["status"] for row in result["milestones"]] == ["APPROVED", "APPROVED", "REJECTED"]
    assert result["summary"]["approved_total"] == "1500.00"
    assert result["summary"]["active_totals_match"] is False
    assert "REJECTED_MILESTONES_PRESENT" in result["summary"]["discrepancies"]
    history = client.get(f"/api/contracts/{contract_id}/approval-history").json()
    assert [event["action"] for event in history] == ["APPROVED", "APPROVED", "REJECTED"]
    assert [event["contract_review_revision"] for event in history] == [1, 2, 3]


def test_review_flags_require_explicit_acknowledgement(api):
    _, client, contract_id = api
    row = get_rows(client, contract_id)["milestones"][0]
    result = decide(client, row, "approve", acknowledge_review_flags=False)
    assert result.status_code == 409
    assert any("OFFLINE_FIXTURE" in flag for flag in result.json()["detail"]["review_flags"])
    assert client.get(f"/api/milestones/{row['id']}/history").json() == []


def test_stale_milestone_and_contract_updates_do_not_add_audit_events(api):
    application, client, contract_id = api
    before = get_rows(client, contract_id)["milestones"]
    assert client.patch(f"/api/milestones/{before[0]['id']}", json={**context(before[0]), "changes": {"name": "Corrected planning name"}}).status_code == 200
    assert decide(client, before[0], "approve").status_code == 409
    # Even a different milestone needs the current totals/review context.
    assert decide(client, before[1], "approve").status_code == 409
    with Session(application.state.engine) as session:
        assert session.scalar(select(func.count()).select_from(ReviewEvent)) == 1


@pytest.mark.parametrize("target", ["APPROVED", "REJECTED"])
def test_decisions_are_terminal_until_explicitly_reopened(api, target):
    _, client, contract_id = api
    row = get_rows(client, contract_id)["milestones"][0]
    decided = decide(client, row, "approve" if target == "APPROVED" else "reject").json()
    assert decided["status"] == target
    assert decide(client, decided, "approve").status_code == 409
    assert decide(client, decided, "reject").status_code == 409
    assert client.patch(f"/api/milestones/{row['id']}", json={**context(decided), "changes": {"amount": "610.00"}}).status_code == 409
    reopened = decide(client, decided, "reopen")
    assert reopened.status_code == 200 and reopened.json()["status"] == "REVIEW_REQUIRED"
    assert reopened.json()["reviewed_at"] is None
    assert decide(client, reopened.json(), "reopen").status_code == 409
    history = client.get(f"/api/milestones/{row['id']}/history").json()
    assert [event["action"] for event in history] == [target, "REOPENED"]


@pytest.mark.parametrize("changes", [{}, {"amount": "0"}, {"amount": "-1"}, {"amount": "1.999"}, {"amount": 1.1}, {"amount": True}, {"currency": "XYZ"}, {"currency": "usd"}, {"status": "APPROVED"}, {"evidence": {}}, {"invoice_id": "INV2-NOT-ALLOWED"}], ids=["empty", "zero", "negative", "precision", "float", "bool", "currency_unknown", "currency_case", "status", "evidence", "invoice"])
def test_invalid_edits_are_rejected_without_changes(api, changes):
    _, client, contract_id = api
    row = get_rows(client, contract_id)["milestones"][0]
    result = client.patch(f"/api/milestones/{row['id']}", json={**context(row), "changes": changes})
    assert result.status_code == 422
    assert client.get(f"/api/milestones/{row['id']}/history").json() == []
    assert client.get(f"/api/milestones/{row['id']}").json()["revision"] == 1


@pytest.mark.parametrize("confirmation", [False, 1, "true", None])
def test_approval_requires_an_actual_explicit_boolean(api, confirmation):
    _, client, contract_id = api
    row = get_rows(client, contract_id)["milestones"][0]
    assert decide(client, row, "approve", confirmed=confirmation).status_code == 422


def test_empty_reason_and_boolean_revision_are_invalid(api):
    _, client, contract_id = api
    row = get_rows(client, contract_id)["milestones"][0]
    assert decide(client, row, "reject", note=" ").status_code == 422
    assert decide(client, row, "reject", expected_revision=True).status_code == 422


def test_missing_terms_cannot_be_approved_but_can_be_corrected(api):
    _, client, contract_id = api
    row = get_rows(client, contract_id)["milestones"][0]
    cleared = client.patch(f"/api/milestones/{row['id']}", json={**context(row), "changes": {"payment_conditions": None, "acceptance_criteria": []}}).json()
    assert decide(client, cleared, "approve").status_code == 422
    assert decide(client, cleared, "reject").status_code == 200


def test_optional_deadline_can_be_cleared_without_invention(api):
    _, client, contract_id = api
    row = get_rows(client, contract_id)["milestones"][0]
    changed = client.patch(f"/api/milestones/{row['id']}", json={**context(row), "changes": {"deadline": None}})
    assert changed.status_code == 200 and changed.json()["deadline"] is None
    assert changed.json()["evidence"]["deadline"]["value"] == "2026-11-01"


def test_jpy_fractional_currency_values_are_rejected(api):
    _, client, contract_id = api
    row = get_rows(client, contract_id)["milestones"][0]
    assert client.patch(f"/api/milestones/{row['id']}", json={**context(row), "changes": {"currency": "JPY", "amount": "1.50"}}).status_code == 422


def test_noop_edit_does_not_create_a_revision(api):
    _, client, contract_id = api
    row = get_rows(client, contract_id)["milestones"][0]
    assert client.patch(f"/api/milestones/{row['id']}", json={**context(row), "changes": {"amount": "600.00"}}).status_code == 422


def test_human_review_blocks_forced_extraction_and_keeps_ids_and_history(api):
    _, client, contract_id = api
    row = get_rows(client, contract_id)["milestones"][0]
    assert decide(client, row, "approve").status_code == 200
    result = client.post(f"/api/contracts/{contract_id}/extract", json={"mode": "offline_demo", "force": True})
    assert result.status_code == 409
    assert get_rows(client, contract_id)["milestones"][0]["id"] == row["id"]
    assert len(client.get(f"/api/milestones/{row['id']}/history").json()) == 1
    # Cached extraction still returns the current review state.
    cached = client.post(f"/api/contracts/{contract_id}/extract", json={"mode": "offline_demo"})
    assert cached.status_code == 200 and cached.json()["milestones"][0]["status"] == "APPROVED"


def test_processing_locks_review_and_unknown_records_are_clear(api):
    application, client, contract_id = api
    row = get_rows(client, contract_id)["milestones"][0]
    with Session(application.state.engine) as session:
        session.get(Contract, contract_id).status = "PROCESSING"
        session.commit()
    assert decide(client, row, "approve").status_code == 409
    assert client.get(f"/api/milestones/{uuid4()}").status_code == 404
    assert client.get("/api/milestones/not-a-uuid").status_code == 422
    assert client.get(f"/api/contracts/{uuid4()}/approval-history").status_code == 404


def test_foreign_origin_cannot_approve(api):
    _, client, contract_id = api
    row = get_rows(client, contract_id)["milestones"][0]
    result = client.post(f"/api/milestones/{row['id']}/approve", headers={"Origin": "https://foreign.example.com"}, json={**context(row), "confirmed": True})
    assert result.status_code == 403


def test_approval_history_and_edits_survive_application_restart(api, tmp_path):
    application, client, contract_id = api
    row = get_rows(client, contract_id)["milestones"][0]
    edited = client.patch(f"/api/milestones/{row['id']}", json={**context(row), "changes": {"amount": "650.00"}}).json()
    approved = decide(client, edited, "approve").json()
    history = client.get(f"/api/contracts/{contract_id}/approval-history").json()
    restarted_app = create_app(storage_url=str(application.state.engine.url))
    with TestClient(restarted_app, base_url="http://127.0.0.1:8000") as restarted:
        assert restarted.get(f"/api/milestones/{row['id']}").json() == approved
        assert restarted.get(f"/api/contracts/{contract_id}/approval-history").json() == history
