"""Owner-only, immutable report snapshots. No provider dependencies or domain writes."""
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import String, func, select
from sqlalchemy.orm import Session

from app.auth import actor, own_project
from app.database import get_db
from app.resources import rate
from app.storage import (Project, Contract, ContractDocument, Milestone, ChangeRequest,
    ChangeOrder, ReviewEvent, ScopeEvent, ContractEvent, InvoiceLink, InvoiceEvent,
    ClientDecision, SecurityEvent, User)

router = APIRouter(prefix="/api/projects", tags=["Evidence Pack"])
MAX_BYTES = 2 * 1024 * 1024
MAX_PDF_BYTES = 8 * 1024 * 1024
FORMAT = "scopeshield-evidence-v1"


def failure(code, message, status=422):
    return HTTPException(status, {"code": code, "message": message})


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def scalar(value):
    if isinstance(value, datetime): return value.replace(tzinfo=timezone.utc).isoformat()
    if isinstance(value, date): return value.isoformat()
    if isinstance(value, Decimal): return format(value, "f")
    return value


def capture(session, project_id):
    """One actual SQLite read transaction, including ownership and every source row.

    Python sqlite legacy transaction mode does not BEGIN on SELECT. Explicit BEGIN
    is required here to avoid mixing pre/post-edit records across separate queries.
    Never load original PDF BLOBs, passwords, sessions or provider credentials.
    """
    session.reset()
    connection = session.connection()
    connection.exec_driver_sql("BEGIN")
    try:
        project = own_project(session, str(project_id))
        used = 0
        def rows(model, fields, condition, maximum):
            nonlocal used
            columns = [getattr(model, name) for name in fields.split()]
            count = session.scalar(select(func.count()).select_from(model).where(condition))
            if count > maximum:
                raise failure("REPORT_TOO_LARGE", "Project exceeds report record limits; no partial report was generated.")
            size = session.scalar(select(func.sum(sum((func.coalesce(func.length(c.cast(String)), 0) for c in columns), 0)))
                                  .select_from(model).where(condition)) or 0
            used += size
            if used > MAX_BYTES:
                raise failure("REPORT_TOO_LARGE", "Project exceeds the 2 MiB source budget; no records were silently omitted.")
            result = [{name: scalar(value) for name, value in zip(fields.split(), row)}
                      for row in session.execute(select(*columns).where(condition))]
            return sorted(result, key=lambda item: (str(item.get("created_at", "")), str(item.get("id", ""))))
        contracts = rows(Contract, "id project_id filename media_type size_bytes sha256 text page_count status extraction original_ai_output original_output_kind review_revision created_at updated_at error_code", Contract.project_id == project.id, 10)
        ids = [c["id"] for c in contracts]
        milestones = rows(Milestone, "id contract_id ordinal name amount currency payment_conditions deadline deliverables acceptance_criteria evidence status description revision has_human_edits updated_at reviewed_at reviewer_label", Milestone.contract_id.in_(ids), 100)
        requests = rows(ChangeRequest, "id project_id contract_id contract_sha256 contract_text_sha256 title requested_changes conversation_messages status revision analysis_revision analysis original_ai_output error_code created_at updated_at reviewed_at reviewer_label", ChangeRequest.contract_id.in_(ids), 30)
        request_ids = [r["id"] for r in requests]
        orders = rows(ChangeOrder, "id change_request_id analysis_revision proposed_deliverables proposed_price currency proposed_deadline supporting_evidence status revision created_at updated_at reviewed_at reviewer_label", ChangeOrder.change_request_id.in_(request_ids), 30)
        order_ids = [o["id"] for o in orders]
        milestone_ids = [m["id"] for m in milestones]
        reviews = rows(ReviewEvent, "id actor_id contract_id milestone_id revision contract_review_revision action reviewer_label note before after acknowledged_issues created_at", ReviewEvent.contract_id.in_(ids), 500)
        scope = rows(ScopeEvent, "id actor_id change_request_id change_order_id revision action reviewer_label note before after created_at", ScopeEvent.change_request_id.in_(request_ids), 500)
        invoices = rows(InvoiceLink, "id milestone_id contract_id milestone_revision contract_review_revision invoice_number snapshot amount currency state invoice_id paypal_status verified error_code revision created_at updated_at last_checked_at", InvoiceLink.contract_id.in_(ids), 100)
        invoice_events = rows(InvoiceEvent, "id actor_id milestone_id link_id action reviewer_label note data created_at", InvoiceEvent.milestone_id.in_(milestone_ids), 500)
        contract_events = rows(ContractEvent, "id actor_id contract_id action data created_at", ContractEvent.contract_id.in_(ids), 500)
        decisions = rows(ClientDecision, "id order_id client_id order_revision decision note created_at", ClientDecision.order_id.in_(order_ids), 100)
        security = rows(SecurityEvent, "id actor_id action resource_id created_at", SecurityEvent.resource_id.in_([project.id, *ids, *milestone_ids, *request_ids, *order_ids]), 500)
        documents = rows(ContractDocument, "contract_id provenance", ContractDocument.contract_id.in_(ids), 10)
        actors = {e.get("actor_id") or e.get("client_id") for e in [*reviews, *scope, *invoice_events, *contract_events, *decisions, *security]}
        people = rows(User, "id display_name role", User.id.in_(actors - {None}), 100)
        result = {"format": FORMAT, "project": {key: scalar(getattr(project, key)) for key in ("id", "title", "currency", "total_value", "status", "created_at")},
                  "contracts": contracts, "milestones": milestones, "requests": requests, "orders": orders,
                  "reviews": reviews, "scope_events": scope, "invoices": invoices, "invoice_events": invoice_events,
                  "contract_events": contract_events, "client_decisions": decisions, "security_events": security, "documents": documents, "actors": people}
        if len(canonical(result)) > MAX_BYTES:
            raise failure("REPORT_TOO_LARGE", "Serialized snapshot exceeds 2 MiB; no partial report was generated.")
        return result
    finally:
        session.rollback()  # Release read lock before CPU work. Never commit/export audit.



def run_worker(snapshot, *, summary=False, executive=False):
    environment = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "PATH") if key in os.environ}
    process = subprocess.Popen([sys.executable, "-I", str(Path(__file__).with_name("report_worker.py")), *(["--summary"] if summary else ["--executive"] if executive else [])],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        output, _ = process.communicate(canonical(snapshot), timeout=25)
    except BaseException as exc:
        process.kill(); process.communicate()
        if isinstance(exc, subprocess.TimeoutExpired):
            raise failure("REPORT_TIMEOUT", "Report generation exceeded 25 seconds. No project records changed.", 503) from None
        raise
    if process.returncode or len(output) > MAX_PDF_BYTES:
        raise failure("REPORT_GENERATION_FAILED", "Report could not be rendered within its limits. No project records changed.", 503)
    return output


def generate_pdf(snapshot, *, executive=False):
    output = run_worker(snapshot, executive=True) if executive else run_worker(snapshot)
    if not output.startswith(b"%PDF-"):
        raise failure("REPORT_GENERATION_FAILED", "The renderer did not return a valid PDF. No project records changed.", 503)
    return output


def generate_summary(snapshot):
    try:
        value = json.loads(run_worker(snapshot, summary=True))
        if set(value) != {"warnings", "sections", "intelligence"} or not isinstance(value["intelligence"], dict) or any(not isinstance(value[key], list) or not all(isinstance(item, str) for item in value[key]) for key in ("warnings", "sections")):
            raise ValueError
        return value
    except (ValueError, TypeError):
        raise failure("REPORT_GENERATION_FAILED", "The renderer did not return a valid summary. No project records changed.", 503) from None


def packet(session, project_id):
    try:
        snapshot = capture(session, project_id)
        snapshot["source_revision"] = digest(snapshot)
        snapshot["generated_at"] = datetime.now(timezone.utc).isoformat()
        return snapshot
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise failure("REPORT_SOURCE_INVALID", "Stored report data is malformed; ask the operator to review it. No records changed.") from None


@router.get("/{project_id}/evidence-pack")
def preview(project_id: UUID, request: Request, session: Session = Depends(get_db)):
    user_id = actor(session).id
    session.reset()  # Do not retain an auth lookup connection while reserving admission.
    rate(session.get_bind(), "REPORT_PREVIEW", user_id, 20)
    with request.app.state.resources.slot("pdf"):
        snapshot = packet(session, project_id)
        content = generate_summary(snapshot)
    return {"project_id": str(project_id), "source_revision": snapshot["source_revision"], "generated_at": snapshot["generated_at"],
            "counts": {key: len(snapshot[key]) for key in ("contracts", "milestones", "requests", "orders", "invoices")},
            "warnings": content["warnings"], "sections": content["sections"], "intelligence": content["intelligence"]}


@router.get("/{project_id}/evidence-pack.pdf")
def download(project_id: UUID, request: Request, expected_revision: str = Query(pattern=r"^[a-f0-9]{64}$"), executive: bool = False, session: Session = Depends(get_db)):
    user_id = actor(session).id
    session.reset()
    rate(session.get_bind(), "REPORT_DOWNLOAD", user_id, 5)
    with request.app.state.resources.slot("pdf"):
        snapshot = packet(session, project_id)
        if snapshot["source_revision"] != expected_revision:
            raise failure("REPORT_REVISION_CHANGED", "Project changed after the preview. Refresh the report summary before downloading.", 409)
        data = generate_pdf(snapshot, executive=True) if executive else generate_pdf(snapshot)
    return Response(data, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="scopeshield-{"summary-" if executive else ""}{project_id}-{expected_revision[:12]}.pdf"',
        "Cache-Control": "no-store, private", "X-Content-Type-Options": "nosniff"})
