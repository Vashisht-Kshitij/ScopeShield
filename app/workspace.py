"""Read-only catalog and factual activity aggregation for the local frontend."""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload

from app.contract_schemas import ProjectRead, StrictModel
from app.contracts import lookup
from app.database import get_db
from app.auth import actor
from app.storage import (ChangeOrder, ChangeRequest, ClientDecision, Contract, ContractEvent, InvoiceEvent, Milestone,
                         Project, ReviewEvent, ScopeEvent)

router = APIRouter(prefix="/api", tags=["Workspace"])
CONTRACT_PHASE = {"CONTRACT_UPLOADED": 0, "EXTRACTION_STARTED": 1, "EXTRACTION_SAVED": 2, "EXTRACTION_FAILED": 2}


def activity_order(value):
    kind = value["kind"]
    details = value["details"]
    causal = (details.get("event_sequence", CONTRACT_PHASE.get(value["action"], 0)) if kind == "contract"
              else details.get("contract_revision", details.get("link_revision", value["revision"] or 0)))
    return value["created_at"], {"contract": 0, "milestone": 1, "scope": 2, "invoice": 3, "client_response": 4}[kind], causal, value["id"]


class ContractSummary(StrictModel):
    id: UUID
    project_id: UUID
    title: str
    filename: str
    status: str
    created_at: datetime
    updated_at: datetime
    review_revision: int
    milestone_count: int
    pending_milestones: int
    change_request_count: int
    extraction_provider: str | None
    extraction_mode: str | None
    live_ai: bool | None


class ActivityRead(StrictModel):
    id: str
    entity_id: str
    kind: str
    action: str
    created_at: datetime
    revision: int | None = None
    reviewer_label: str | None = None
    note: str | None = None
    details: dict


@router.get("/contracts", response_model=list[ContractSummary])
def list_contracts(project_id: UUID | None = None, offset: int = Query(0, ge=0),
                   limit: int = Query(50, ge=1, le=100), session: Session = Depends(get_db)):
    query = select(Contract).join(Project).where(Project.owner_id == actor(session).id).order_by(Contract.created_at.desc(), Contract.id).offset(offset).limit(limit)
    if project_id:
        query = query.where(Contract.project_id == str(project_id))
    rows = list(session.scalars(query))
    ids = [row.id for row in rows]
    counts, pending, changes = {}, {}, {}
    for cid, status, count in session.execute(select(Milestone.contract_id, Milestone.status, func.count())
            .where(Milestone.contract_id.in_(ids)).group_by(Milestone.contract_id, Milestone.status)):
        counts[cid] = counts.get(cid, 0) + count
        if status in {"DRAFT", "REVIEW_REQUIRED"}: pending[cid] = pending.get(cid, 0) + count
    for cid, count in session.execute(select(ChangeRequest.contract_id, func.count())
            .where(ChangeRequest.contract_id.in_(ids)).group_by(ChangeRequest.contract_id)):
        changes[cid] = count
    return [{"id": row.id, "project_id": row.project_id,
             "title": (row.extraction or {}).get("data", {}).get("project_title", {}).get("value") or row.filename,
             "filename": row.filename, "status": row.status, "review_revision": row.review_revision,
             "created_at": row.created_at.replace(tzinfo=timezone.utc),
             "updated_at": row.updated_at.replace(tzinfo=timezone.utc),
             "milestone_count": counts.get(row.id, 0), "pending_milestones": pending.get(row.id, 0),
             "change_request_count": changes.get(row.id, 0),
             "extraction_provider": (row.extraction or {}).get("provider"),
             "extraction_mode": (row.extraction or {}).get("mode"),
             "live_ai": (row.extraction or {}).get("live_ai")} for row in rows]


@router.get("/projects", response_model=list[ProjectRead])
def list_projects(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), session: Session = Depends(get_db)):
    return [{"id": row.id, "title": row.title, "currency": row.currency, "total_value": row.total_value,
             "status": row.status, "created_at": row.created_at.replace(tzinfo=timezone.utc),
             "contract_ids": [contract.id for contract in row.contracts]}
            for row in session.scalars(select(Project).where(Project.owner_id == actor(session).id).options(selectinload(Project.contracts))
             .order_by(Project.created_at.desc(), Project.id).offset(offset).limit(limit))]


@router.get("/contracts/{contract_id}/activity", response_model=list[ActivityRead])
def activity(contract_id: UUID, offset: int = Query(0, ge=0, le=5000),
              limit: int = Query(100, ge=1, le=500), session: Session = Depends(get_db)):
    contract = lookup(session, contract_id)
    entries = []
    def add(row, kind, entity_id, details, action=None, revision=None, reviewer=None, note=None):
        entries.append(dict(id=row.id, entity_id=entity_id, kind=kind, action=action or row.action,
                            created_at=row.created_at.replace(tzinfo=timezone.utc), revision=revision,
                            reviewer_label=reviewer, note=note, details=details))
    count = offset + limit
    contract_sequence = func.coalesce(ContractEvent.data["event_sequence"].as_integer(), case(CONTRACT_PHASE, value=ContractEvent.action, else_=0))
    for event in session.scalars(select(ContractEvent).where(ContractEvent.contract_id == contract.id)
                                .order_by(ContractEvent.created_at.desc(), contract_sequence.desc(), ContractEvent.id.desc()).limit(count)):
        add(event, "contract", contract.id, {**event.data, "actor_id": event.actor_id})
    for event in session.scalars(select(ReviewEvent).where(ReviewEvent.contract_id == contract.id)
                                .order_by(ReviewEvent.created_at.desc(), ReviewEvent.contract_review_revision.desc(), ReviewEvent.id.desc()).limit(count)):
        changes = {key: {"before": event.before.get(key), "after": value}
                   for key, value in event.after.items() if event.before.get(key) != value}
        add(event, "milestone", event.milestone_id, {"changes": changes, "contract_revision": event.contract_review_revision, "actor_id": event.actor_id},
            "MILESTONE_" + event.action, event.revision, event.reviewer_label, event.note)
    for event in session.scalars(select(ScopeEvent).join(ChangeRequest, ScopeEvent.change_request_id == ChangeRequest.id)
            .where(ChangeRequest.contract_id == contract.id).order_by(ScopeEvent.created_at.desc(), ScopeEvent.revision.desc(), ScopeEvent.id.desc()).limit(count)):
        after = event.after.get("change_order") if event.change_order_id else event.after.get("request")
        before = event.before.get("change_order") if event.change_order_id else event.before.get("request")
        tracked = {"status", "title", "requested_changes", "analysis_revision", "proposed_deliverables",
                   "proposed_price", "currency", "proposed_deadline"}
        changes = {key: {"before": (before or {}).get(key), "after": value}
                   for key, value in (after or {}).items() if key in tracked and (before or {}).get(key) != value}
        add(event, "scope", event.change_order_id or event.change_request_id,
            {"previous_status": (before or {}).get("status"), "status": (after or {}).get("status"),
             "decision": event.after.get("decision"), "changes": changes, "actor_id": event.actor_id}, revision=event.revision, reviewer=event.reviewer_label, note=event.note)
    for event in session.scalars(select(InvoiceEvent).join(Milestone, InvoiceEvent.milestone_id == Milestone.id)
            .where(Milestone.contract_id == contract.id).order_by(InvoiceEvent.created_at.desc(),
                func.coalesce(InvoiceEvent.data["link_revision"].as_integer(), 0).desc(), InvoiceEvent.id.desc()).limit(count)):
        add(event, "invoice", event.milestone_id, {**event.data, "actor_id": event.actor_id}, "INVOICE_" + event.action,
            reviewer=event.reviewer_label, note=event.note)
    for response in session.scalars(select(ClientDecision).join(ChangeOrder).join(ChangeRequest)
            .where(ChangeRequest.contract_id == contract.id).order_by(ClientDecision.created_at.desc(), ClientDecision.id.desc()).limit(count)):
        add(response, "client_response", response.order_id,
            {"order_revision": response.order_revision, "actor_id": response.client_id},
            "CLIENT_ORDER_" + response.decision, note=response.note)
    return sorted(entries, key=activity_order, reverse=True)[offset:offset + limit]
