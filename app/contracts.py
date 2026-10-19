"""Contract ingestion and extraction routes; no PayPal dependency or action path."""
from datetime import timedelta, timezone
from uuid import UUID, uuid4
import json
import hashlib

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile, Request
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session
from starlette.responses import Response

from app.contract_schemas import ContractRead, ExtractionRequest, ProjectRead
from app.database import get_db
from app.extraction import (
    ExtractionProvider, OfflineDemoProvider,
    get_extraction_provider, sanitized_extraction_error, validate_extraction,
)
from app.ingestion import MAX_UPLOAD_BYTES, ingest
from app.storage import Contract, ContractDocument, ContractEvent, Milestone, Project, utc_now
from app.pdf_provenance import verified_provenance
from app.reviews import milestone_response
from app.auth import actor, own_project
from app.resources import ai_operation, error_headers, setting

router = APIRouter(prefix="/api", tags=["Contracts"])


def record_event(session, contract_id, action, data, *, created_at=None):
    # Upload/claim/completion already hold the contract's write transaction. Save
    # causal order independently of the wall clock; preserve all legacy timestamps.
    count, latest = session.execute(select(func.count(), func.max(ContractEvent.data["event_sequence"].as_integer()))
        .where(ContractEvent.contract_id == contract_id)).one()
    sequence = max(count, latest or 0) + 1
    session.add(ContractEvent(contract_id=contract_id, action=action, data={**data, "event_sequence": sequence},
                              **({"created_at": created_at} if created_at is not None else {})))


def lookup(session: Session, contract_id: UUID) -> Contract:
    contract = session.get(Contract, str(contract_id))
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract not found")
    return contract


def contract_response(contract: Contract) -> dict:
    from app.gemini import input_eligibility
    try:
        eligible, message = input_eligibility(contract.text)
    except HTTPException:
        eligible, message = False, "Configured AI input settings are invalid. The saved agreement remains available; ask the operator to check configuration"
    return {
        "original_pdf_available": contract.document is not None,
        "pdf_provenance": verified_provenance(contract.text, contract.page_count, contract.document.provenance,
                                               document_sha256=contract.sha256) if contract.document else None,
        "ai_input_eligible": eligible, "ai_input_message": message,
        "id": contract.id, "project_id": contract.project_id, "filename": contract.filename,
        "media_type": contract.media_type, "size_bytes": contract.size_bytes, "sha256": contract.sha256,
        "text": contract.text, "page_count": contract.page_count, "status": contract.status,
        "error_code": contract.error_code, "created_at": contract.created_at.replace(tzinfo=timezone.utc),
        "updated_at": contract.updated_at.replace(tzinfo=timezone.utc), "extraction": contract.extraction,
        "milestones": [milestone_response(item) for item in contract.milestones],
        "review_revision": contract.review_revision,
        "original_ai_output": contract.original_ai_output,
        "original_output_kind": contract.original_output_kind,
    }


@router.post("/contracts", status_code=201, response_model=ContractRead, summary="Upload a PDF or text contract")
def upload_contract(
    request: Request,
    file: UploadFile = File(...), project_id: UUID | None = Form(default=None),
    session: Session = Depends(get_db),
):
    try:
        with request.app.state.resources.slot("pdf"):
            raw = file.file.read(MAX_UPLOAD_BYTES + 1)
            document = ingest(file.filename, file.content_type, raw)
    finally:
        file.file.close()
    if project_id:
        project = own_project(session, str(project_id))
    else:
        project = Project(owner_id=actor(session).id)
        session.add(project)
        session.flush()
    fields = {key: value for key, value in document.__dict__.items() if key != "pdf_provenance"}
    contract = Contract(project_id=project.id, **fields)
    session.add(contract)
    session.flush()
    if document.media_type == "application/pdf":
        # INSERT above holds SQLite's writer lock until commit: concurrent uploads
        # cannot both observe free retention capacity and exceed these budgets.
        total = session.scalar(select(func.coalesce(func.sum(func.length(ContractDocument.data)), 0)))
        owned = session.scalar(select(func.coalesce(func.sum(func.length(ContractDocument.data)), 0))
            .join(Contract, Contract.id == ContractDocument.contract_id)
            .join(Project, Project.id == Contract.project_id).where(Project.owner_id == actor(session).id))
        if (total + len(raw) > setting("PDF_STORAGE_BYTES", 512 * 1024 * 1024, maximum=512 * 1024 * 1024)
                or owned + len(raw) > setting("PDF_USER_STORAGE_BYTES", 64 * 1024 * 1024, maximum=64 * 1024 * 1024)):
            raise HTTPException(507, {"code": "PDF_STORAGE_FULL", "message": "Original PDF retention allowance reached. Saved documents remain intact; ask the operator for a fresh synthetic workspace or upload UTF-8 text."})
        contract.document = ContractDocument(data=raw, provenance=document.pdf_provenance)
    record_event(session, contract.id, "CONTRACT_UPLOADED", {"filename": contract.filename}, created_at=contract.created_at)
    session.commit()
    return contract_response(contract)


@router.get("/contracts/{contract_id}", response_model=ContractRead, summary="Retrieve saved text, status, and extraction")
def get_contract(contract_id: UUID, session: Session = Depends(get_db)):
    return contract_response(lookup(session, contract_id))


@router.get("/projects/{project_id}", response_model=ProjectRead, summary="Retrieve a project and its contract IDs")
def get_project(project_id: UUID, session: Session = Depends(get_db)):
    project = session.get(Project, str(project_id))
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return {
        "id": project.id, "title": project.title, "currency": project.currency,
        "total_value": project.total_value, "status": project.status,
        "created_at": project.created_at.replace(tzinfo=timezone.utc),
        "contract_ids": [item.id for item in project.contracts],
    }


@router.get("/contracts/{contract_id}/original", summary="Download the owner's preserved original PDF")
def original_pdf(contract_id: UUID, session: Session = Depends(get_db)):
    # Global authorization resolves this contract through its owning project and
    # denies clients before this handler. Do not add an unauthenticated file mount.
    contract = lookup(session, contract_id)
    document = contract.document
    if document is None:
        raise HTTPException(404, "Original PDF unavailable for this record")
    data = document.data
    if len(data) != contract.size_bytes or hashlib.sha256(data).hexdigest() != contract.sha256:
        raise HTTPException(409, "Original PDF integrity check failed; contact the operator")
    return Response(data, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="agreement-{contract.id}.pdf"',
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox; default-src 'none'", "X-Frame-Options": "DENY",
    })


@router.post("/contracts/{contract_id}/extract", response_model=ContractRead, summary="Extract facts for human review")
def extract_contract(
    contract_id: UUID, request: Request, payload: ExtractionRequest = Body(default_factory=ExtractionRequest),
    session: Session = Depends(get_db),
    configured: ExtractionProvider = Depends(get_extraction_provider),
):
    if request.app.state.deployment.production and payload.mode == "configured":
        raise HTTPException(403, detail={"code": "DEMO_PROVIDER_DISABLED", "message": "Public demo: choose a bundled offline scenario. Live AI is disabled."})
    contract = lookup(session, contract_id)
    started = utc_now()
    if (contract.status == "PROCESSING" and contract.processing_started_at
            and contract.processing_started_at >= started - timedelta(minutes=5)):
        raise HTTPException(status_code=409, detail="Contract extraction is already processing")
    if contract.extraction and not payload.force:
        if contract.extraction["mode"] != payload.mode:
            raise HTTPException(status_code=409, detail="A result from another mode is saved; use force=true to reprocess explicitly")
        if contract.status in {"FAILED", "PROCESSING"}:
            # Recover an old failed retry (including already-reviewed rows), or an
            # expired lease, without replacing the last validated snapshot/IDs.
            restored = session.execute(update(Contract).where(
                Contract.id == contract.id, Contract.status == contract.status,
                Contract.processing_token == contract.processing_token,
                Contract.review_revision == contract.review_revision,
            ).values(status="REVIEW_REQUIRED", processing_token=None, processing_started_at=None))
            if restored.rowcount != 1:
                session.rollback()
                raise HTTPException(409, "Extraction changed during recovery; retrieve the current agreement")
            record_event(session, contract.id, "EXTRACTION_RECOVERED", {
                "retained_validated_extraction": True, "last_attempt_error": contract.error_code,
            })
            session.commit()
            session.refresh(contract)
        return contract_response(contract)
    if contract.review_revision > 0:
        raise HTTPException(status_code=409, detail="Human-reviewed milestones cannot be replaced by extraction; upload a new contract version")
    if payload.mode == "configured":
        from app.gemini import input_eligibility
        eligible, message = input_eligibility(contract.text)
        if not eligible:
            raise HTTPException(413, {"code": "AI_INPUT_TOO_LARGE", "message": message})
    token = str(uuid4())
    retained_validated = bool(contract.extraction)
    claimed = session.execute(update(Contract).where(
        Contract.id == contract.id,
        Contract.review_revision == 0,
        or_(Contract.status != "PROCESSING", Contract.processing_started_at < started - timedelta(minutes=5)),
    ).values(status="PROCESSING", processing_token=token, processing_started_at=started, error_code=None))
    if claimed.rowcount != 1:
        session.rollback()
        raise HTTPException(status_code=409, detail="Contract extraction is already processing")
    record_event(session, contract.id, "EXTRACTION_STARTED", {"mode": payload.mode, "attempt_id": token}, created_at=started)
    session.commit()
    provider = OfflineDemoProvider() if payload.mode == "offline_demo" else configured
    try:
        with ai_operation(session, provider, request.app.state.resources):
            raw = provider.extract(contract.text)
        result = validate_extraction(raw, contract.text, provider=provider, mode=payload.mode)
    except Exception as exc:
        error = sanitized_extraction_error(exc)
        failed = session.execute(update(Contract).where(Contract.id == contract.id, Contract.processing_token == token).values(
            status="REVIEW_REQUIRED" if retained_validated else "FAILED",
            processing_token=None, processing_started_at=None, error_code=error.code,
        ))
        if failed.rowcount == 1:
            record_event(session, contract.id, "EXTRACTION_FAILED", {
                "mode": payload.mode, "attempt_id": token, "error_code": error.code,
                "retained_validated_extraction": retained_validated,
            })
        session.commit()
        raise HTTPException(status_code=error.status_code, detail={"code": error.code, "message": error.message}, headers=error_headers(error)) from None
    saved = session.execute(update(Contract).where(Contract.id == contract.id, Contract.processing_token == token).values(
        status="REVIEW_REQUIRED", processing_token=None, processing_started_at=None, error_code=None,
        extraction=result.model_dump(mode="json"),
        original_ai_output=json.loads(raw), original_output_kind="provider_raw",
    ))
    if saved.rowcount != 1:
        session.rollback()
        raise HTTPException(status_code=409, detail="This extraction attempt was superseded; retrieve the current result")
    record_event(session, contract.id, "EXTRACTION_SAVED", {
        "mode": result.mode, "provider": result.provider, "live_ai": result.live_ai, "attempt_id": token,
    })
    session.execute(delete(Milestone).where(Milestone.contract_id == contract.id))
    for ordinal, milestone in enumerate(result.data.milestones, start=1):
        session.add(Milestone(
            contract_id=contract.id, ordinal=ordinal, name=milestone.name.value,
            amount=milestone.amount.value, currency=milestone.currency.value,
            payment_conditions=milestone.payment_conditions.value, deadline=milestone.deadline.value,
            deliverables=[item.value for item in milestone.deliverables if item.value is not None],
            acceptance_criteria=[item.value for item in milestone.acceptance_criteria if item.value is not None],
            evidence=milestone.model_dump(mode="json"), status="REVIEW_REQUIRED",
        ))
    project = contract.project
    # A project can hold multiple agreements; don't overwrite a previous candidate.
    if project.title is None and project.total_value is None:
        project.title, project.currency = result.data.project_title.value, result.data.currency.value
        project.total_value, project.status = result.data.total_value.value, "REVIEW_REQUIRED"
    session.commit()
    session.expire_all()
    return contract_response(lookup(session, contract_id))
