"""Evidence export through real ownership/CSRF and disposable synthetic storage."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import BytesIO
import json
from threading import Event
from uuid import uuid4

from pypdf import PdfReader
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.evidence_pack import capture, digest, generate_pdf, generate_summary, packet
from app.report_content import document, quote_reference
from app.pdf_provenance import page_provenance
from app.storage import Contract, Milestone, Project, User, InvoiceLink
from test_scope_changes import api, analyze, approve, prepare, upload, LABEL, body
from test_conversations import create


def report(client, project):
    preview = client.get(f"/api/projects/{project}/evidence-pack")
    assert preview.status_code == 200, preview.text
    result = client.get(f"/api/projects/{project}/evidence-pack.pdf", params={"expected_revision": preview.json()["source_revision"]})
    assert result.status_code == 200, result.text[:500]
    reader = PdfReader(BytesIO(result.content), strict=True)
    saved = json.loads(reader.attachments["project-snapshot.json"][0])
    return preview.json(), result, reader, saved


def text_content(snapshot):
    doc = document(snapshot)
    return "\n".join(b["text"] for s in doc["sections"] for b in s["blocks"])


def domain_state(app):
    with app.state.engine.connect() as db:
        return {table: list(map(tuple, db.execute(text("SELECT * FROM " + table)))) for table in
                ("projects", "contracts", "milestones", "change_requests", "change_orders", "review_events", "scope_events", "invoice_links", "invoice_events", "contract_events", "client_decisions", "security_events", "ai_usage")}


@pytest.mark.parametrize("scenario", [0, 1, 2])
def test_pdf_snapshot_exact_sources_review_states_and_no_domain_mutation(api, scenario):
    app, client, _ = api
    contract, row = create(client, scenario)
    saved = analyze(client, row).json()
    if scenario == 1:
        saved = approve(client, saved)
        assert prepare(client, saved).status_code == 201
    before = domain_state(app)
    summary, response, reader, snapshot = report(client, contract["project_id"])
    assert domain_state(app) == before
    assert snapshot["requests"][0]["conversation_messages"] == row["conversation_messages"]
    assert summary["counts"]["requests"] == 1 and summary["warnings"]
    if scenario == 2: assert any("ambiguous" in warning for warning in summary["warnings"])
    if scenario != 1: assert any("unapproved" in warning for warning in summary["warnings"])
    printable = "\n".join(p.extract_text() for p in reader.pages)
    assert "EVIDENCE PACK" in printable and "CUMULATIVE" in printable and "OFFLINE / SIMULATED" in printable
    assert "Human-reviewed conclusion" in printable if scenario == 1 else "UNAPPROVED / rejected / pending" in printable
    assert "PROPOSED / REVIEW_REQUIRED" in printable if scenario == 1 else "None recorded; no terms inferred" in printable
    assert all(f"Page {i}" in page.extract_text() for i, page in enumerate(reader.pages, 1))
    assert "no-store" in response.headers["cache-control"]
    assert response.headers["content-disposition"].startswith("attachment;")
    assert not any(page.get("/Annots") for page in reader.pages)
    assert "/OpenAction" not in reader.trailer["/Root"] and "/JavaScript" not in str(reader.trailer["/Root"].get("/Names", {}))
    revision = snapshot.pop("source_revision"); snapshot.pop("generated_at")
    assert digest(snapshot) == revision


@pytest.mark.parametrize("role", ["anonymous", "foreign", "client"])
def test_authorization_denies_summary_and_binary_without_leak(api, role):
    app, client, contract = api
    preview = client.get(f"/api/projects/{contract['project_id']}/evidence-pack").json()
    if role == "anonymous": client.cookies.clear()
    else:
        with Session(app.state.engine) as db:
            user = db.scalar(select(User).where(User.username == "domain.test"))
            if role == "client":
                from app.storage import ProjectShare
                user.role = "client"
                db.add(ProjectShare(project_id=contract["project_id"], client_id=user.id))
            else:
                other = User(username="other", display_name="Other", role="freelancer", password_hash=user.password_hash)
                db.add(other); db.flush(); db.get(Project, contract["project_id"]).owner_id = other.id
            db.commit()
    expected = {"anonymous": 401, "foreign": 404, "client": 403}[role]
    for path in ("evidence-pack", f"evidence-pack.pdf?expected_revision={preview['source_revision']}"):
        result = client.get(f"/api/projects/{contract['project_id']}/{path}")
        assert result.status_code == expected and not result.content.startswith(b"%PDF")


def test_revision_conflict_and_missing_records(api):
    app, client, contract = api
    url = f"/api/projects/{contract['project_id']}/evidence-pack"
    revision = client.get(url).json()["source_revision"]
    assert client.get(f"/api/projects/{uuid4()}/evidence-pack").status_code == 404
    with Session(app.state.engine) as db:
        db.get(Contract, contract["id"]).review_revision += 1; db.commit()
    assert client.get(url + ".pdf", params={"expected_revision": revision}).status_code == 409
    summary, _, _, snapshot = report(client, contract["project_id"])
    assert summary["source_revision"] != revision and snapshot["contracts"][0]["review_revision"] == 1


@pytest.mark.parametrize("evidence,valid", [
    ({"source_excerpt": "made up", "source_start": 0, "source_end": 7, "page": 42}, False),
    ({"source_excerpt": "repeat"}, False),
    ({"source_excerpt": "repeat", "source_start": 10, "source_end": 16, "page": 42}, True),
    ({"source_excerpt": "😀", "source_start": 8, "source_end": 9}, True),
])
def test_exact_range_repetition_unicode_and_never_trust_invented_pages(evidence, valid):
    text = "repeat\n\n😀 repeat"
    result = quote_reference(text, evidence)
    assert result["verified"] is valid
    if valid: assert result["physical_pages"] == [] and text[result["start"]:result["end"]] == result["quote"]


def test_verified_multiline_original_page_boundary_and_invalid_mapping():
    pages = ["First clause\ncontinued", "Second clause\ncontinued"]
    text = "\n\n".join(pages); metadata = page_provenance(pages)
    from app.pdf_provenance import verified_provenance
    quote = {"source_excerpt": "continued\n\nSecond clause", "source_start": 13, "source_end": 37, "page": 99}
    assert quote_reference(text, quote, verified_provenance(text, 2, metadata))["physical_pages"] == [1, 2]
    metadata["pages"][0]["page"] = 99
    assert quote_reference(text, quote, verified_provenance(text, 2, metadata))["physical_pages"] == []


def test_malicious_unicode_multiline_source_survives_attachment_and_safe_render(api):
    app, client, contract = api
    message = '  café 👩🏽‍💻 हिन्दी 中文\n<script>alert(1)</script><a href="https://evil.test">link</a>\u202e\x00  '
    # Corrupt legacy input is never executed; report preserves exact raw source.
    with Session(app.state.engine) as db:
        db.get(Contract, contract["id"]).text = message; db.commit()
    _, _, reader, snapshot = report(client, contract["project_id"])
    assert snapshot["contracts"][0]["text"] == message
    assert not any(page.get("/Annots") for page in reader.pages)
    assert len(reader.pages) > 0


def test_concurrent_generation_is_rejected_and_does_not_clear_first_slot(api, monkeypatch):
    app, client, contract = api
    url = f"/api/projects/{contract['project_id']}/evidence-pack"
    revision = client.get(url).json()["source_revision"]
    entered, release = Event(), Event()
    original = generate_pdf
    def waiting(snapshot):
        entered.set(); assert release.wait(20); return original(snapshot)
    monkeypatch.setattr("app.evidence_pack.generate_pdf", waiting)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(client.get, url + ".pdf", params={"expected_revision": revision})
        try:
            assert entered.wait(20)
            blocked = client.get(url + ".pdf", params={"expected_revision": revision})
            assert blocked.status_code == 429 and blocked.headers["Retry-After"] == "5"
            with Session(app.state.engine) as db:
                db.get(Project, contract["project_id"]).title = "Edited while the prior snapshot renders"
                db.commit()
        finally: release.set()
        result = future.result()
        assert result.status_code == 200
        snapshot = json.loads(PdfReader(BytesIO(result.content)).attachments["project-snapshot.json"][0])
        assert snapshot["project"]["title"] != "Edited while the prior snapshot renders"
    assert client.get(url).json()["source_revision"] != revision


def test_limits_refuse_no_truncation(api, monkeypatch):
    _, client, contract = api
    monkeypatch.setattr("app.evidence_pack.MAX_BYTES", 10)
    response = client.get(f"/api/projects/{contract['project_id']}/evidence-pack")
    assert response.status_code == 422 and response.json()["detail"]["code"] == "REPORT_TOO_LARGE"


@pytest.mark.parametrize("status", ["DRAFT", "SENT", "PAID"])
@pytest.mark.parametrize("provider_mode", ["mocked_paypal_sandbox", "paypal_sandbox", None])
def test_invoice_status_is_historical_sandbox_or_explicit_simulation(api, status, provider_mode):
    from decimal import Decimal
    from app.storage import utc_now, InvoiceEvent
    app, client, contract = api
    contract = upload(client, __import__('pathlib').Path("examples/day5_sandbox_agreement.txt").read_bytes())
    extracted = client.post(f"/api/contracts/{contract['id']}/extract", json={"mode": "offline_demo"}).json()
    milestone = extracted["milestones"][0]
    with Session(app.state.engine) as db:
        link = InvoiceLink(milestone_id=milestone["id"], contract_id=contract["id"], milestone_revision=1,
            contract_review_revision=0, idempotency_key=str(uuid4()), request_fingerprint="0"*64,
            invoice_number="SS-"+uuid4().hex[:22], snapshot={"preview": {"provider_mode": provider_mode}},
            amount=Decimal("1.00"), currency="USD", state="CREATED", invoice_id="INV2-SYNTHETIC-EXAMPLE",
            paypal_status=status, verified=True, last_checked_at=utc_now())
        db.add(link); db.flush()
        db.add(InvoiceEvent(milestone_id=milestone["id"], link_id=link.id, action="STATUS_SYNCED", reviewer_label="Synthetic mocked transport", note="All provider I/O is mocked in this test", data={"invoice_id": link.invoice_id, "paypal_status": status}))
        db.commit()
    _, _, reader, snapshot = report(client, contract["project_id"])
    printable = "\n".join(p.extract_text() for p in reader.pages)
    assert "Saved paypal_status: " + status in printable
    if provider_mode == "paypal_sandbox": assert "Recorded sandbox verification as of last_checked_at" in printable
    elif provider_mode == "mocked_paypal_sandbox": assert "OFFLINE / SIMULATED provider state" in printable and "Not independently provider-verified" in printable
    else: assert "UNRESOLVED provider provenance" in printable and "Not independently provider-verified" in printable
    assert "never real-money settlement" in printable


def test_missing_quote_and_unsupported_classification_remain_unresolved(api):
    app, client, _ = api
    contract, row = create(client, 1)
    saved = analyze(client, row).json()
    from app.storage import ChangeRequest
    with Session(app.state.engine) as db:
        analysis = deepcopy(saved["analysis"])
        analysis["findings"][0]["contract_evidence"][0].update(source_excerpt="fabricated source", source_start=0, source_end=17, page=42)
        db.get(ChangeRequest, row["id"]).analysis = analysis; db.commit()
    _, _, reader, _ = report(client, contract["project_id"])
    printable = "\n".join(p.extract_text() for p in reader.pages)
    assert "UNRESOLVED grounding" in printable and "UNRESOLVED evidence" in printable
    assert "physical original PDF page(s) 42" not in printable


def test_worker_timeout_kills_reaps_and_receives_no_provider_environment(monkeypatch):
    import subprocess
    calls = []
    class Worker:
        def communicate(self, *args, **kwargs):
            calls.append("communicate")
            if len(calls) == 1: raise subprocess.TimeoutExpired("synthetic-worker", 25)
            return b"", None
        def kill(self): calls.append("kill")
    def start(args, **kwargs):
        assert "-I" in args and "GEMINI_API_KEY" not in kwargs["env"] and "PAYPAL_CLIENT_SECRET" not in kwargs["env"]
        return Worker()
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-must-not-be-inherited")
    monkeypatch.setattr("app.evidence_pack.subprocess.Popen", start)
    for operation in (generate_pdf, generate_summary):
        calls.clear()
        with pytest.raises(__import__('fastapi').HTTPException) as caught: operation({})
        assert caught.value.status_code == 503 and calls == ["communicate", "kill", "communicate"]


def test_long_unbroken_unicode_and_malicious_message_rendering(api):
    app, client, _ = api
    _, row = create(client, messages=[{"sequence": 1, "sender": "Synthetic client", "role": "client", "content": 'é😀<script>no</script>\n' + 'X'*1900}])
    summary, _, reader, snapshot = report(client, row["project_id"])
    text = "\n".join(page.extract_text() for page in reader.pages)
    assert "[U+1F600]" in text and "<script>no</script>" in text
    assert snapshot["requests"][0]["conversation_messages"] == row["conversation_messages"]
    assert len(reader.pages) >= 3 and not any(page.get("/Annots") for page in reader.pages)


def test_approved_order_is_bound_to_exact_terms_and_human_edit_is_preserved(api):
    app, client, _ = api
    contract, row = create(client, 1)
    row = analyze(client, row).json()
    findings = deepcopy(row["analysis"]["findings"])
    findings[0]["explanation"] = "Human clarification: confirm whether this adds a separate page section."
    row = client.patch(f"/api/change-requests/{row['id']}/analysis", json=body(row["revision"], analysis={"findings": findings})).json()
    row = approve(client, row)
    order = prepare(client, row, proposed_price="75.00", currency="USD").json()
    approved = client.post(f"/api/change-orders/{order['id']}/approve", json=body(order["request_revision"], expected_order_revision=order["revision"], confirmed=True, acknowledge_review_flags=True))
    assert approved.status_code == 200, approved.text
    _, _, reader, _ = report(client, contract["project_id"])
    printable = "\n".join(p.extract_text() for p in reader.pages)
    assert "Freelancer APPROVED terms" in printable and "Human clarification" in printable and "Correction before" in printable
    from app.storage import ChangeOrder
    from decimal import Decimal
    with Session(app.state.engine) as db:
        db.get(ChangeOrder, order["id"]).proposed_price = Decimal("90.00"); db.commit()
    _, _, reader, _ = report(client, contract["project_id"])
    assert "current approval not confirmed" in "\n".join(p.extract_text() for p in reader.pages)


def test_empty_owned_project_and_malformed_render_failure_are_safe(api, monkeypatch):
    app, client, _ = api
    with Session(app.state.engine) as db:
        user = db.scalar(select(User).where(User.username == "domain.test"))
        project = Project(owner_id=user.id, title="Empty synthetic project", status="DRAFT")
        db.add(project); db.commit(); project_id = project.id
    summary, _, reader, _ = report(client, project_id)
    assert summary["counts"]["contracts"] == 0 and len(reader.pages) >= 1
    class Worker:
        returncode = 1
        def communicate(self, *args, **kwargs): return b"not a PDF", None
    monkeypatch.setattr("app.evidence_pack.subprocess.Popen", lambda *a, **kw: Worker())
    response = client.get(f"/api/projects/{project_id}/evidence-pack.pdf", params={"expected_revision": summary["source_revision"]})
    assert response.status_code == 503 and response.json()["detail"]["code"] == "REPORT_GENERATION_FAILED"


def test_real_ingested_pdf_page_citations_and_corrupt_provenance_fallback(api):
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from pathlib import Path
    import reportlab
    from app.conversation_fixture import PATH, catalog
    from app.storage import ContractDocument
    app, client, _ = api
    stream = BytesIO(); pdf = canvas.Canvas(stream)
    pdfmetrics.registerFont(TTFont("TestVera", str(Path(reportlab.__file__).parent / "fonts/Vera.ttf")))
    lines = PATH.read_text(encoding="utf-8").splitlines()
    for group in (lines[:4], lines[4:]):
        pdf.setFont("TestVera", 8)
        for i, line in enumerate(group): pdf.drawString(25, 790 - i*35, line)
        pdf.showPage()
    pdf.save()
    uploaded = client.post("/api/contracts", files={"file": ("physical-pages.pdf", stream.getvalue(), "application/pdf")})
    assert uploaded.status_code == 201, uploaded.text
    contract = uploaded.json()
    row = client.post(f"/api/contracts/{contract['id']}/change-requests", json={**LABEL, "project_id": contract["project_id"], "title": "Synthetic PDF conversation", "conversation_messages": catalog()[1]["conversation_messages"]}).json()
    result = analyze(client, row)
    assert result.status_code == 200, result.text
    _, _, reader, _ = report(client, contract["project_id"])
    assert "physical original PDF page(s) 2" in "\n".join(p.extract_text() for p in reader.pages)
    with Session(app.state.engine) as db:
        stored = db.get(ContractDocument, contract["id"])
        metadata = deepcopy(stored.provenance); metadata["pages"][0]["page"] = 42
        stored.provenance = metadata; db.commit()
    summary, _, reader, _ = report(client, contract["project_id"])
    assert any("page provenance is unavailable" in w for w in summary["warnings"])
    assert "physical original PDF page(s)" not in "\n".join(p.extract_text() for p in reader.pages)
