"""Explicit project sharing and separate, non-financial client order responses."""
from datetime import timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, StrictBool, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import actor, own_project, user_read
from app.contract_schemas import StrictModel
from app.database import get_db
from app.storage import (ChangeOrder, ChangeRequest, ClientDecision, Contract,
                         Project, ProjectShare, SecurityEvent, User)

router = APIRouter(prefix="/api", tags=["Project sharing"])


class Share(StrictModel):
    username: str = Field(min_length=3, max_length=100)
    enabled: StrictBool
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def literal_true(cls, value):
        if value is not True: raise ValueError("Confirmation must be literal true")
        return value


@router.get("/projects/{project_id}/sharing")
def shared_clients(project_id: UUID, session: Session = Depends(get_db)):
    return [user_read(user) for user in session.scalars(select(User).join(ProjectShare, ProjectShare.client_id == User.id)
        .where(ProjectShare.project_id == str(project_id)).order_by(User.username))]


@router.post("/projects/{project_id}/sharing")
def share_project(project_id: UUID, payload: Share, session: Session = Depends(get_db)):
    project = own_project(session, str(project_id))
    user = session.scalar(select(User).where(User.username == payload.username.strip().lower(), User.role == "client", User.active.is_(True)))
    if user is None:
        raise HTTPException(404, "Active client account not found")
    existing = session.get(ProjectShare, (project.id, user.id))
    if payload.enabled and existing is None:
        session.add(ProjectShare(project_id=project.id, client_id=user.id))
    elif not payload.enabled and existing:
        session.delete(existing)
    else:
        return {"shared": payload.enabled}
    session.add(SecurityEvent(actor_id=actor(session).id, action="PROJECT_SHARED" if payload.enabled else "PROJECT_UNSHARED", resource_id=project.id))
    session.commit()
    return {"shared": payload.enabled}


def client_project(session: Session, project_id: str):
    user = actor(session)
    row = session.get(ProjectShare, (project_id, user.id))
    if row is None:
        raise HTTPException(404, "Shared project not found")
    return session.get(Project, project_id)


def response_read(row: ClientDecision):
    return dict(id=row.id, client_id=row.client_id, order_revision=row.order_revision, decision=row.decision,
                note=row.note, created_at=row.created_at.replace(tzinfo=timezone.utc))


def order_public(session: Session, order: ChangeOrder, *, client_id: str | None = None):
    request = session.get(ChangeRequest, order.change_request_id)
    analysis = request.analysis or {}
    query = select(ClientDecision).where(ClientDecision.order_id == order.id)
    if client_id:
        query = query.where(ClientDecision.client_id == client_id)
    return dict(id=order.id, revision=order.revision, proposed_deliverables=order.proposed_deliverables,
        proposed_price=format(order.proposed_price, ".2f") if order.proposed_price is not None else None,
        currency=order.currency, proposed_deadline=order.proposed_deadline,
        supporting_evidence=order.supporting_evidence,
        uncertainties=list(dict.fromkeys(question for finding in analysis.get("findings", [])
            if finding["classification"] != "IN_SCOPE" for question in finding["uncertainties"])),
        provenance={key: analysis.get(key) for key in ("provider", "mode", "live_ai")},
        responses=[response_read(row) for row in session.scalars(query.order_by(ClientDecision.created_at))])


@router.get("/change-orders/{order_id}/client-responses")
def owner_responses(order_id: UUID, session: Session = Depends(get_db)):
    return order_public(session, session.get(ChangeOrder, str(order_id)))


@router.get("/client/projects")
def client_projects(session: Session = Depends(get_db)):
    return [dict(id=row.id, title=row.title or "Shared project") for row in session.scalars(select(Project).join(ProjectShare)
        .where(ProjectShare.client_id == actor(session).id).order_by(Project.created_at.desc()).limit(100))]


@router.get("/client/projects/{project_id}")
def client_project_detail(project_id: UUID, session: Session = Depends(get_db)):
    project = client_project(session, str(project_id))
    contracts = []
    for contract in session.scalars(select(Contract).where(Contract.project_id == project.id).order_by(Contract.created_at)):
        orders = session.scalars(select(ChangeOrder).join(ChangeRequest)
            .where(ChangeRequest.contract_id == contract.id, ChangeRequest.status == "APPROVED", ChangeOrder.status == "APPROVED"))
        contracts.append(dict(id=contract.id, filename=contract.filename, text=contract.text,
            approved_milestones=[dict(name=row.name, amount=format(row.amount, ".2f") if row.amount else None, currency=row.currency,
                deliverables=row.deliverables, acceptance_criteria=row.acceptance_criteria, deadline=row.deadline)
                for row in contract.milestones if row.status == "APPROVED"],
            orders=[order_public(session, order, client_id=actor(session).id) for order in orders]))
    return dict(id=project.id, title=project.title or "Shared project", contracts=contracts)


class ClientResponse(StrictModel):
    expected_order_revision: int = Field(ge=1)
    decision: Literal["APPROVED", "REJECTED"]
    note: str = Field(min_length=3, max_length=1000)
    confirmed: Literal[True]
    _literal_true = field_validator("confirmed", mode="before")(Share.literal_true.__func__)


@router.post("/client/change-orders/{order_id}/respond", status_code=201)
def respond(order_id: UUID, payload: ClientResponse, session: Session = Depends(get_db)):
    order = session.get(ChangeOrder, str(order_id))
    row = session.get(ChangeRequest, order.change_request_id) if order else None
    if row is None:
        raise HTTPException(404, "Shared change order not found")
    client_project(session, row.project_id)
    if order.status != "APPROVED" or row.status != "APPROVED" or order.analysis_revision != row.analysis_revision:
        raise HTTPException(409, "The freelancer must approve this order before requesting your response")
    if payload.expected_order_revision != order.revision:
        raise HTTPException(409, "Terms changed; refresh and review the current order")
    response = ClientDecision(order_id=order.id, client_id=actor(session).id, order_revision=order.revision,
                              decision=payload.decision, note=payload.note.strip())
    if len(response.note) < 3:
        raise HTTPException(422, "Enter a response note of at least three characters")
    session.add(response)
    session.add(SecurityEvent(actor_id=actor(session).id, action="CLIENT_ORDER_" + payload.decision, resource_id=order.id))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "A response for this order revision is already recorded") from None
    return response_read(response)
