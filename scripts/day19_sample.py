"""Prepare a private synthetic Day 19 walkthrough; no real DB or provider access.

Only ignored .local/day19 or .local/day20 databases and two explicitly synthetic
account names are accepted. Does not modify existing owner projects. Sample PDFs use new filenames.
"""
from contextlib import ExitStack
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import secrets
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def prepare(database, password, username="synthetic.product", outputs=None):
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from app.auth import hash_password
    from app.day19_fixture import scenario
    from app.main import create_app
    from app.storage import User, Project, Milestone, Contract, InvoiceLink, InvoiceEvent
    database = Path(database).resolve()
    if not any(database.is_relative_to(ROOT / f".local/{day}") for day in ("day19", "day20")) or username not in {"synthetic.product", "qa.intelligence"}:
        raise ValueError("Only the isolated Day 19 synthetic workspace is allowed")
    output_paths = [Path(p).resolve() for p in outputs or []]
    if any(not p.is_relative_to(ROOT / "output/pdf") or p.exists() for p in output_paths):
        raise ValueError("Use new PDF files under output/pdf")
    database.parent.mkdir(parents=True, exist_ok=True)
    env = {"SCOPESHIELD_DATABASE_URL": "sqlite:///" + database.as_posix(), "SCOPESHIELD_AI_PROVIDER": "disabled", "SCOPESHIELD_AI_ALLOW_LIVE": "false"}
    def forbidden(*args, **kwargs): raise AssertionError("Synthetic sample prohibits external providers")
    def check(response, status=200):
        if response.status_code != status: raise RuntimeError(f"Synthetic workflow failed: {response.status_code}")
        return response.json()
    context = {"reviewer_label": "Synthetic scripted reviewer", "note": "SCRIPTED OFFLINE SIMULATION: workflow demonstration only; no real client or human consent is asserted."}
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, env))
        stack.enter_context(patch("requests.sessions.Session.request", forbidden))
        stack.enter_context(patch("requests.request", forbidden))
        app = create_app(storage_url=env["SCOPESHIELD_DATABASE_URL"])
        client = stack.enter_context(TestClient(app, base_url="http://127.0.0.1:8000"))
        with Session(app.state.engine) as db:
            user = db.scalar(select(User).where(User.username == username))
            if user is None:
                user = User(username=username, display_name="Synthetic product reviewer", role="freelancer", password_hash=hash_password(password)); db.add(user); db.flush()
            if db.scalar(select(Project).where(Project.owner_id == user.id)): raise ValueError("Existing workspace is preserved; use a fresh synthetic database")
            user_id = user.id; db.commit()
        csrf = check(client.get("/api/auth/csrf"))["csrf_token"]
        login = check(client.post("/api/auth/login", json={"username": username, "password": password}, headers={"X-CSRF-Token": csrf}))
        client.headers["X-CSRF-Token"] = login["csrf_token"]
        example = scenario()
        contract = check(client.post("/api/contracts", files={"file": (example["filename"], example["text"].encode(), "text/plain")}), 201)
        contract = check(client.post(f"/api/contracts/{contract['id']}/extract", json={"mode": "offline_demo"}))
        first = contract["milestones"][0]
        check(client.post(f"/api/milestones/{first['id']}/approve", json={**context, "expected_revision": first["revision"], "expected_contract_revision": contract["review_revision"], "confirmed": True, "acknowledge_review_flags": True}))
        r = check(client.post(f"/api/contracts/{contract['id']}/change-requests", json={**context, "project_id": contract["project_id"], "title": "Synthetic launch conversation", "conversation_messages": example["conversation_messages"]}), 201)
        r = check(client.post(f"/api/change-requests/{r['id']}/analyze", json={**context, "expected_revision": r["revision"], "mode": "offline_demo"}))
        r = check(client.post(f"/api/change-requests/{r['id']}/analysis/approve", json={**context, "expected_revision": r["revision"], "confirmed": True, "acknowledge_review_flags": True}))
        check(client.post(f"/api/change-requests/{r['id']}/change-order", json={**context, "expected_revision": r["revision"], "terms": {"proposed_deliverables": ["SYNTHETIC PROPOSAL: separate ticket-checkout work; support hours require negotiation"], "proposed_price": "2.00", "currency": "USD"}}), 201)
        with Session(app.state.engine) as db:
            p = db.get(Project, contract["project_id"]); p.title = "SYNTHETIC - Community launch page"
            # Seed history only; never create/send a provider invoice or payment.
            for m, status in zip(contract["milestones"], ["PAID", "SENT", "DRAFT"]):
                actual = db.get(Milestone, m["id"])
                link = InvoiceLink(milestone_id=m["id"], contract_id=contract["id"], milestone_revision=actual.revision,
                    contract_review_revision=db.get(Contract, contract["id"]).review_revision, idempotency_key=str(uuid4()), request_fingerprint="0" * 64,
                    invoice_number="SIM-" + uuid4().hex[:20], snapshot={"preview": {"provider_mode": "mocked_paypal_sandbox"}, "fixture_notice": "SIMULATED - NO REAL PAYMENT"},
                    amount=Decimal("1.00"), currency="USD", state="CREATED", invoice_id="INV2-SIM-" + uuid4().hex[:12].upper(), paypal_status=status, verified=False)
                db.add(link); db.flush()
                db.add(InvoiceEvent(actor_id=user_id, milestone_id=m["id"], link_id=link.id, action="SIMULATED_HISTORY_IMPORTED", reviewer_label=context["reviewer_label"], note=context["note"], data={"simulated": True, "paypal_status": status}))
            db.commit()
        preview = check(client.get(f"/api/projects/{contract['project_id']}/evidence-pack"))
        manifest = {"synthetic_only": True, "provider_calls": 0, "source_revision": preview["source_revision"], "counts": preview["counts"], "pdfs": []}
        for path, executive in zip(output_paths, (True, False)):
            response = client.get(f"/api/projects/{contract['project_id']}/evidence-pack.pdf", params={"expected_revision": preview["source_revision"], "executive": executive})
            if response.status_code != 200 or not response.content.startswith(b"%PDF-"): raise RuntimeError("Sample PDF failed")
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as f: f.write(response.content)
            manifest["pdfs"].append({"file": path.name, "executive": executive, "sha256": hashlib.sha256(response.content).hexdigest()})
        return manifest


if __name__ == "__main__":
    directory = ROOT / ".local/day19" / ("sample-" + uuid4().hex)
    outputs = [ROOT / "output/pdf/scopeshield-project-executive-summary.pdf", ROOT / "output/pdf/scopeshield-project-complete-evidence.pdf"]
    result = prepare(directory / "synthetic.db", secrets.token_urlsafe(24), outputs=outputs)
    with (ROOT / "output/pdf/scopeshield-project-evidence.manifest.json").open("x", encoding="utf-8") as f: json.dump(result, f, indent=2)
    print(json.dumps(result))
