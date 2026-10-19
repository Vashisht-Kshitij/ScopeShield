"""Explicit invoice actions for the trusted local operator."""
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, StringConstraints, field_validator

from app.contract_schemas import StrictModel
from app.models import InvoiceID
from app.review_schemas import Label, Note, ReviewContext

Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


class CreateMilestoneInvoice(ReviewContext):
    contract_id: UUID
    idempotency_key: UUID
    preview_digest: Digest
    confirmed: StrictBool
    acknowledge_discrepancies: StrictBool = False
    discrepancy_note: Note | None = None

    @field_validator("confirmed")
    @classmethod
    def confirm(cls, value):
        if value is not True:
            raise ValueError("Invoice creation requires explicit confirmation")
        return value


class InvoiceAction(StrictModel):
    expected_link_revision: StrictInt = Field(ge=1)
    reviewer_label: Label
    note: Note


class ReconcileInvoice(InvoiceAction):
    invoice_id: InvoiceID | None = None


class SendConfirmation(StrictModel):
    confirmed: StrictBool

    @field_validator("confirmed")
    @classmethod
    def confirm(cls, value):
        if value is not True:
            raise ValueError("Sending requires a separate explicit confirmation")
        return value


class SendMilestoneInvoice(InvoiceAction, SendConfirmation):
    expected_invoice_id: InvoiceID


class InvoicePreview(StrictModel):
    environment: Literal["paypal_sandbox"] = "paypal_sandbox"
    contract_id: UUID
    milestone_id: UUID
    milestone_revision: int
    contract_review_revision: int
    name: str
    description: str
    amount: Decimal
    currency: Literal["USD"]
    declared_total: Decimal
    approved_total: Decimal
    discrepancies: list[str]
    review_flags: list[str]
    preview_digest: Digest
    existing_link_id: UUID | None
    recipient_email: str
    provider_mode: Literal["paypal_sandbox", "mocked_paypal_sandbox"] = "paypal_sandbox"


class InvoiceLinkRead(StrictModel):
    environment: Literal["paypal_sandbox"] = "paypal_sandbox"
    id: UUID
    milestone_id: UUID
    contract_id: UUID
    milestone_revision: int
    contract_review_revision: int
    invoice_number: str
    state: Literal["CREATING", "CREATED", "RECONCILIATION_REQUIRED"]
    invoice_id: InvoiceID | None
    paypal_status: str | None
    amount: Decimal
    currency: str
    snapshot: dict
    verified: bool
    error_code: str | None
    revision: int
    created_at: datetime
    updated_at: datetime
    last_checked_at: datetime | None
    recipient_view_url: str | None
    recipient_email: str | None = None
    provider_mode: Literal["paypal_sandbox", "mocked_paypal_sandbox"] = "paypal_sandbox"
    send_state: Literal["NOT_REQUESTED", "REQUESTED", "ACCEPTED", "UNCERTAIN"] = "NOT_REQUESTED"


class InvoiceEventRead(StrictModel):
    id: UUID
    milestone_id: UUID
    link_id: UUID | None
    action: str
    reviewer_label: str
    note: str
    data: dict
    created_at: datetime
