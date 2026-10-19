"""Explicit, revision-checked review requests for the trusted local operator."""
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, StringConstraints, field_validator, model_validator

from app.contract_schemas import CurrencyValue, MilestoneRead, MoneyValue, StrictModel, TextValue, TitleValue

SUPPORTED_CURRENCIES = frozenset({"USD", "EUR", "GBP", "INR", "CAD", "AUD", "JPY", "CHF", "NZD", "SGD", "HKD"})
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=100)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)]


class ReviewContext(StrictModel):
    expected_revision: StrictInt = Field(ge=1)
    expected_contract_revision: StrictInt = Field(ge=0)
    reviewer_label: Label
    note: Note


class MilestoneChanges(StrictModel):
    name: TitleValue | None = None
    description: TextValue | None = None
    amount: MoneyValue | None = None
    currency: CurrencyValue | None = None
    payment_conditions: TextValue | None = None
    deadline: date | None = None
    deliverables: list[TextValue] = Field(default_factory=list, max_length=50)
    acceptance_criteria: list[TextValue] = Field(default_factory=list, max_length=50)

    @field_validator("currency")
    @classmethod
    def supported_currency(cls, value):
        if value is not None and value not in SUPPORTED_CURRENCIES:
            raise ValueError("Currency is not in the supported ISO currency set")
        return value

    @model_validator(mode="after")
    def at_least_one_change(self):
        if not self.model_fields_set:
            raise ValueError("Provide at least one milestone field to edit")
        return self


class MilestonePatch(ReviewContext):
    changes: MilestoneChanges


class ReviewDecision(ReviewContext):
    confirmed: StrictBool
    acknowledge_review_flags: StrictBool = False

    @field_validator("confirmed")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("The decision requires explicit confirmation")
        return value


class ReviewSummary(StrictModel):
    contract_id: UUID
    contract_review_revision: int
    currency: str | None
    declared_total: Decimal | None
    milestone_total: Decimal | None
    active_total: Decimal | None
    approved_total: Decimal | None
    totals_match: bool | None
    active_totals_match: bool | None
    counts: dict[str, int]
    discrepancies: list[str]


class ContractMilestones(StrictModel):
    summary: ReviewSummary
    milestones: list[MilestoneRead]


class ReviewEventRead(StrictModel):
    id: UUID
    contract_id: UUID
    milestone_id: UUID
    revision: int
    contract_review_revision: int
    action: str
    reviewer_label: str
    note: str
    before: dict
    after: dict
    acknowledged_issues: list[str]
    created_at: datetime
