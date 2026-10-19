"""Persistent revision-checked scope review. No PayPal imports or action paths."""
import hashlib
import json
from datetime import timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.contracts import lookup as contract_lookup
from app.database import get_db
from app.extraction import sanitized_extraction_error
from app.resources import ai_operation, error_headers
from fastapi import Request
from app.scope_comparison import ScopeProvider, get_scope_provider, validate_comparison
from app.scope_schemas import (
    AnalyzeScope, ChangeOrderDecision, ChangeOrderRead, ChangeRequestRead, CreateChangeRequest,
    EditChangeOrder, EditChangeRequest, EditScopeAnalysis, PrepareChangeOrder,
    ScopeDecision, ScopeEventRead,
)
from app.storage import ChangeOrder, ChangeRequest, Contract, ScopeEvent, utc_now
from app.conversations import conversation_digest, requested_work, save_sources, message_accounting

router = APIRouter(prefix="/api", tags=["Scope changes"])
PENDING = {"PENDING", "REVIEW_REQUIRED"}


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def request_lookup(session: Session, request_id: UUID | str) -> ChangeRequest:
    row = session.get(ChangeRequest, str(request_id))
    if row is None:
        raise HTTPException(404, "Change request not found")
    return row


def order_lookup(session: Session, order_id: UUID) -> ChangeOrder:
    order = session.get(ChangeOrder, str(order_id))
    if order is None:
        raise HTTPException(404, "Change order not found")
    return order


def dates(row) -> dict:
    return {key: getattr(row, key).replace(tzinfo=timezone.utc) if getattr(row, key) else None
            for key in ("created_at", "updated_at", "reviewed_at")}


def request_response(session: Session, row: ChangeRequest) -> dict:
    fields = ("id", "project_id", "contract_id", "contract_sha256", "contract_text_sha256", "title",
              "requested_changes", "conversation_messages", "status", "revision", "analysis_revision", "analysis",
              "original_ai_output", "error_code", "reviewer_label")
    return {**{key: getattr(row, key) for key in fields}, **dates(row),
            "message_accounting": message_accounting(row.conversation_messages or [], (row.analysis or {}).get('findings', [])),
            "change_order_id": session.scalar(select(ChangeOrder.id).where(ChangeOrder.change_request_id == row.id))}


def order_response(session: Session, order: ChangeOrder) -> dict:
    row = request_lookup(session, order.change_request_id)
    contract = session.get(Contract, row.contract_id)
    fields = ("id", "change_request_id", "analysis_revision", "proposed_deliverables", "proposed_price",
              "currency", "proposed_deadline", "supporting_evidence", "status", "revision", "reviewer_label")
    uncertainties = list(dict.fromkeys(question for finding in row.analysis["findings"]
                                      if finding["classification"] in {"OUT_OF_SCOPE", "AMBIGUOUS"}
                                      for question in finding["uncertainties"]))
    return {**{key: getattr(order, key) for key in fields}, **dates(order), "uncertainties": uncertainties,
            "project_id": row.project_id, "contract_id": row.contract_id,
            "contract_sha256": row.contract_sha256, "contract_text_sha256": row.contract_text_sha256,
            "contract_filename": contract.filename, "request_revision": row.revision}


def snapshot(session: Session, row: ChangeRequest) -> dict:
    value = {"request": ChangeRequestRead.model_validate(request_response(session, row)).model_dump(mode="json")}
    order = session.scalar(select(ChangeOrder).where(ChangeOrder.change_request_id == row.id))
    if order:
        value["change_order"] = ChangeOrderRead.model_validate(order_response(session, order)).model_dump(mode="json")
    return value


def context(session: Session, row: ChangeRequest, payload) -> Contract:
    if row.revision != payload.expected_revision:
        raise HTTPException(409, "Stale change request revision; retrieve the current request")
    if row.conversation_messages is not None and (
        row.requested_changes != requested_work(row.conversation_messages)
        or (row.analysis and row.analysis.get("conversation_sha256") != conversation_digest(row.conversation_messages))
    ):
        raise HTTPException(409, "Conversation sources no longer match this analysis; review the saved revision")
    contract = session.get(Contract, row.contract_id)
    if (contract is None or contract.project_id != row.project_id or contract.sha256 != row.contract_sha256
            or text_digest(contract.text) != row.contract_text_sha256):
        raise HTTPException(409, "The original contract reference changed; create a request for the correct version")
    return contract


def mutate(session: Session, row: ChangeRequest, payload, action: str, changes: dict,
           *, token: str | None = None, order: ChangeOrder | None = None,
           order_changes: dict | None = None, new_order: ChangeOrder | None = None) -> dict:
    """One aggregate revision and one audit event, atomically with any order edit."""
    before = snapshot(session, row)
    conditions = [ChangeRequest.id == row.id, ChangeRequest.revision == payload.expected_revision,
                  ChangeRequest.status == row.status]
    if token:
        conditions.append(ChangeRequest.processing_token == token)
    changed = session.execute(update(ChangeRequest).where(*conditions).values(
        **changes, revision=payload.expected_revision + 1, updated_at=utc_now(),
    ))
    if changed.rowcount != 1:
        session.rollback()
        raise HTTPException(409, "Scope request changed during this operation; retrieve its current revision")
    if new_order:
        session.add(new_order)
        session.flush()
        order = new_order
    elif order is not None:
        changed = session.execute(update(ChangeOrder).where(
            ChangeOrder.id == order.id, ChangeOrder.revision == payload.expected_order_revision,
            ChangeOrder.status == "REVIEW_REQUIRED",
        ).values(**order_changes, revision=order.revision + 1, updated_at=utc_now()))
        if changed.rowcount != 1:
            session.rollback()
            raise HTTPException(409, "Stale change order revision or terminal decision")
        session.refresh(order)
    session.refresh(row)
    after = snapshot(session, row)
    if hasattr(payload, "confirmed"):
        after["decision"] = {"confirmed": payload.confirmed,
                             "acknowledge_review_flags": payload.acknowledge_review_flags}
    session.add(ScopeEvent(
        change_request_id=row.id, change_order_id=order.id if order else None,
        revision=row.revision, action=action, reviewer_label=payload.reviewer_label, note=payload.note,
        before=before, after=after,
    ))
    session.commit()
    session.expire_all()
    return request_response(session, request_lookup(session, row.id))


@router.post("/contracts/{contract_id}/change-requests", status_code=201, response_model=ChangeRequestRead,
             summary="Save requested work against an original contract version")
def create_change_request(contract_id: UUID, payload: CreateChangeRequest, session: Session = Depends(get_db)):
    contract = contract_lookup(session, contract_id)
    if str(payload.project_id) != contract.project_id:
        raise HTTPException(409, "The contract does not belong to the supplied project")
    work, messages = save_sources(payload)
    row = ChangeRequest(
        project_id=contract.project_id, contract_id=contract.id, contract_sha256=contract.sha256,
        contract_text_sha256=text_digest(contract.text), title=payload.title,
        requested_changes=work, conversation_messages=messages,
    )
    session.add(row)
    session.flush()
    session.add(ScopeEvent(change_request_id=row.id, revision=1, action="REQUEST_CREATED",
                           reviewer_label=payload.reviewer_label, note=payload.note,
                           before={}, after=snapshot(session, row)))
    session.commit()
    return request_response(session, row)


@router.get("/contracts/{contract_id}/change-requests", response_model=list[ChangeRequestRead])
def list_change_requests(contract_id: UUID, offset: int = Query(default=0, ge=0),
                         limit: int = Query(default=50, ge=1, le=100), newest: bool = False, session: Session = Depends(get_db)):
    contract_lookup(session, contract_id)
    return [request_response(session, row) for row in session.scalars(select(ChangeRequest).where(
        ChangeRequest.contract_id == str(contract_id)).order_by(
            ChangeRequest.created_at.desc() if newest else ChangeRequest.created_at,
            ChangeRequest.id.desc() if newest else ChangeRequest.id)
        .offset(offset).limit(limit))]


@router.get("/change-requests/{request_id}", response_model=ChangeRequestRead)
def get_change_request(request_id: UUID, session: Session = Depends(get_db)):
    return request_response(session, request_lookup(session, request_id))


@router.patch("/change-requests/{request_id}", response_model=ChangeRequestRead)
def edit_change_request(request_id: UUID, payload: EditChangeRequest, session: Session = Depends(get_db)):
    row = request_lookup(session, request_id)
    context(session, row, payload)
    if row.status not in PENDING:
        raise HTTPException(409, "Only pending requests can be edited")
    if (payload.conversation_messages is None) != (row.conversation_messages is None):
        raise HTTPException(409, "Source kind cannot change; edit this conversation as messages")
    work, messages = save_sources(payload, row.conversation_messages)
    if row.title == payload.title and row.requested_changes == work and row.conversation_messages == messages:
        raise HTTPException(422, "No request fields changed")
    return mutate(session, row, payload, "REQUEST_EDITED", {
        "title": payload.title, "requested_changes": work, "conversation_messages": messages, "status": "PENDING",
        "analysis": None, "original_ai_output": None, "analysis_revision": None, "error_code": None,
        "reviewed_at": None, "reviewer_label": None,
    })


@router.post("/change-requests/{request_id}/cancel", response_model=ChangeRequestRead)
def cancel_change_request(request_id: UUID, payload: ScopeDecision, session: Session = Depends(get_db)):
    row = request_lookup(session, request_id)
    context(session, row, payload)
    if row.status not in PENDING:
        raise HTTPException(409, "Only pending requests can be cancelled")
    return mutate(session, row, payload, "REQUEST_CANCELLED", {
        "status": "CANCELLED", "reviewed_at": utc_now(), "reviewer_label": payload.reviewer_label,
    })


@router.post("/change-requests/{request_id}/analyze", response_model=ChangeRequestRead,
             summary="Compare scope and persist a proposal requiring human review")
def analyze_scope(request_id: UUID, payload: AnalyzeScope, request: Request, session: Session = Depends(get_db)):
    if request.app.state.deployment.production and payload.mode == "configured":
        raise HTTPException(403, detail={"code": "DEMO_PROVIDER_DISABLED", "message": "Public demo: live AI is disabled. Use the matching offline example."})
    row = request_lookup(session, request_id)
    contract = context(session, row, payload)
    if row.status == "ANALYZING":
        if row.processing_started_at and row.processing_started_at >= utc_now() - timedelta(minutes=5):
            raise HTTPException(409, "Scope analysis is already processing")
    elif row.status not in PENDING:
        raise HTTPException(409, "Only pending requests can be analyzed")
    token = str(uuid4())
    text, changes = contract.text, list(row.requested_changes)
    conversation = json.loads(json.dumps(row.conversation_messages)) if row.conversation_messages is not None else None
    mutate(session, row, payload, "ANALYSIS_STARTED", {
        "status": "ANALYZING", "processing_token": token, "processing_started_at": utc_now(), "error_code": None,
    })
    attempt = payload.model_copy(update={"expected_revision": payload.expected_revision + 1})
    try:
        # Resolve configuration AFTER the durable claim, so configuration failures
        # also preserve pending requests and receive a persisted failure event.
        provider = get_scope_provider() if payload.mode == "configured" else offline_provider()
        with ai_operation(session, provider, request.app.state.resources):
            raw = (provider.compare_conversation(text, conversation) if conversation is not None
                   else provider.compare_scope(text, changes))
        result = validate_comparison(raw, text, changes, provider=provider, mode=payload.mode, conversation=conversation)
    except Exception as exc:
        error = sanitized_extraction_error(exc)
        session.expire_all()
        row = request_lookup(session, request_id)
        mutate(session, row, attempt, "ANALYSIS_FAILED", {
            "status": "REVIEW_REQUIRED" if row.analysis else "PENDING", "error_code": error.code,
            "processing_token": None, "processing_started_at": None,
        }, token=token)
        raise HTTPException(error.status_code, {"code": error.code,
                            "message": error.message + ". The request and any prior result are preserved",
                            "request_id": row.id}, headers=error_headers(error)) from None
    session.expire_all()
    row = request_lookup(session, request_id)
    context(session, row, attempt)
    return mutate(session, row, attempt, "ANALYSIS_SAVED", {
        "status": "REVIEW_REQUIRED", "analysis": result.model_dump(mode="json"),
        "original_ai_output": json.loads(raw), "analysis_revision": attempt.expected_revision + 1,
        "processing_token": None, "processing_started_at": None, "error_code": None,
        "reviewed_at": None, "reviewer_label": None,
    }, token=token)


def offline_provider() -> ScopeProvider:
    from app.demo import OfflineScenarioProvider
    return OfflineScenarioProvider()


@router.patch("/change-requests/{request_id}/analysis", response_model=ChangeRequestRead)
def edit_analysis(request_id: UUID, payload: EditScopeAnalysis, session: Session = Depends(get_db)):
    row = request_lookup(session, request_id)
    contract = context(session, row, payload)
    if row.status != "REVIEW_REQUIRED" or not row.analysis:
        raise HTTPException(409, "An analysis under review is required for editing")
    metadata = SimpleNamespace(name=row.analysis["provider"], live_ai=row.analysis["live_ai"],
                               model=row.analysis["model"], last_usage=row.analysis["token_usage"])
    result = validate_comparison(payload.analysis.model_dump_json(), contract.text, row.requested_changes,
                                 provider=metadata, mode=row.analysis["mode"], conversation=row.conversation_messages)
    result.edited_by_human = True
    result.review_flags = sorted(set(result.review_flags + ["HUMAN_EDITED_ANALYSIS"]))
    return mutate(session, row, payload, "ANALYSIS_EDITED", {
        "analysis": result.model_dump(mode="json"), "analysis_revision": payload.expected_revision + 1,
        "reviewed_at": None, "reviewer_label": None, "error_code": None,
    })


def decide_analysis(session: Session, row: ChangeRequest, payload: ScopeDecision, status: str):
    context(session, row, payload)
    if row.status != "REVIEW_REQUIRED" or not row.analysis:
        raise HTTPException(409, "Only an analysis under review can receive a decision")
    flags = row.analysis["review_flags"]
    if status == "APPROVED" and not payload.acknowledge_review_flags:
        raise HTTPException(409, {"message": "Explicit acknowledgement of review flags is required", "review_flags": flags})
    return mutate(session, row, payload, "ANALYSIS_" + status, {
        "status": status, "reviewed_at": utc_now(), "reviewer_label": payload.reviewer_label,
    })


@router.post("/change-requests/{request_id}/analysis/approve", response_model=ChangeRequestRead)
def approve_analysis(request_id: UUID, payload: ScopeDecision, session: Session = Depends(get_db)):
    return decide_analysis(session, request_lookup(session, request_id), payload, "APPROVED")


@router.post("/change-requests/{request_id}/analysis/reject", response_model=ChangeRequestRead)
def reject_analysis(request_id: UUID, payload: ScopeDecision, session: Session = Depends(get_db)):
    return decide_analysis(session, request_lookup(session, request_id), payload, "REJECTED")


@router.post("/change-requests/{request_id}/change-order", status_code=201, response_model=ChangeOrderRead,
             summary="Prepare a human-entered draft; never create an invoice")
def prepare_order(request_id: UUID, payload: PrepareChangeOrder, session: Session = Depends(get_db)):
    row = request_lookup(session, request_id)
    context(session, row, payload)
    if row.status != "APPROVED" or not row.analysis:
        raise HTTPException(409, "Approve the scope analysis before preparing a change order")
    findings = [item for item in row.analysis["findings"] if item["classification"] in {"OUT_OF_SCOPE", "AMBIGUOUS"}]
    if not findings:
        raise HTTPException(409, "An in-scope request does not qualify for a change order")
    if session.scalar(select(ChangeOrder.id).where(ChangeOrder.change_request_id == row.id)):
        raise HTTPException(409, "A change order already exists for this request")
    evidence = [item for finding in findings for item in finding["contract_evidence"]]
    order = ChangeOrder(id=str(uuid4()), change_request_id=row.id, analysis_revision=row.analysis_revision,
                        supporting_evidence=evidence, **payload.terms.model_dump())
    mutate(session, row, payload, "ORDER_PREPARED", {}, new_order=order)
    return order_response(session, session.get(ChangeOrder, order.id))


@router.get("/change-requests/{request_id}/change-order", response_model=ChangeOrderRead)
def get_request_order(request_id: UUID, session: Session = Depends(get_db)):
    row = request_lookup(session, request_id)
    order = session.scalar(select(ChangeOrder).where(ChangeOrder.change_request_id == row.id))
    if order is None:
        raise HTTPException(404, "No change order exists for this request")
    return order_response(session, order)


@router.get("/change-orders/{order_id}", response_model=ChangeOrderRead)
def get_order(order_id: UUID, session: Session = Depends(get_db)):
    return order_response(session, order_lookup(session, order_id))


def order_context(session: Session, order: ChangeOrder, payload) -> ChangeRequest:
    row = request_lookup(session, order.change_request_id)
    context(session, row, payload)
    if order.revision != payload.expected_order_revision or order.status != "REVIEW_REQUIRED":
        raise HTTPException(409, "Stale change order revision or terminal decision")
    if row.status != "APPROVED" or row.analysis_revision != order.analysis_revision:
        raise HTTPException(409, "Change order analysis context is no longer approved")
    return row


@router.patch("/change-orders/{order_id}", response_model=ChangeOrderRead)
def edit_order(order_id: UUID, payload: EditChangeOrder, session: Session = Depends(get_db)):
    order = order_lookup(session, order_id)
    row = order_context(session, order, payload)
    changes = payload.terms.model_dump()
    if all(getattr(order, key) == value for key, value in changes.items()):
        raise HTTPException(422, "No change order fields changed")
    mutate(session, row, payload, "ORDER_EDITED", {}, order=order, order_changes=changes)
    return order_response(session, order)


def decide_order(session: Session, order: ChangeOrder, payload: ChangeOrderDecision, status: str):
    row = order_context(session, order, payload)
    if status == "APPROVED" and not payload.acknowledge_review_flags:
        raise HTTPException(409, "Acknowledge the analysis uncertainties and human-entered terms before approval")
    mutate(session, row, payload, "ORDER_" + status, {}, order=order, order_changes={
        "status": status, "reviewed_at": utc_now(), "reviewer_label": payload.reviewer_label,
    })
    return order_response(session, order)


@router.post("/change-orders/{order_id}/approve", response_model=ChangeOrderRead)
def approve_order(order_id: UUID, payload: ChangeOrderDecision, session: Session = Depends(get_db)):
    return decide_order(session, order_lookup(session, order_id), payload, "APPROVED")


@router.post("/change-orders/{order_id}/reject", response_model=ChangeOrderRead)
def reject_order(order_id: UUID, payload: ChangeOrderDecision, session: Session = Depends(get_db)):
    return decide_order(session, order_lookup(session, order_id), payload, "REJECTED")


@router.get("/change-requests/{request_id}/history", response_model=list[ScopeEventRead])
def scope_history(request_id: UUID, offset: int = Query(default=0, ge=0),
                  limit: int = Query(default=100, ge=1, le=500), session: Session = Depends(get_db)):
    request_lookup(session, request_id)
    return [{**{key: getattr(event, key) for key in ("id", "change_request_id", "change_order_id", "revision",
             "action", "reviewer_label", "note", "before", "after")},
             "created_at": event.created_at.replace(tzinfo=timezone.utc)}
            for event in session.scalars(select(ScopeEvent).where(ScopeEvent.change_request_id == str(request_id))
                                         .order_by(ScopeEvent.revision).offset(offset).limit(limit))]
