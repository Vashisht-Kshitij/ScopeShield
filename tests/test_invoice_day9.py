"""Recipient-bound previews, separate durable send intent, and offline catalog reliability."""
import copy
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.paypal import PayPalAPIError
from app.storage import InvoiceEvent, InvoiceLink, Milestone
from test_milestone_invoices import api, approve, creation, action, INVOICE


def draft(api):
    app, client, cid, paypal = api
    row = approve(client, cid)
    response = client.post(f"/api/milestones/{row['id']}/invoice", json=creation(client, row))
    assert response.status_code == 201, response.text
    return app, client, row, paypal, response.json()


def send_body(link, **changes):
    return {**action(link), "expected_invoice_id": link["invoice_id"], "confirmed": True, **changes}


def test_preview_recipient_is_bound_to_digest_and_never_calls_provider(api):
    _, client, cid, paypal = api
    row = approve(client, cid)
    view = client.get(f"/api/milestones/{row['id']}/invoice/preview").json()
    assert view["recipient_email"] == paypal.recipient_email and view["amount"] == "1.00"
    assert not paypal.creates and not paypal.reads
    payload = creation(client, row)
    paypal.recipient_email = "different@personal.example.com"
    result = client.post(f"/api/milestones/{row['id']}/invoice", json=payload)
    assert result.status_code == 409 and not paypal.creates


@pytest.mark.parametrize("value", [False, "true", 1, None])
def test_sending_requires_its_own_literal_confirmation(api, value):
    _, client, row, paypal, link = draft(api)
    paypal.send_milestone_invoice = lambda *a, **k: pytest.fail("Invalid confirmation reached PayPal")
    response = client.post(f"/api/milestones/{row['id']}/invoice/send", json=send_body(link, confirmed=value))
    assert response.status_code == 422


@pytest.mark.parametrize("change", ["revision", "invoice", "approval", "recipient"])
def test_stale_or_changed_send_context_never_reaches_provider(api, change):
    app, client, row, paypal, link = draft(api)
    payload = send_body(link)
    if change == "revision": payload["expected_link_revision"] += 1
    if change == "invoice": payload["expected_invoice_id"] = "INV2-OTHER-1234"
    if change == "recipient": paypal.recipient_email = "changed@personal.example.com"
    if change == "approval":
        with Session(app.state.engine) as session:
            session.get(Milestone, row["id"]).description = "Unrecorded tampering"
            session.commit()
    paypal.send_milestone_invoice = lambda *a, **k: pytest.fail("Stale send reached PayPal")
    assert client.post(f"/api/milestones/{row['id']}/invoice/send", json=payload).status_code == 409


def test_send_commits_intent_and_blocks_legacy_bypass_and_repeat(api):
    app, client, row, paypal, link = draft(api)
    sends = []
    def send(invoice_id, **expected):
        with Session(app.state.engine) as session:
            assert session.scalar(select(InvoiceEvent.id).where(InvoiceEvent.link_id == link["id"], InvoiceEvent.action == "SEND_REQUESTED"))
        assert expected["recipient_email"] == paypal.recipient_email
        assert expected["amount"] == link["amount"] or str(expected["amount"]) == link["amount"]
        sends.append(invoice_id); paypal.status = "SENT"
    paypal.send_milestone_invoice = send
    path = f"/api/milestones/{row['id']}/invoice"
    assert client.post(f"/api/invoices/{INVOICE}/send", json={"confirmed": True}).status_code == 410
    result = client.post(path + "/send", json=send_body(link))
    assert result.status_code == 200, result.text
    sent = result.json()
    assert sent["paypal_status"] == "SENT" and sent["send_state"] == "ACCEPTED" and sent["verified"] is True
    assert client.post(path + "/send", json=send_body(sent)).status_code == 409
    assert client.post(path + "/sync", json=action(sent)).status_code == 200
    assert sends == [INVOICE] and len(paypal.creates) == 1
    events = client.get(path + "/history").json()
    assert [entry["action"] for entry in events].count("SEND_REQUESTED") == 1
    assert [entry["action"] for entry in events].count("SEND_ACCEPTED") == 1


def test_uncertain_send_never_resets_permission_even_after_draft_status_sync(api):
    _, client, row, paypal, link = draft(api)
    sends = []
    def failed(invoice_id, **expected):
        sends.append(invoice_id)
        raise PayPalAPIError("PAYPAL_TIMEOUT", operation="send_invoice", http_status=504)
    paypal.send_milestone_invoice = failed
    path = f"/api/milestones/{row['id']}/invoice"
    assert client.post(path + "/send", json=send_body(link)).status_code == 504
    saved = client.get(path).json()
    assert saved["send_state"] == "UNCERTAIN" and not saved["verified"]
    refreshed = client.post(path + "/sync", json=action(saved)).json()
    assert refreshed["paypal_status"] == "DRAFT" and refreshed["verified"]
    assert client.post(path + "/send", json=send_body(refreshed)).status_code == 409
    assert sends == [INVOICE]


def test_send_accepted_followup_failure_preserves_identity_and_recovery(api):
    _, client, row, paypal, link = draft(api)
    def accepted(invoice_id, **expected):
        paypal.status = "SENT"
        paypal.read_error = PayPalAPIError("PAYPAL_UNAVAILABLE", operation="get_invoice")
    paypal.send_milestone_invoice = accepted
    path = f"/api/milestones/{row['id']}/invoice"
    result = client.post(path + "/send", json=send_body(link))
    assert result.status_code == 502 and result.json()["detail"]["completed_action"] == "send_accepted"
    saved = client.get(path).json()
    assert saved["invoice_id"] == INVOICE and saved["send_state"] == "ACCEPTED" and not saved["verified"]
    paypal.read_error = None
    assert client.post(path + "/reconcile", json=action(saved)).json()["paypal_status"] == "SENT"


def test_legacy_missing_recipient_cannot_be_silently_sent(api):
    app, client, row, paypal, link = draft(api)
    with Session(app.state.engine) as session:
        saved = session.get(InvoiceLink, link["id"])
        value = copy.deepcopy(saved.snapshot); value["preview"].pop("recipient_email")
        saved.snapshot = value; session.commit()
    assert client.post(f"/api/milestones/{row['id']}/invoice/send", json=send_body(link)).status_code == 409


def test_causal_audit_revisions_order_equal_timestamps(api):
    app, client, row, _, link = draft(api)
    with Session(app.state.engine) as session:
        for event in session.scalars(select(InvoiceEvent).where(InvoiceEvent.link_id == link["id"])):
            event.created_at = datetime(2026, 1, 1)
        session.commit()
    history = client.get(f"/api/milestones/{row['id']}/invoice/history").json()
    assert [event["action"] for event in history] == ["CREATE_REQUESTED", "CREATE_SUCCEEDED", "STATUS_SYNCED"]
    assert [event["data"]["link_revision"] for event in history] == [1, 2, 3]


def test_all_catalog_scenarios_use_real_ingestion_and_matching_fixtures(api):
    _, client, _, paypal = api
    scenarios = client.get("/api/demo-scenarios").json()
    assert len(scenarios) == 9 and all(row["live_ai"] is False for row in scenarios)
    assert any(row["id"] == "release-cedar" and row["supports_extraction"] for row in scenarios)
    from test_scope_changes import create, analyze
    for scenario in scenarios:
        contract = client.post("/api/contracts", files={"file": (scenario["filename"], scenario["text"].encode(), "text/plain")}).json()
        assert contract["text"].strip() == scenario["text"].strip()
        if scenario["supports_extraction"]:
            result = client.post(f"/api/contracts/{contract['id']}/extract", json={"mode": "offline_demo"})
            assert result.status_code == 200 and result.json()["extraction"]["live_ai"] is False
        if scenario.get("conversation_messages"):
            from test_scope_changes import LABEL
            request = client.post(f"/api/contracts/{contract['id']}/change-requests", json={
                **LABEL, "project_id": contract["project_id"], "title": scenario["title"],
                "conversation_messages": scenario["conversation_messages"]})
            assert request.status_code == 201
            result = analyze(client, request.json())
            assert result.status_code == 200 and result.json()["analysis"]["live_ai"] is False
        if scenario["requested_changes"]:
            request = create(client, contract, scenario["requested_changes"])
            result = analyze(client, request)
            assert result.status_code == 200 and result.json()["analysis"]["live_ai"] is False
            changed = create(client, contract, ["Arbitrary unsupported extra work"])
            assert analyze(client, changed).status_code == 422
    assert not paypal.creates and not paypal.reads


def test_full_walkthrough_rejects_changed_terms_and_preserves_exact_candidates(api):
    _,client,_,paypal=api
    from app.day6_fixture import WALKTHROUGH, SCENARIOS
    source=WALKTHROUGH.read_text(encoding="utf-8")
    original=client.post("/api/contracts",files={"file":(WALKTHROUGH.name,source.encode(),"text/plain")}).json()
    result=client.post(f"/api/contracts/{original['id']}/extract",json={"mode":"offline_demo"})
    assert result.status_code==200
    saved=result.json()
    assert saved["extraction"]["milestone_total"]=="3.00" and saved["extraction"]["totals_match"] is True
    assert [row["amount"] for row in saved["milestones"]]==["1.00"]*3
    assert all(row["status"]=="REVIEW_REQUIRED" for row in saved["milestones"])
    changed=client.post("/api/contracts",files={"file":("changed.txt",source.replace("USD 3.00","USD 30.00").encode(),"text/plain")}).json()
    assert client.post(f"/api/contracts/{changed['id']}/extract",json={"mode":"offline_demo"}).status_code==422
    from test_scope_changes import create,analyze
    assert analyze(client,create(client,changed,[SCENARIOS[0][0]])).status_code==422
    assert not paypal.creates and not paypal.reads
