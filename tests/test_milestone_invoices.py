"""Financial workflow tests: providers are mocked and network remains blocked."""
import copy
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.database import migration_config
from app.main import create_app, get_paypal
from app.paypal import PayPalAPIError
from app.storage import Contract, InvoiceEvent, InvoiceLink, Milestone, utc_now

PATH = Path(__file__).resolve().parents[1] / "examples" / "day5_sandbox_agreement.txt"
INVOICE = "INV2-DAY5-TEST-1234-ABCD"


class FakePayPal:
    recipient_email = "buyer@personal.example.com"
    def __init__(self):
        self.creates = []
        self.reads = []
        self.searches = []
        self.status = "DRAFT"
        self.create_error = None
        self.read_error = None
        self.search_missing = False
        self.after_create = None
    def create_invoice(self, **kwargs):
        self.creates.append(kwargs)
        if self.after_create:
            self.after_create()
        if self.create_error:
            raise self.create_error
        return INVOICE
    def verify_milestone_invoice(self, invoice_id, **expected):
        self.reads.append((invoice_id, expected))
        if self.read_error:
            raise self.read_error
        return {"invoice_id": invoice_id, "status": self.status, "currency_code": "USD",
                "total_amount": "1.00", "recipient_view_url": None}
    def find_milestone_invoice(self, **expected):
        self.searches.append(expected)
        if self.search_missing:
            return None
        return self.verify_milestone_invoice(INVOICE, **expected)


@pytest.fixture
def api(tmp_path):
    url = "sqlite:///" + (tmp_path / "day5.db").as_posix()
    app = create_app(storage_url=url)
    paypal = FakePayPal()
    app.dependency_overrides[get_paypal] = lambda: paypal
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        uploaded = client.post("/api/contracts", files={"file": (PATH.name, PATH.read_bytes(), "text/plain")})
        assert uploaded.status_code == 201
        contract_id = uploaded.json()["id"]
        extraction = client.post(f"/api/contracts/{contract_id}/extract", json={"mode": "offline_demo"})
        assert extraction.status_code == 200
        yield app, client, contract_id, paypal


def rows(client, cid):
    return client.get(f"/api/contracts/{cid}/milestones").json()["milestones"]


def context(row):
    return {"expected_revision": row["revision"], "expected_contract_revision": row["contract_review_revision"],
            "reviewer_label": "Day 5 test operator", "note": "Explicit synthetic sandbox test action"}


def approve(client, cid, index=0):
    row = rows(client, cid)[index]
    result = client.post(f"/api/milestones/{row['id']}/approve", json={**context(row), "confirmed": True, "acknowledge_review_flags": True})
    assert result.status_code == 200
    return result.json()


def creation(client, row):
    view = client.get(f"/api/milestones/{row['id']}/invoice/preview")
    assert view.status_code == 200, view.json()
    row = client.get(f"/api/milestones/{row['id']}").json()
    return {**context(row), "contract_id": row["contract_id"], "idempotency_key": str(uuid4()),
            "preview_digest": view.json()["preview_digest"], "confirmed": True,
            "acknowledge_discrepancies": True, "discrepancy_note": "Partial billing: only the USD 1 sketch is approved and selected; other milestones remain under review"}


def action(link):
    return {"expected_link_revision": link["revision"], "reviewer_label": "Day 5 test operator", "note": "Read-only provider reconciliation or status verification"}


def test_real_fixture_is_three_grounded_one_dollar_candidates(api):
    _, client, cid, paypal = api
    saved = client.get(f"/api/contracts/{cid}").json()
    assert saved["extraction"]["live_ai"] is False
    assert saved["extraction"]["totals_match"] is True
    assert len(saved["milestones"]) == 3
    assert all(row["amount"] == "1.00" for row in saved["milestones"])
    assert saved["milestones"][2]["deadline"] is None
    assert not paypal.creates


def test_explicit_approved_conversion_stores_exact_snapshot_status_and_audit(api):
    app, client, cid, paypal = api
    row = approve(client, cid)
    body = creation(client, row)
    result = client.post(f"/api/milestones/{row['id']}/invoice", json=body)
    assert result.status_code == 201, result.json()
    link = result.json()
    assert link["invoice_id"] == INVOICE and link["paypal_status"] == "DRAFT"
    assert link["verified"] is True and link["state"] == "CREATED"
    assert link["amount"] == "1.00" and link["milestone_revision"] == row["revision"]
    assert link["snapshot"]["evidence"] == row["evidence"]
    assert link["snapshot"]["discrepancy_note"] == body["discrepancy_note"]
    assert len(paypal.creates) == 1
    sent = paypal.creates[0]
    assert sent["amount_usd"] == Decimal("1.00")
    assert sent["milestone"] == row["name"] and sent["description"] == row["payment_conditions"]
    assert sent["request_id"] == link["id"] and sent["invoice_number"] == link["invoice_number"]
    assert client.get(f"/api/milestones/{row['id']}/invoice").json() == link
    history = client.get(f"/api/milestones/{row['id']}/invoice/history").json()
    assert [event["action"] for event in history] == ["CREATE_REQUESTED", "CREATE_SUCCEEDED", "STATUS_SYNCED"]
    with Session(app.state.engine) as session:
        assert session.get(InvoiceLink, link["id"]).amount == Decimal("1.00")


@pytest.mark.parametrize("decision", [None, "reject"])
def test_unapproved_or_rejected_candidates_are_blocked_and_audited(api, decision):
    _, client, cid, paypal = api
    row = rows(client, cid)[0]
    if decision:
        client.post(f"/api/milestones/{row['id']}/{decision}", json={**context(row), "confirmed": True})
        row = rows(client, cid)[0]
    assert client.get(f"/api/milestones/{row['id']}/invoice/preview").status_code == 409
    body = {**context(row), "contract_id": cid, "idempotency_key": str(uuid4()), "preview_digest": "a" * 64, "confirmed": True}
    assert client.post(f"/api/milestones/{row['id']}/invoice", json=body).status_code == 409
    assert not paypal.creates
    assert client.get(f"/api/milestones/{row['id']}/invoice").status_code == 404
    assert client.get(f"/api/milestones/{row['id']}/invoice/history").json()[0]["action"] == "VALIDATION_REJECTED"


@pytest.mark.parametrize("change", ["milestone_revision", "contract_revision", "digest", "contract_id"])
def test_stale_or_wrong_contract_context_cannot_reach_paypal(api, change):
    _, client, cid, paypal = api
    row = approve(client, cid)
    body = creation(client, row)
    if change == "milestone_revision": body["expected_revision"] -= 1
    if change == "contract_revision": body["expected_contract_revision"] -= 1
    if change == "digest": body["preview_digest"] = "b" * 64
    if change == "contract_id": body["contract_id"] = str(uuid4())
    assert client.post(f"/api/milestones/{row['id']}/invoice", json=body).status_code == 409
    assert not paypal.creates


@pytest.mark.parametrize("ack,note", [(False, None), (True, None), (False, "Reviewed discrepancy")])
def test_partial_approved_total_requires_separate_discrepancy_review(api, ack, note):
    _, client, cid, paypal = api
    row = approve(client, cid)
    view = client.get(f"/api/milestones/{row['id']}/invoice/preview").json()
    assert view["declared_total"] == "3.00" and view["approved_total"] == "1.00"
    assert "APPROVED_TOTAL_DIFFERS_FROM_CONTRACT" in view["discrepancies"]
    body = creation(client, row)
    body.update(acknowledge_discrepancies=ack, discrepancy_note=note)
    result = client.post(f"/api/milestones/{row['id']}/invoice", json=body)
    assert result.status_code == 409
    assert not paypal.creates


def test_idempotent_replay_and_changed_key_never_make_a_second_create_call(api):
    _, client, cid, paypal = api
    row = approve(client, cid)
    body = creation(client, row)
    path = f"/api/milestones/{row['id']}/invoice"
    first = client.post(path, json=body)
    assert first.status_code == 201
    repeat = client.post(path, json=body)
    assert repeat.status_code == 200 and repeat.json() == first.json()
    changed = {**body, "idempotency_key": str(uuid4())}
    assert client.post(path, json=changed).status_code == 409
    assert client.post(path, json={**body, "note": "Changed request with the same key"}).status_code == 409
    assert len(paypal.creates) == 1


def test_idempotency_key_cannot_be_reused_for_another_milestone(api):
    _, client, cid, paypal = api
    approve(client, cid)
    approve(client, cid, 1)
    first, second = rows(client, cid)[:2]
    body = creation(client, first)
    assert client.post(f"/api/milestones/{first['id']}/invoice", json=body).status_code == 201
    other = creation(client, second)
    other["idempotency_key"] = body["idempotency_key"]
    assert client.post(f"/api/milestones/{second['id']}/invoice", json=other).status_code == 409
    assert len(paypal.creates) == 1


def test_reservation_is_committed_and_locks_review_before_provider_call(api):
    app, client, cid, paypal = api
    row = approve(client, cid)
    def callback():
        with Session(app.state.engine) as session:
            attempt = session.scalar(select(InvoiceLink).where(InvoiceLink.milestone_id == row["id"]))
            assert attempt.state == "CREATING" and attempt.invoice_id is None
        reopened = client.post(f"/api/milestones/{row['id']}/reopen", json={**context(row), "confirmed": True})
        assert reopened.status_code == 409
    paypal.after_create = callback
    assert client.post(f"/api/milestones/{row['id']}/invoice", json=creation(client, row)).status_code == 201


@pytest.mark.parametrize("failure", [
    PayPalAPIError("PAYPAL_TIMEOUT", operation="create_invoice", http_status=504),
    PayPalAPIError("PAYPAL_ERROR", operation="create_invoice", upstream_status=422),
    PayPalAPIError("INVOICE_ID_MISSING", operation="create_invoice", upstream_status=201),
])
def test_provider_failure_reserves_attempt_and_read_only_recovery_never_retries_post(api, failure):
    _, client, cid, paypal = api
    row = approve(client, cid)
    body = creation(client, row)
    path = f"/api/milestones/{row['id']}/invoice"
    paypal.create_error = failure
    assert client.post(path, json=body).status_code == failure.http_status
    link = client.get(path).json()
    assert link["state"] == "RECONCILIATION_REQUIRED" and link["invoice_id"] is None
    assert client.post(path, json=body).status_code == 409
    paypal.create_error = None
    recovered = client.post(path + "/reconcile", json=action(link))
    assert recovered.status_code == 200
    assert recovered.json()["invoice_id"] == INVOICE and recovered.json()["verified"] is True
    assert len(paypal.creates) == 1 and len(paypal.searches) == 1


def test_no_search_match_keeps_reservation_instead_of_authorizing_retry(api):
    _, client, cid, paypal = api
    row = approve(client, cid)
    path = f"/api/milestones/{row['id']}/invoice"
    body = creation(client, row)
    paypal.create_error = PayPalAPIError("PAYPAL_TIMEOUT", operation="create_invoice", http_status=504)
    client.post(path, json=body)
    paypal.search_missing = True
    link = client.get(path).json()
    result = client.post(path + "/reconcile", json=action(link))
    assert result.status_code == 200 and result.json()["state"] == "RECONCILIATION_REQUIRED"
    assert result.json()["error_code"] == "INVOICE_NOT_FOUND"
    assert client.post(path, json={**body, "idempotency_key": str(uuid4())}).status_code == 409
    assert len(paypal.creates) == 1


def test_lost_local_record_after_acceptance_recovers_known_id_without_another_post(api, monkeypatch):
    app, client, cid, paypal = api
    row = approve(client, cid)
    body = creation(client, row)
    original_commit = Session.commit
    count = 0
    def fail_recording(session):
        nonlocal count
        count += 1
        if count == 2:
            raise OperationalError("synthetic database failure", {}, Exception("synthetic"))
        return original_commit(session)
    monkeypatch.setattr(Session, "commit", fail_recording)
    path = f"/api/milestones/{row['id']}/invoice"
    result = client.post(path, json=body)
    assert result.status_code == 503 and result.json()["detail"]["invoice_id"] == INVOICE
    monkeypatch.setattr(Session, "commit", original_commit)
    link = client.get(path).json()
    assert link["state"] == "CREATING" and link["invoice_id"] is None
    assert client.post(path + "/reconcile", json=action(link)).status_code == 409
    with Session(app.state.engine) as session:
        session.get(InvoiceLink, link["id"]).created_at = utc_now() - timedelta(minutes=6)
        session.commit()
    recovered = client.post(path + "/reconcile", json={**action(link), "invoice_id": INVOICE})
    assert recovered.status_code == 200 and recovered.json()["invoice_id"] == INVOICE
    assert len(paypal.creates) == 1


def test_failed_follow_up_read_keeps_known_invoice_and_syncs_actual_payment_status(api):
    _, client, cid, paypal = api
    row = approve(client, cid)
    body = creation(client, row)
    path = f"/api/milestones/{row['id']}/invoice"
    paypal.read_error = PayPalAPIError("PAYPAL_TIMEOUT", operation="get_invoice", http_status=504)
    result = client.post(path, json=body)
    assert result.status_code == 504 and result.json()["detail"]["invoice_id"] == INVOICE
    link = client.get(path).json()
    assert link["invoice_id"] == INVOICE and link["paypal_status"] is None and link["verified"] is False
    paypal.read_error = None
    for status in ("DRAFT", "UNPAID", "PARTIALLY_PAID", "PAID", "MARKED_AS_PAID", "PAID_EXTERNAL"):
        paypal.status = status
        result = client.post(path + "/sync", json=action(link))
        assert result.status_code == 200 and result.json()["paypal_status"] == status
        link = result.json()
    assert len(paypal.creates) == 1


def test_sync_stale_revision_and_replacing_known_association_are_blocked(api):
    _, client, cid, paypal = api
    row = approve(client, cid)
    path = f"/api/milestones/{row['id']}/invoice"
    link = client.post(path, json=creation(client, row)).json()
    assert client.post(path + "/sync", json={**action(link), "expected_link_revision": 1}).status_code == 409
    assert client.post(path + "/reconcile", json={**action(link), "invoice_id": "INV2-OTHER-1234-5678"}).status_code == 409
    assert len(paypal.reads) == 1


def test_edited_contract_total_is_flagged_without_adjusting_invoice_amount(api):
    _, client, cid, paypal = api
    first = rows(client, cid)[0]
    changed = client.patch(f"/api/milestones/{first['id']}", json={**context(first), "changes": {"amount": "2.00"}})
    assert changed.status_code == 200
    row = approve(client, cid)
    preview = client.get(f"/api/milestones/{row['id']}/invoice/preview").json()
    assert preview["amount"] == "2.00" and "MILESTONE_TOTAL_MISMATCH" in preview["discrepancies"]
    assert row["evidence"]["amount"]["value"] == "1.00"


@pytest.mark.parametrize("changes", [{"amount": "1000.01"}, {"currency": "EUR"}])
def test_approved_values_outside_sandbox_conversion_limits_are_blocked(api, changes):
    _, client, cid, paypal = api
    row = rows(client, cid)[0]
    assert client.patch(f"/api/milestones/{row['id']}", json={**context(row), "changes": changes}).status_code == 200
    row = approve(client, cid)
    assert client.get(f"/api/milestones/{row['id']}/invoice/preview").status_code == 422
    assert not paypal.creates


def test_superseded_or_unrecorded_approval_does_not_authorize_conversion(api):
    app, client, cid, paypal = api
    row = approve(client, cid)
    with Session(app.state.engine) as session:
        session.get(Milestone, row["id"]).payment_conditions = "Unreviewed altered clause"
        session.commit()
    assert client.get(f"/api/milestones/{row['id']}/invoice/preview").status_code == 409
    assert not paypal.creates


@pytest.mark.parametrize("extra", [{"confirmed": False}, {"confirmed": "true"}, {"amount": "1.00"}, {"status": "PAID"}, {"recipient": "different@example.com"}, {"expected_revision": True}])
def test_client_cannot_override_money_payment_recipient_or_confirmation(api, extra):
    _, client, cid, paypal = api
    row = approve(client, cid)
    result = client.post(f"/api/milestones/{row['id']}/invoice", json={**creation(client, row), **extra})
    assert result.status_code == 422
    assert not paypal.creates


def test_link_and_history_survive_real_application_lifespan_restart(api):
    app, client, cid, paypal = api
    row = approve(client, cid)
    path = f"/api/milestones/{row['id']}/invoice"
    saved = client.post(path, json=creation(client, row)).json()
    events = client.get(path + "/history").json()
    restarted = create_app(storage_url=str(app.state.engine.url))
    restarted.dependency_overrides[get_paypal] = lambda: paypal
    with TestClient(restarted, base_url="http://127.0.0.1:8000") as other:
        assert other.get(path).json() == saved
        assert other.get(path + "/history").json() == events


def test_day4_reviews_survive_day5_schema_upgrade_and_nonempty_downgrade_refuses(api):
    app, client, cid, paypal = api
    row = approve(client, cid)
    original = client.get(f"/api/contracts/{cid}").json()
    cfg = migration_config()
    # Accounts now prevent any downgrade that would discard ownership/session history.
    with pytest.raises(RuntimeError, match="accounts, ownership"):
        with app.state.engine.begin() as connection:
            cfg.attributes["connection"] = connection
            command.downgrade(cfg, "0002_milestone_review")
    assert client.get(f"/api/contracts/{cid}").json() == original
    assert client.post(f"/api/milestones/{row['id']}/invoice", json=creation(client, row)).status_code == 201
    with pytest.raises(RuntimeError, match="accounts, ownership"):
        with app.state.engine.begin() as connection:
            cfg.attributes["connection"] = connection
            command.downgrade(cfg, "0002_milestone_review")


def test_foreign_origin_cannot_create_and_unknown_milestone_is_404(api):
    _, client, cid, paypal = api
    row = approve(client, cid)
    body = creation(client, row)
    assert client.post(f"/api/milestones/{row['id']}/invoice", json=body, headers={"Origin": "https://foreign.example"}).status_code == 403
    assert client.get(f"/api/milestones/{uuid4()}/invoice").status_code == 404
    assert not paypal.creates
