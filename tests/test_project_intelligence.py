"""Financial and executive projections use persisted records, never providers."""
from copy import deepcopy
from decimal import Decimal
from io import BytesIO
import json
from pathlib import Path
from uuid import uuid4

import pytest
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.project_intelligence import intelligence, money_totals
from app.report_brief import brief_pages
from app.storage import ChangeOrder, ChangeRequest, ClientDecision, Contract, Milestone, Project, User
from test_scope_changes import api, analyze, approve, prepare, upload, body
from test_conversations import create
from test_evidence_pack import domain_state


def projection(client, project):
    response = client.get(f"/api/projects/{project}/evidence-pack")
    assert response.status_code == 200, response.text
    return response.json()


def test_exact_decimal_currency_partition_unknown_and_no_double_count():
    rows = [{"id": "a", "amount": "0.10", "currency": "USD"}, {"id": "b", "amount": "0.20", "currency": "USD"},
            {"id": "c", "amount": "5.00", "currency": "EUR"}, {"id": "d", "amount": None, "currency": "USD"},
            {"id": "e", "amount": "10.00", "currency": None}, {"id": "f", "amount": "NaN", "currency": "USD"}]
    result = money_totals([*rows, rows[0]])
    assert result == {"amounts": [{"currency": "EUR", "value": "5.00"}, {"currency": "USD", "value": "0.30"}], "unknown_terms": 3}
    assert money_totals([{"id": "a", "amount": "1e100000", "currency": "USD"}])["unknown_terms"] == 1
    for amount, currency in [(0.1, "USD"), (True, "USD"), ("0.001", "USD"), ("1.00", "ZZZ"), ("0", "USD")]:
        assert money_totals([{"id": "bad", "amount": amount, "currency": currency}]) == {"amounts": [], "unknown_terms": 1}


def test_verified_contract_current_milestone_values_and_unknown_outstanding(api):
    app, client, _ = api
    c = upload(client, Path("examples/day5_sandbox_agreement.txt").read_bytes())
    c = client.post(f"/api/contracts/{c['id']}/extract", json={"mode": "offline_demo"}).json()
    m = c["milestones"][0]
    response = client.post(f"/api/milestones/{m['id']}/approve", json=body(m["revision"], expected_contract_revision=c["review_revision"], confirmed=True, acknowledge_review_flags=True))
    assert response.status_code == 200, response.text
    before = domain_state(app)
    result = projection(client, c["project_id"])["intelligence"]
    assert domain_state(app) == before
    assert result["contracts"][0]["value"] == "3.00" and result["contracts"][0]["currency"] == "USD"
    assert result["milestones"]["approved_value"]["amounts"] == [{"currency": "USD", "value": "1.00"}]
    assert result["milestones"]["awaiting_value"]["amounts"] == [{"currency": "USD", "value": "2.00"}]
    assert result["milestones"]["awaiting_review"] == 2
    assert result["outstanding"]["value"] is None
    with Session(app.state.engine) as db:
        db.get(Milestone, m["id"]).amount = Decimal("999.00")
        db.get(Project, c["project_id"]).total_value = Decimal("90000.00")
        db.commit()
    changed = projection(client, c["project_id"])["intelligence"]
    assert changed["contracts"][0]["value"] == "3.00"  # metadata is never substituted
    assert changed["milestones"]["approved"] == 0 and changed["milestones"]["awaiting_review"] == 3
    other = upload(client, Path("examples/day5_sandbox_agreement.txt").read_bytes(), project_id=c["project_id"])
    assert client.post(f"/api/contracts/{other['id']}/extract", json={"mode": "offline_demo"}).status_code == 200
    versions = projection(client, c["project_id"])["intelligence"]
    assert versions["milestones"]["awaiting_value"]["not_combined"] and not versions["milestones"]["awaiting_value"]["amounts"]
    assert len(versions["milestones"]["per_agreement"]) == 2


@pytest.mark.parametrize("scenario,classification", [(0, "IN_SCOPE"), (1, "OUT_OF_SCOPE"), (2, "AMBIGUOUS")])
def test_request_count_excludes_individual_cumulative_and_repeated_double_count(api, scenario, classification):
    app, client, _ = api
    c, r = create(client, scenario)
    r = analyze(client, r).json()
    i = projection(client, c["project_id"])["intelligence"]
    assert sum(i["classifications"].values()) == 1
    assert i["classifications"][classification] == 1
    assert not i["requests"][0]["human_reviewed"]
    assert "OFFLINE / SIMULATED" in i["requests"][0]["origin"]
    assert i["requests"][0]["finding_count"] > 1
    if scenario == 1:
        r = approve(client, r)
        i = projection(client, c["project_id"])["intelligence"]
        assert i["requests"][0]["human_reviewed"]
        with Session(app.state.engine) as db:
            a = deepcopy(r["analysis"]); a["findings"][0]["contract_evidence"][0]["source_excerpt"] = "invented exclusion"
            db.get(ChangeRequest, r["id"]).analysis = a; db.commit()
        i = projection(client, c["project_id"])["intelligence"]
        assert i["classifications"]["UNRESOLVED"] == 1 and not i["requests"][0]["human_reviewed"]
        assert not i["findings"]


def test_order_prices_and_revision_specific_client_responses(api):
    app, client, _ = api
    c, r = create(client, 1); r = approve(client, analyze(client, r).json())
    order = prepare(client, r, proposed_price="75.00", currency="USD").json()
    i = projection(client, c["project_id"])["intelligence"]
    assert i["order_counts"] == {"approved": 0, "proposed": 1, "rejected": 0}
    assert i["milestones"]["approved_value"]["amounts"] == []
    approved = client.post(f"/api/change-orders/{order['id']}/approve", json=body(order["request_revision"], expected_order_revision=order["revision"], confirmed=True, acknowledge_review_flags=True)).json()
    with Session(app.state.engine) as db:
        user = db.scalar(select(User).where(User.username == "domain.test"))
        db.add(ClientDecision(order_id=order["id"], client_id=user.id, order_revision=order["revision"], decision="APPROVED", note="Synthetic older-revision response")); db.commit()
    i = projection(client, c["project_id"])["intelligence"]
    assert i["order_counts"]["approved"] == 1
    assert i["orders"][0]["stale_responses"] == 1 and not i["orders"][0]["current_responses"]
    assert i["orders"][0]["client_decision"] == "No current response recorded"
    with Session(app.state.engine) as db:
        db.get(ChangeOrder, order["id"]).proposed_price = Decimal("90.00"); db.commit()
    i = projection(client, c["project_id"])["intelligence"]
    assert i["order_counts"]["approved"] == 0 and i["order_counts"]["proposed"] == 1


@pytest.mark.parametrize("role,expected", [("anonymous", 401), ("foreign", 404), ("client", 403)])
def test_executive_download_owner_only_and_no_attachment_route(api, role, expected):
    app, client, c = api
    preview = projection(client, c["project_id"])
    if role == "anonymous": client.cookies.clear()
    else:
        with Session(app.state.engine) as db:
            user = db.scalar(select(User).where(User.username == "domain.test"))
            if role == "client":
                from app.storage import ProjectShare
                user.role = "client"; db.add(ProjectShare(project_id=c["project_id"], client_id=user.id))
            else:
                other = User(username="foreign", display_name="Other", role="freelancer", password_hash=user.password_hash); db.add(other); db.flush()
                db.get(Project, c["project_id"]).owner_id = other.id
            db.commit()
    result = client.get(f"/api/projects/{c['project_id']}/evidence-pack.pdf", params={"executive": True, "expected_revision": preview["source_revision"]})
    assert result.status_code == expected and not result.content.startswith(b"%PDF")
    assert client.get("/project-snapshot.json").status_code == 404


def test_two_page_summary_exact_quotes_no_prominent_uuids_and_regeneration(api):
    app, client, _ = api
    c, r = create(client, 1); r = analyze(client, r).json()
    preview = projection(client, c["project_id"]); before = domain_state(app)
    url = f"/api/projects/{c['project_id']}/evidence-pack.pdf"
    response = client.get(url, params={"executive": True, "expected_revision": preview["source_revision"]})
    assert response.status_code == 200, response.text[:500]
    assert domain_state(app) == before and "no-store" in response.headers["cache-control"]
    reader = PdfReader(BytesIO(response.content), strict=True)
    assert len(reader.pages) == 2
    text = "\n".join(p.extract_text() for p in reader.pages)
    assert "PROJECT BRIEF" in text and "UNAPPROVED interpretation" in text and "OFFLINE / SIMULATED" in text
    assert c["id"] not in text and preview["source_revision"] not in text
    saved = json.loads(reader.attachments["project-snapshot.json"][0])
    for page in brief_pages(saved, intelligence(saved)):
        for b in page:
            if b["kind"] == "quote":
                assert b["text"] in text
                assert b["text"] in c["text"] or any(b["text"] in m["content"] for m in r["conversation_messages"])
    assert not any(p.get("/Annots") for p in reader.pages)
    r = approve(client, r)
    assert client.get(url, params={"executive": True, "expected_revision": preview["source_revision"]}).status_code == 409
    fresh = projection(client, c["project_id"])
    assert fresh["source_revision"] != preview["source_revision"] and fresh["intelligence"]["requests"][0]["human_reviewed"]


def test_unicode_fonts_and_safe_escaping_in_both_pdf_views(api):
    app, client, c = api
    with Session(app.state.engine) as db:
        db.get(Project, c["project_id"]).title = 'Café “review” – € / ₹ / 😀 <script>x</script>'
        db.commit()
    preview = projection(client, c["project_id"])
    for executive in (False, True):
        response = client.get(f"/api/projects/{c['project_id']}/evidence-pack.pdf", params={"executive": executive, "expected_revision": preview["source_revision"]})
        assert response.status_code == 200
        reader = PdfReader(BytesIO(response.content))
        text = "\n".join(p.extract_text() for p in reader.pages)
        # Bold headings use Vera; the regular body font includes the rupee glyph.
        assert 'Café' in text and '<script>x</script>' in text and '[U+1F600]' in text
        assert '₹' in text
        assert not any(p.get('/Annots') for p in reader.pages)


def test_new_walkthrough_reuses_terms_and_refuses_altered_sources(api):
    from app.day19_fixture import scenario
    app, client, _ = api
    example = scenario()
    c = upload(client, example["text"].encode())
    extracted = client.post(f"/api/contracts/{c['id']}/extract", json={"mode": "offline_demo"})
    assert extracted.status_code == 200 and len(extracted.json()["milestones"]) == 3
    from test_scope_changes import LABEL
    row = client.post(f"/api/contracts/{c['id']}/change-requests", json={**LABEL, "project_id": c["project_id"], "title": example["title"], "conversation_messages": example["conversation_messages"]})
    assert row.status_code == 201, row.text
    response = analyze(client, row.json())
    assert response.status_code == 200, response.text
    findings = response.json()["analysis"]["findings"]
    assert [f["classification"] for f in findings] == ["IN_SCOPE", "OUT_OF_SCOPE", "AMBIGUOUS", "AMBIGUOUS"]
    assert all(not f.get("proposed_price") for f in findings)
    from app.day19_fixture import compare
    from app.extraction import ExtractionError
    messages = deepcopy(row.json()["conversation_messages"]); messages[0]["content"] += " extra"
    with pytest.raises(ExtractionError): compare(example["text"], messages)


@pytest.mark.parametrize("mode,origin", [("mocked_paypal_sandbox", "SIMULATED"), ("paypal_sandbox", "HISTORICAL_SANDBOX"), (None, "UNRESOLVED")])
def test_invoice_summary_separates_created_paid_and_never_reduces_real_balance(api, mode, origin):
    from app.storage import InvoiceLink
    app, client, _ = api
    c = upload(client, Path("examples/day5_sandbox_agreement.txt").read_bytes())
    c = client.post(f"/api/contracts/{c['id']}/extract", json={"mode": "offline_demo"}).json()
    with Session(app.state.engine) as db:
        db.add(InvoiceLink(milestone_id=c["milestones"][0]["id"], contract_id=c["id"], milestone_revision=1, contract_review_revision=0,
            idempotency_key=str(uuid4()), request_fingerprint="0"*64, invoice_number="SIM-"+uuid4().hex[:20], snapshot={"preview": {"provider_mode": mode}},
            amount=Decimal("1.00"), currency="USD", state="CREATED", invoice_id="INV2-SIM-EXAMPLE", paypal_status="PAID", verified=True))
        db.commit()
    p = projection(client, c["project_id"])
    assert p["intelligence"]["invoice_groups"] == [{"origin": origin, "local_state": "CREATED", "provider_status": "PAID", "count": 1}]
    assert not p["intelligence"]["invoices"][0]["historical_check_corroborated"]
    assert p["intelligence"]["outstanding"]["value"] is None
    result = client.get(f"/api/projects/{c['project_id']}/evidence-pack.pdf", params={"executive": True, "expected_revision": p["source_revision"]})
    assert result.status_code == 200, result.text[:400]
    text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(result.content)).pages)
    assert "local CREATED / provider PAID" in text
    if mode == "mocked_paypal_sandbox": assert "SIMULATED - NO REAL PAYMENT" in text
    assert "not real payment" in text and "NOT DETERMINED" in text
