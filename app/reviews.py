"""Audited local review decisions. This module has no PayPal execution capability."""
from datetime import timezone
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update, exists
from sqlalchemy.orm import Session

from app.contract_schemas import MilestoneRead
from app.database import get_db
from app.review_schemas import (
    ContractMilestones, MilestonePatch, ReviewDecision, ReviewEventRead, ReviewSummary,
    SUPPORTED_CURRENCIES,
)
from app.storage import Contract, Milestone, ReviewEvent, InvoiceLink, utc_now

router = APIRouter(prefix="/api", tags=["Milestone review"])
EDITABLE = {"DRAFT", "REVIEW_REQUIRED"}
FIELDS = ("name", "description", "amount", "currency", "payment_conditions", "deadline", "deliverables", "acceptance_criteria")


def milestone_response(row: Milestone) -> dict:
    return {
        "id": row.id, "contract_id": row.contract_id, "ordinal": row.ordinal,
        **{field: getattr(row, field) for field in FIELDS},
        "status": row.status, "evidence": row.evidence, "revision": row.revision,
        "contract_review_revision": row.contract.review_revision,
        "has_human_edits": row.has_human_edits,
        "updated_at": row.updated_at.replace(tzinfo=timezone.utc),
        "reviewed_at": row.reviewed_at.replace(tzinfo=timezone.utc) if row.reviewed_at else None,
        "reviewer_label": row.reviewer_label,
    }


def summary(contract: Contract) -> dict:
    data = (contract.extraction or {}).get("data", {})
    currency = data.get("currency", {}).get("value")
    declared = data.get("total_value", {}).get("value")
    declared = Decimal(str(declared)) if declared is not None else None
    discrepancies = []
    if declared is None:
        discrepancies.append("CONTRACT_TOTAL_UNKNOWN")
    if not currency:
        discrepancies.append("CONTRACT_CURRENCY_UNKNOWN")
    def total(rows):
        if not rows:
            return Decimal("0")
        if not currency or any(row.amount is None or row.currency != currency for row in rows):
            return None
        return sum((row.amount for row in rows), Decimal("0"))
    all_rows = list(contract.milestones)
    total_value = total(all_rows)
    active = total([row for row in all_rows if row.status != "REJECTED"])
    approved = total([row for row in all_rows if row.status == "APPROVED"])
    matches = total_value == declared if total_value is not None and declared is not None else None
    active_matches = active == declared if active is not None and declared is not None else None
    if matches is False:
        discrepancies.append("MILESTONE_TOTAL_MISMATCH")
    if total_value is None:
        discrepancies.append("MISSING_AMOUNTS_OR_CURRENCY_CONFLICT")
    if any(row.status == "REJECTED" for row in all_rows):
        discrepancies.append("REJECTED_MILESTONES_PRESENT")
        if active_matches is False:
            discrepancies.append("ACTIVE_TOTAL_MISMATCH")
    return {
        "contract_id": contract.id, "contract_review_revision": contract.review_revision,
        "currency": currency, "declared_total": declared, "milestone_total": total_value,
        "active_total": active, "approved_total": approved, "totals_match": matches,
        "active_totals_match": active_matches,
        "counts": {state: sum(row.status == state for row in all_rows) for state in ("DRAFT", "REVIEW_REQUIRED", "APPROVED", "REJECTED")},
        "discrepancies": discrepancies,
    }


def lookup(session: Session, milestone_id: UUID) -> Milestone:
    row = session.get(Milestone, str(milestone_id))
    if row is None:
        raise HTTPException(status_code=404, detail="Milestone not found")
    return row


def snapshot(row: Milestone) -> dict:
    result = {field: getattr(row, field) for field in FIELDS}
    result["amount"] = format(row.amount, ".2f") if row.amount is not None else None
    result["deadline"] = row.deadline.isoformat() if row.deadline else None
    result.update(status=row.status, revision=row.revision, has_human_edits=row.has_human_edits,
                  reviewed_at=row.reviewed_at.isoformat() if row.reviewed_at else None,
                  reviewer_label=row.reviewer_label)
    return result


def review_flags(row: Milestone) -> list[str]:
    issues = list(summary(row.contract)["discrepancies"])
    prefix = f"milestones[{row.ordinal - 1}]"
    for issue in (row.contract.extraction or {}).get("review_issues", []):
        field, code = issue["field"], issue["code"]
        if field.startswith(prefix) or field in {"contract_text", "provider", "project_title", "currency", "total_value", "milestones"}:
            issues.append(field + ":" + code)
    if row.has_human_edits:
        issues.append("HUMAN_EDITS_DIFFER_FROM_ORIGINAL_AI")
    return sorted(set(issues))


def check_context(row: Milestone, payload):
    if row.contract.status == "PROCESSING":
        raise HTTPException(status_code=409, detail="Contract extraction is processing; review is locked")
    if row.contract.status != "REVIEW_REQUIRED" or not row.contract.extraction:
        raise HTTPException(status_code=409, detail="Recover the last validated extraction before reviewing milestones")
    if row.revision != payload.expected_revision or row.contract.review_revision != payload.expected_contract_revision:
        raise HTTPException(status_code=409, detail="Stale review revision; retrieve the latest milestone and contract summary")


def apply_action(session: Session, row: Milestone, payload, action: str, changes: dict, issues: list[str]):
    if session.scalar(select(InvoiceLink.id).where(InvoiceLink.milestone_id == row.id)):
        raise HTTPException(status_code=409, detail="A durable invoice attempt locks this milestone; reconcile the invoice instead of changing approved facts")
    before = snapshot(row)
    claimed = session.execute(update(Contract).where(
        Contract.id == row.contract_id, Contract.review_revision == payload.expected_contract_revision,
        Contract.status != "PROCESSING",
    ).values(review_revision=Contract.review_revision + 1))
    if claimed.rowcount != 1:
        session.rollback()
        raise HTTPException(status_code=409, detail="Stale contract review revision")
    changed = session.execute(update(Milestone).where(
        Milestone.id == row.id, Milestone.revision == payload.expected_revision,
        Milestone.status == before["status"],
        ~exists(select(InvoiceLink.id).where(InvoiceLink.milestone_id == Milestone.id)),
    ).values(**changes, revision=Milestone.revision + 1, updated_at=utc_now()))
    if changed.rowcount != 1:
        session.rollback()
        raise HTTPException(status_code=409, detail="Stale milestone review revision")
    session.refresh(row)
    session.add(ReviewEvent(
        contract_id=row.contract_id, milestone_id=row.id, revision=row.revision,
        contract_review_revision=payload.expected_contract_revision + 1, action=action,
        reviewer_label=payload.reviewer_label, note=payload.note,
        before=before, after=snapshot(row), acknowledged_issues=issues,
    ))
    session.commit()
    session.expire_all()
    return milestone_response(lookup(session, UUID(row.id)))


@router.get("/contracts/{contract_id}/milestones", response_model=ContractMilestones)
def get_contract_milestones(contract_id: UUID, session: Session = Depends(get_db)):
    contract = session.get(Contract, str(contract_id))
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract not found")
    return {"summary": summary(contract), "milestones": [milestone_response(row) for row in contract.milestones]}


@router.get("/milestones/{milestone_id}", response_model=MilestoneRead)
def get_milestone(milestone_id: UUID, session: Session = Depends(get_db)):
    return milestone_response(lookup(session, milestone_id))


@router.patch("/milestones/{milestone_id}", response_model=MilestoneRead)
def edit_milestone(milestone_id: UUID, payload: MilestonePatch, session: Session = Depends(get_db)):
    row = lookup(session, milestone_id)
    check_context(row, payload)
    if row.status not in EDITABLE:
        raise HTTPException(status_code=409, detail="Only milestones under review can be edited; reopen a decision first")
    changes = payload.changes.model_dump(exclude_unset=True)
    currency, amount = changes.get("currency", row.currency), changes.get("amount", row.amount)
    if currency == "JPY" and amount is not None and amount != amount.quantize(Decimal("1")):
        raise HTTPException(status_code=422, detail="JPY amounts must use whole currency units")
    if all(getattr(row, field) == value for field, value in changes.items()):
        raise HTTPException(status_code=422, detail="The edit does not change any milestone field")
    changes.update(has_human_edits=True, status="REVIEW_REQUIRED", reviewed_at=None, reviewer_label=None)
    return apply_action(session, row, payload, "EDITED", changes, [])


def decision(session: Session, row: Milestone, payload: ReviewDecision, target: str):
    check_context(row, payload)
    if target == "REVIEW_REQUIRED":
        if row.status not in {"APPROVED", "REJECTED"}:
            raise HTTPException(status_code=409, detail="Only an approved or rejected milestone can be reopened")
        return apply_action(session, row, payload, "REOPENED", {"status": target, "reviewed_at": None, "reviewer_label": None}, [])
    if row.status not in EDITABLE:
        raise HTTPException(status_code=409, detail="This milestone already has a decision; reopen it before changing that decision")
    flags = review_flags(row)
    if target == "APPROVED":
        if not row.name or row.amount is None or row.currency not in SUPPORTED_CURRENCIES or not row.payment_conditions or not row.deliverables or not row.acceptance_criteria:
            raise HTTPException(status_code=422, detail="Approval requires a name, valid amount/currency, payment conditions, deliverables, and acceptance criteria")
        if row.currency == "JPY" and row.amount != row.amount.quantize(Decimal("1")):
            raise HTTPException(status_code=422, detail="JPY amounts must use whole currency units")
        if flags and not payload.acknowledge_review_flags:
            raise HTTPException(status_code=409, detail={"message": "Review flags require explicit acknowledgement", "review_flags": flags})
    return apply_action(session, row, payload, target,
                        {"status": target, "reviewed_at": utc_now(), "reviewer_label": payload.reviewer_label},
                        flags if payload.acknowledge_review_flags else [])


@router.post("/milestones/{milestone_id}/approve", response_model=MilestoneRead)
def approve_milestone(milestone_id: UUID, payload: ReviewDecision, session: Session = Depends(get_db)):
    return decision(session, lookup(session, milestone_id), payload, "APPROVED")


@router.post("/milestones/{milestone_id}/reject", response_model=MilestoneRead)
def reject_milestone(milestone_id: UUID, payload: ReviewDecision, session: Session = Depends(get_db)):
    return decision(session, lookup(session, milestone_id), payload, "REJECTED")


@router.post("/milestones/{milestone_id}/reopen", response_model=MilestoneRead)
def reopen_milestone(milestone_id: UUID, payload: ReviewDecision, session: Session = Depends(get_db)):
    return decision(session, lookup(session, milestone_id), payload, "REVIEW_REQUIRED")


def event_response(row: ReviewEvent) -> dict:
    return {field: getattr(row, field) for field in ("id", "contract_id", "milestone_id", "revision", "contract_review_revision", "action", "reviewer_label", "note", "before", "after", "acknowledged_issues")} | {"created_at": row.created_at.replace(tzinfo=timezone.utc)}


@router.get("/milestones/{milestone_id}/history", response_model=list[ReviewEventRead])
def milestone_history(milestone_id: UUID, session: Session = Depends(get_db)):
    lookup(session, milestone_id)
    return [event_response(row) for row in session.scalars(select(ReviewEvent).where(ReviewEvent.milestone_id == str(milestone_id)).order_by(ReviewEvent.revision))]


@router.get("/contracts/{contract_id}/approval-history", response_model=list[ReviewEventRead])
def contract_history(contract_id: UUID, session: Session = Depends(get_db)):
    if session.get(Contract, str(contract_id)) is None:
        raise HTTPException(status_code=404, detail="Contract not found")
    return [event_response(row) for row in session.scalars(select(ReviewEvent).where(ReviewEvent.contract_id == str(contract_id)).order_by(ReviewEvent.contract_review_revision))]
