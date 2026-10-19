"""Explicit sandbox draft creation with durable reservations and read-only recovery.

Sending is a separate confirmed action with a durable attempt marker. There is no
pay, reset, resend, or automatic financial POST retry operation.
"""
import hashlib
import json
from datetime import timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, object_session

from app.database import get_db
from app.dependencies import get_paypal
from app.invoice_schemas import (
    CreateMilestoneInvoice, InvoiceAction, InvoiceEventRead, InvoiceLinkRead,
    InvoicePreview, ReconcileInvoice, SendMilestoneInvoice,
)
from app.paypal import PayPalAPIError, PayPalSandbox
from app.reviews import lookup, review_flags, snapshot, summary
from app.storage import Contract, InvoiceEvent, InvoiceLink, ReviewEvent, utc_now

router = APIRouter(prefix="/api/milestones", tags=["Milestone invoices"])


def digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def get_link(session: Session, milestone_id: UUID) -> InvoiceLink:
    lookup(session, milestone_id)
    link = session.scalar(select(InvoiceLink).where(InvoiceLink.milestone_id == str(milestone_id)))
    if link is None:
        raise HTTPException(status_code=404, detail="No invoice attempt is linked to this milestone")
    return link


def link_response(link: InvoiceLink) -> dict:
    fields = ("id", "milestone_id", "contract_id", "milestone_revision", "contract_review_revision",
              "invoice_number", "state", "invoice_id", "paypal_status", "amount", "currency", "snapshot",
              "verified", "error_code", "revision", "recipient_view_url")
    result = {field: getattr(link, field) for field in fields}
    saved = link.snapshot.get("preview", {})
    result.update(recipient_email=saved.get("recipient_email"), provider_mode=saved.get("provider_mode", "paypal_sandbox"))
    session = object_session(link)
    sends = list(session.scalars(select(InvoiceEvent).where(InvoiceEvent.link_id == link.id,
        InvoiceEvent.action.in_(["SEND_REQUESTED", "SEND_ACCEPTED", "SEND_UNCERTAIN"])))) if session else []
    send = max(sends, key=lambda item: (item.data.get("link_revision", 0), item.created_at, item.id), default=None)
    result["send_state"] = {"SEND_REQUESTED": "REQUESTED", "SEND_ACCEPTED": "ACCEPTED", "SEND_UNCERTAIN": "UNCERTAIN"}.get(send.action if send else None, "NOT_REQUESTED")
    for field in ("created_at", "updated_at", "last_checked_at"):
        value = getattr(link, field)
        result[field] = value.replace(tzinfo=timezone.utc) if value else None
    return result


def event(session: Session, milestone_id: str, payload, action: str, data: dict, link_id: str | None = None):
    if link_id:
        data = {**data, "link_revision": session.scalar(select(InvoiceLink.revision).where(InvoiceLink.id == link_id))}
    session.add(InvoiceEvent(milestone_id=milestone_id, link_id=link_id, action=action,
                            reviewer_label=payload.reviewer_label, note=payload.note, data=data))


def preview(session: Session, row, paypal: PayPalSandbox) -> dict:
    if row.status != "APPROVED" or row.contract.status != "REVIEW_REQUIRED":
        raise HTTPException(status_code=409, detail="Invoice conversion requires a currently approved milestone and a successfully extracted contract")
    approval = session.scalar(select(ReviewEvent).where(
        ReviewEvent.milestone_id == row.id, ReviewEvent.revision == row.revision,
        ReviewEvent.action == "APPROVED",
    ))
    if approval is None or approval.after != snapshot(row):
        raise HTTPException(status_code=409, detail="The current milestone does not match its recorded approval")
    amount = row.amount
    if row.currency != "USD" or row.contract.extraction["data"]["currency"]["value"] != "USD":
        raise HTTPException(status_code=422, detail="Milestone invoice conversion supports USD only")
    if not isinstance(amount, Decimal) or not amount.is_finite() or not Decimal("0") < amount <= Decimal("1000") or amount != amount.quantize(Decimal("0.01")):
        raise HTTPException(status_code=422, detail="Sandbox invoice amount must be positive, at most USD 1000 and use at most two decimal places")
    if not row.name or not 3 <= len(row.name) <= 120 or not row.payment_conditions or not row.deliverables or not row.acceptance_criteria:
        raise HTTPException(status_code=422, detail="Approved milestone fields are incomplete or exceed sandbox invoice limits")
    totals = summary(row.contract)
    if totals["declared_total"] is None or totals["milestone_total"] is None or totals["approved_total"] is None:
        raise HTTPException(status_code=409, detail="Resolve unknown contract amounts or conflicting currencies before invoicing")
    discrepancies = list(totals["discrepancies"])
    if totals["approved_total"] != totals["declared_total"]:
        discrepancies.append("APPROVED_TOTAL_DIFFERS_FROM_CONTRACT")
    data = {
        "contract_id": row.contract_id, "milestone_id": row.id,
        "milestone_revision": row.revision, "contract_review_revision": row.contract.review_revision,
        "name": row.name, "description": row.description or row.payment_conditions,
        "amount": format(amount, ".2f"), "currency": row.currency,
        "declared_total": format(totals["declared_total"], ".2f"),
        "approved_total": format(totals["approved_total"], ".2f"),
        "discrepancies": sorted(set(discrepancies)), "review_flags": review_flags(row),
        "recipient_email": paypal.recipient_email,
        "provider_mode": "mocked_paypal_sandbox" if getattr(paypal, "mocked", False) else "paypal_sandbox",
    }
    data["preview_digest"] = digest(data)
    data["existing_link_id"] = session.scalar(select(InvoiceLink.id).where(InvoiceLink.milestone_id == row.id))
    return data


def validate_create(session: Session, row, payload: CreateMilestoneInvoice, paypal: PayPalSandbox) -> dict:
    if str(payload.contract_id) != row.contract_id:
        raise HTTPException(status_code=409, detail="Milestone does not belong to the supplied contract")
    if row.revision != payload.expected_revision or row.contract.review_revision != payload.expected_contract_revision:
        raise HTTPException(status_code=409, detail="Stale approval context; retrieve a fresh invoice preview")
    value = preview(session, row, paypal)
    if value["preview_digest"] != payload.preview_digest:
        raise HTTPException(status_code=409, detail="Invoice preview has changed; retrieve and review the current preview")
    if value["discrepancies"] and (not payload.acknowledge_discrepancies or not payload.discrepancy_note):
        raise HTTPException(status_code=409, detail={"message": "Invoice discrepancies require explicit acknowledgement and a discrepancy_note", "discrepancies": value["discrepancies"]})
    return value


def expected_provider_facts(link: InvoiceLink) -> dict:
    value = link.snapshot["preview"]
    return {"invoice_number": link.invoice_number, "name": value["name"],
            "description": value["description"], "amount": link.amount,
            **({"recipient_email": value["recipient_email"]} if value.get("recipient_email") else {})}


def update_link(session: Session, link: InvoiceLink, payload, action: str, changes: dict, data: dict) -> dict:
    baseline, link_id, milestone_id = link.revision, link.id, link.milestone_id
    changed = session.execute(update(InvoiceLink).where(InvoiceLink.id == link_id, InvoiceLink.revision == baseline)
                              .values(**changes, revision=baseline + 1, updated_at=utc_now()))
    if changed.rowcount != 1:
        session.rollback()
        raise HTTPException(status_code=409, detail="Invoice attempt changed during the provider read; retrieve its current revision")
    event(session, milestone_id, payload, action, data, link_id)
    session.commit()
    session.expire_all()
    return link_response(session.get(InvoiceLink, link_id))


def provider_error(exc: PayPalAPIError) -> dict:
    return {"provider": "paypal_sandbox", "operation": exc.operation,
            "error": exc.name, "upstream_status": exc.upstream_status, "debug_id": exc.debug_id}


def sync(session: Session, link: InvoiceLink, payload, paypal: PayPalSandbox, *, completed_action="created") -> dict:
    try:
        result = paypal.verify_milestone_invoice(link.invoice_id, **expected_provider_facts(link))
    except PayPalAPIError as exc:
        result = update_link(session, link, payload, "STATUS_SYNC_FAILED",
                             {"error_code": exc.name, "verified": False}, provider_error(exc))
        raise HTTPException(status_code=exc.http_status, detail={**provider_error(exc), "link_id": result["id"],
                            "invoice_id": result["invoice_id"], "completed_action": completed_action,
                            "message": "The invoice and any send attempt are saved; status verification failed. Refresh or reconcile instead of repeating a financial action"}) from None
    return update_link(session, link, payload, "STATUS_SYNCED",
                       {"paypal_status": result["status"], "recipient_view_url": result["recipient_view_url"],
                        "verified": True, "error_code": None, "last_checked_at": utc_now()},
                       {"invoice_id": link.invoice_id, "paypal_status": result["status"]})


@router.get("/{milestone_id}/invoice/preview", response_model=InvoicePreview)
def get_preview(milestone_id: UUID, session: Session = Depends(get_db), paypal: PayPalSandbox = Depends(get_paypal)):
    return preview(session, lookup(session, milestone_id), paypal)


@router.post("/{milestone_id}/invoice", response_model=InvoiceLinkRead, status_code=201)
def create_draft(milestone_id: UUID, payload: CreateMilestoneInvoice, response: Response,
                 session: Session = Depends(get_db), paypal: PayPalSandbox = Depends(get_paypal)):
    row = lookup(session, milestone_id)
    fingerprint = digest(payload.model_dump(mode="json"))
    existing = session.scalar(select(InvoiceLink).where(InvoiceLink.idempotency_key == str(payload.idempotency_key)))
    if existing:
        if existing.milestone_id != row.id or existing.request_fingerprint != fingerprint:
            raise HTTPException(status_code=409, detail="Idempotency key is already associated with a different request")
        if existing.state != "CREATED":
            raise HTTPException(status_code=409, detail={"message": "Creation is pending or uncertain; reconcile this attempt without another create POST", "link_id": existing.id, "state": existing.state})
        response.status_code = 200
        return link_response(existing)
    if session.scalar(select(InvoiceLink.id).where(InvoiceLink.milestone_id == row.id)):
        raise HTTPException(status_code=409, detail="This milestone already has a durable invoice attempt; changing keys or revisions cannot create another")
    try:
        value = validate_create(session, row, payload, paypal)
    except HTTPException as exc:
        event(session, row.id, payload, "VALIDATION_REJECTED", {"http_status": exc.status_code, "reason": exc.detail})
        session.commit()
        raise
    # Acquire the same contract write guard as review, then reserve before network.
    claimed = session.execute(update(Contract).where(
        Contract.id == row.contract_id, Contract.review_revision == payload.expected_contract_revision,
        Contract.status == "REVIEW_REQUIRED",
    ).values(review_revision=Contract.review_revision))
    if claimed.rowcount != 1:
        session.rollback()
        raise HTTPException(status_code=409, detail="Approval context changed before invoice reservation")
    identity = uuid4()
    link = InvoiceLink(
        id=str(identity), milestone_id=row.id, contract_id=row.contract_id,
        milestone_revision=row.revision, contract_review_revision=row.contract.review_revision,
        idempotency_key=str(payload.idempotency_key), request_fingerprint=fingerprint,
        invoice_number="SS-" + identity.hex[:22], amount=row.amount, currency=row.currency,
        snapshot={"preview": value, "approved_milestone": snapshot(row), "evidence": row.evidence,
                  "discrepancy_note": payload.discrepancy_note,
                  "acknowledge_discrepancies": payload.acknowledge_discrepancies},
    )
    session.add(link)
    try:
        session.flush()
        event(session, row.id, payload, "CREATE_REQUESTED", {"snapshot": link.snapshot, "invoice_number": link.invoice_number}, link.id)
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="A concurrent request reserved this milestone or idempotency key; retrieve the linked attempt") from None
    try:
        invoice_id = paypal.create_invoice(milestone=value["name"], description=value["description"], amount_usd=link.amount,
                                           request_id=link.id, invoice_number=link.invoice_number)
    except PayPalAPIError as exc:
        saved = update_link(session, link, payload, "CREATE_FAILED",
                            {"state": "RECONCILIATION_REQUIRED", "error_code": exc.name}, provider_error(exc))
        raise HTTPException(status_code=exc.http_status, detail={**provider_error(exc), "link_id": saved["id"],
                            "state": saved["state"], "message": "A durable attempt is saved. Reconcile by invoice number; do not retry creation"}) from None
    try:
        update_link(session, link, payload, "CREATE_SUCCEEDED", {"state": "CREATED", "invoice_id": invoice_id}, {"invoice_id": invoice_id})
    except SQLAlchemyError:
        session.rollback()
        raise HTTPException(status_code=503, detail={"message": "PayPal accepted creation but local recording failed; reconcile the saved attempt", "link_id": str(identity), "invoice_id": invoice_id, "completed_action": "created"}) from None
    link = session.get(InvoiceLink, str(identity))
    return sync(session, link, payload, paypal)


@router.get("/{milestone_id}/invoice", response_model=InvoiceLinkRead)
def get_linked_invoice(milestone_id: UUID, session: Session = Depends(get_db)):
    return link_response(get_link(session, milestone_id))


def check_link_revision(link: InvoiceLink, payload: InvoiceAction):
    if payload.expected_link_revision != link.revision:
        raise HTTPException(status_code=409, detail="Stale invoice link revision; retrieve its current state")


@router.post("/{milestone_id}/invoice/sync", response_model=InvoiceLinkRead)
def sync_invoice(milestone_id: UUID, payload: InvoiceAction,
                 session: Session = Depends(get_db), paypal: PayPalSandbox = Depends(get_paypal)):
    link = get_link(session, milestone_id)
    check_link_revision(link, payload)
    if not link.invoice_id:
        raise HTTPException(status_code=409, detail="The invoice ID is unknown; use read-only reconciliation")
    return sync(session, link, payload, paypal)


@router.post("/{milestone_id}/invoice/send", response_model=InvoiceLinkRead)
def send_linked_invoice(milestone_id: UUID, payload: SendMilestoneInvoice,
                        session: Session = Depends(get_db), paypal: PayPalSandbox = Depends(get_paypal)):
    link = get_link(session, milestone_id)
    check_link_revision(link, payload)
    row = lookup(session, milestone_id)
    if (link.state != "CREATED" or not link.invoice_id or link.invoice_id != payload.expected_invoice_id
            or not link.verified or link.paypal_status != "DRAFT" or row.status != "APPROVED"
            or row.revision != link.milestone_revision or snapshot(row) != link.snapshot["approved_milestone"]):
        raise HTTPException(409, "Sending requires the current verified draft and its unchanged approved milestone")
    recipient = link.snapshot.get("preview", {}).get("recipient_email")
    if not recipient or recipient.lower() != paypal.recipient_email.lower():
        raise HTTPException(409, "The saved recipient must match the current sandbox configuration; legacy drafts without a captured recipient cannot be sent here")
    if session.scalar(select(InvoiceEvent.id).where(InvoiceEvent.link_id == link.id, InvoiceEvent.action == "SEND_REQUESTED")):
        raise HTTPException(409, "A send attempt already exists. Refresh status or reconcile; do not send again")
    # The revision CAS serializes concurrent sends. Commit intent BEFORE any provider
    # request; an ambiguous send never grants permission for another email POST.
    update_link(session, link, payload, "SEND_REQUESTED", {}, {"invoice_id": link.invoice_id, "recipient_email": recipient})
    session.refresh(link)
    try:
        paypal.send_milestone_invoice(link.invoice_id, **expected_provider_facts(link))
    except PayPalAPIError as exc:
        saved = update_link(session, link, payload, "SEND_UNCERTAIN", {"verified": False, "error_code": exc.name}, provider_error(exc))
        raise HTTPException(exc.http_status, detail={**provider_error(exc), "message": "The send attempt is saved. Refresh status or reconcile; do not retry sending",
            "invoice_id": saved["invoice_id"], "link_id": saved["id"]}) from None
    update_link(session, link, payload, "SEND_ACCEPTED", {"verified": False}, {"invoice_id": link.invoice_id})
    session.refresh(link)
    return sync(session, link, payload, paypal, completed_action="send_accepted")


@router.post("/{milestone_id}/invoice/reconcile", response_model=InvoiceLinkRead)
def reconcile_invoice(milestone_id: UUID, payload: ReconcileInvoice,
                      session: Session = Depends(get_db), paypal: PayPalSandbox = Depends(get_paypal)):
    link = get_link(session, milestone_id)
    check_link_revision(link, payload)
    if link.state == "CREATING" and link.created_at > utc_now() - timedelta(minutes=5):
        raise HTTPException(status_code=409, detail="Creation may still be running; retrieve the attempt and reconcile after its five-minute safety window")
    if link.invoice_id:
        if payload.invoice_id and payload.invoice_id != link.invoice_id:
            raise HTTPException(status_code=409, detail="An existing invoice association cannot be replaced")
        return sync(session, link, payload, paypal)
    try:
        expected = expected_provider_facts(link)
        result = (paypal.verify_milestone_invoice(payload.invoice_id, **expected) if payload.invoice_id
                  else paypal.find_milestone_invoice(**expected))
    except PayPalAPIError as exc:
        saved = update_link(session, link, payload, "RECONCILIATION_FAILED", {"error_code": exc.name}, provider_error(exc))
        raise HTTPException(status_code=exc.http_status, detail={**provider_error(exc), "link_id": saved["id"]}) from None
    if result is None:
        return update_link(session, link, payload, "RECONCILIATION_NOT_FOUND",
                           {"state": "RECONCILIATION_REQUIRED", "error_code": "INVOICE_NOT_FOUND"},
                           {"message": "No exact match found; this does not prove absence or permit another create request"})
    try:
        return update_link(session, link, payload, "RECONCILED",
                           {"state": "CREATED", "invoice_id": result["invoice_id"], "paypal_status": result["status"],
                            "verified": True, "error_code": None, "last_checked_at": utc_now(),
                            "recipient_view_url": result["recipient_view_url"]},
                           {"invoice_id": result["invoice_id"], "paypal_status": result["status"]})
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="The returned PayPal invoice is already associated with another milestone") from None


@router.get("/{milestone_id}/invoice/history", response_model=list[InvoiceEventRead])
def invoice_history(milestone_id: UUID, session: Session = Depends(get_db)):
    lookup(session, milestone_id)
    rows = sorted(session.scalars(select(InvoiceEvent).where(InvoiceEvent.milestone_id == str(milestone_id))),
                  key=lambda item: (item.created_at, item.data.get("link_revision", 0), item.id))
    return [{field: getattr(row, field) for field in ("id", "milestone_id", "link_id", "action", "reviewer_label", "note", "data")}
            | {"created_at": row.created_at.replace(tzinfo=timezone.utc)} for row in rows]
