"""Strict JSON contracts for provider output, reviewed evidence, and API responses."""
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

T = TypeVar("T")
TextValue = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
TitleValue = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
CurrencyValue = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]


def reject_float(value):
    if isinstance(value, (float, bool)):
        raise ValueError("Amounts must use decimal strings rather than floats")
    return value


MoneyValue = Annotated[
    Decimal, BeforeValidator(reject_float),
    Field(gt=Decimal("0"), le=Decimal("1000000000000"), decimal_places=2, allow_inf_nan=False),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(StrictModel, Generic[T]):
    value: T | None
    source_excerpt: str | None = Field(default=None, min_length=1, max_length=2000)
    source_start: int | None = Field(default=None, ge=0)
    source_end: int | None = Field(default=None, ge=0)


class TitleEvidence(Evidence[TitleValue]):
    pass


class TextEvidence(Evidence[TextValue]):
    pass


class MoneyEvidence(Evidence[MoneyValue]):
    pass


class CurrencyEvidence(Evidence[CurrencyValue]):
    pass


class DateEvidence(Evidence[date]):
    pass


class ExtractedMilestone(StrictModel):
    name: TitleEvidence
    amount: MoneyEvidence
    currency: CurrencyEvidence
    payment_conditions: TextEvidence
    deadline: DateEvidence
    deliverables: list[TextEvidence] = Field(max_length=50)
    acceptance_criteria: list[TextEvidence] = Field(max_length=50)


class ContractExtraction(StrictModel):
    project_title: TitleEvidence
    currency: CurrencyEvidence
    total_value: MoneyEvidence
    deliverables: list[TextEvidence] = Field(max_length=100)
    milestones: list[ExtractedMilestone] = Field(max_length=50)
    review_required_fields: list[Annotated[str, StringConstraints(min_length=1, max_length=200)]] = Field(default_factory=list, max_length=100)


class ReviewIssue(StrictModel):
    field: str
    code: str


class AIRequestAttempt(StrictModel):
    model: str = Field(pattern=r"^gemini-[a-zA-Z0-9.-]{1,70}$")
    response_model: str | None = None
    http_status: int | None = None
    elapsed_ms: int = Field(ge=0)
    error_code: str | None = None
    provider_status: str | None = None
    diagnostic_code: str | None = None
    token_usage: dict[str, int] | None = None


class ExtractionResult(StrictModel):
    provider: str
    model: str | None = None
    token_usage: dict[str, int] | None = None
    model_attempts: list[AIRequestAttempt] = Field(default_factory=list, max_length=3)
    mode: Literal["configured", "offline_demo"]
    live_ai: bool
    requires_human_review: Literal[True] = True
    data: ContractExtraction
    review_issues: list[ReviewIssue]
    milestone_total: Decimal | None
    totals_match: bool | None


class ExtractionRequest(StrictModel):
    mode: Literal["configured", "offline_demo"] = "configured"
    force: bool = False


class MilestoneRead(StrictModel):
    id: UUID
    ordinal: int
    name: str | None
    amount: Decimal | None
    currency: str | None
    payment_conditions: str | None
    deadline: date | None
    deliverables: list[str]
    acceptance_criteria: list[str]
    status: Literal["DRAFT", "REVIEW_REQUIRED", "APPROVED", "REJECTED"]
    evidence: ExtractedMilestone
    contract_id: UUID
    revision: int
    contract_review_revision: int
    has_human_edits: bool
    description: str | None
    updated_at: datetime
    reviewed_at: datetime | None
    reviewer_label: str | None


class ContractRead(StrictModel):
    original_pdf_available: bool = False
    pdf_provenance: dict | None = None
    ai_input_eligible: bool
    ai_input_message: str
    id: UUID
    project_id: UUID
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    text: str
    page_count: int | None
    status: Literal["UPLOADED", "PROCESSING", "REVIEW_REQUIRED", "FAILED"]
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    extraction: ExtractionResult | None
    milestones: list[MilestoneRead]
    review_revision: int
    original_ai_output: ContractExtraction | None
    original_output_kind: Literal["provider_raw", "legacy_validated"] | None


class ProjectRead(StrictModel):
    id: UUID
    title: str | None
    currency: str | None
    total_value: Decimal | None
    status: Literal["DRAFT", "REVIEW_REQUIRED"]
    created_at: datetime
    contract_ids: list[UUID]
