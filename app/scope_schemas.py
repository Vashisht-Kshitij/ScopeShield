"""Strict scope comparison and explicit local human review contracts."""
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, StringConstraints, field_validator, model_validator

from app.contract_schemas import AIRequestAttempt, MoneyValue, StrictModel, TextValue, TitleValue
from app.review_schemas import Label, Note, SUPPORTED_CURRENCIES
from app.conversation_schemas import ConversationMessage, ConversationMessageInput, MessageExcerpt, MessageAccounting, MAX_MESSAGES, ordered_messages

Classification = Literal["IN_SCOPE", "OUT_OF_SCOPE", "AMBIGUOUS"]
Work = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=2000)]


class SourceExcerpt(StrictModel):
    source_excerpt: TextValue
    relationship: Literal["COVERAGE", "EXCLUSION", "LIMIT", "CONTEXT", "CONFLICT"]
    source_start: StrictInt | None = Field(default=None, ge=0)
    source_end: StrictInt | None = Field(default=None, ge=0)


class ScopeFinding(StrictModel):
    ordinal: StrictInt = Field(ge=1, le=20)
    requested_work: Work
    classification: Classification
    explanation: TextValue
    contract_evidence: list[SourceExcerpt] = Field(max_length=10)
    uncertainties: list[TextValue] = Field(max_length=20)
    recommended_action: Literal["FULFILL_EXISTING_SCOPE", "DRAFT_CHANGE_ORDER", "CLARIFY_WITH_CLIENT"]
    requires_human_review: Literal[True]
    message_evidence: list[MessageExcerpt] = Field(default_factory=list, max_length=10)
    change_types: list[Literal["DELIVERABLE", "DEADLINE", "MAINTENANCE", "REVISIONS", "OTHER"]] = Field(default_factory=list, max_length=5)
    conversation_effect: Literal["INDIVIDUAL", "REPEATED", "CONFLICTING", "CUMULATIVE"] | None = None

    @field_validator("requires_human_review", mode="before")
    @classmethod
    def literal_true(cls, value):
        if value is not True:
            raise ValueError("Human review must be literal true")
        return value


class ScopeComparison(StrictModel):
    findings: list[ScopeFinding] = Field(min_length=1, max_length=20)


class ScopeAnalysis(ScopeComparison):
    provider: str
    model: str | None = None
    token_usage: dict[str, int] | None = None
    model_attempts: list[AIRequestAttempt] = Field(default_factory=list, max_length=3)
    mode: Literal["configured", "offline_demo"]
    live_ai: bool
    requires_human_review: Literal[True] = True
    review_flags: list[str]
    edited_by_human: bool = False
    conversation_sha256: str | None = None


class RequestSources(StrictModel):
    requested_changes: list[Work] | None = Field(default=None, min_length=1, max_length=20)
    conversation_messages: list[ConversationMessageInput] | None = Field(default=None, min_length=1, max_length=MAX_MESSAGES)
    _ordered = field_validator("conversation_messages")(ordered_messages)

    @model_validator(mode="after")
    def one_source_kind(self):
        if (self.requested_changes is None) == (self.conversation_messages is None):
            raise ValueError("Supply requested_changes OR conversation_messages, never both")
        return self

    @field_validator("requested_changes")
    @classmethod
    def unique_changes(cls, value):
        if value is not None and len(set(value)) != len(value):
            raise ValueError("Requested changes must be distinct")
        return value


class CreateChangeRequest(RequestSources):
    project_id: UUID
    title: TitleValue
    reviewer_label: Label
    note: Note


class ScopeContext(StrictModel):
    expected_revision: StrictInt = Field(ge=1)
    reviewer_label: Label
    note: Note


class EditChangeRequest(ScopeContext, RequestSources):
    title: TitleValue


class AnalyzeScope(ScopeContext):
    mode: Literal["configured", "offline_demo"] = "configured"


class EditScopeAnalysis(ScopeContext):
    analysis: ScopeComparison


class ScopeDecision(ScopeContext):
    confirmed: StrictBool
    acknowledge_review_flags: StrictBool = False

    @field_validator("confirmed")
    @classmethod
    def confirmed_true(cls, value):
        if value is not True:
            raise ValueError("Explicit confirmation is required")
        return value


class ChangeOrderTerms(StrictModel):
    proposed_deliverables: list[Work] = Field(min_length=1, max_length=20)
    proposed_price: MoneyValue | None = None
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] | None = None
    proposed_deadline: date | None = None

    @model_validator(mode="after")
    def explicit_terms(self):
        if (self.proposed_price is None) != (self.currency is None):
            raise ValueError("Supply both proposed_price and currency, or neither")
        if self.currency is not None and self.currency not in SUPPORTED_CURRENCIES:
            raise ValueError("Unsupported currency")
        if self.currency == "JPY" and self.proposed_price != self.proposed_price.quantize(1):
            raise ValueError("JPY requires whole currency units")
        return self


class PrepareChangeOrder(ScopeContext):
    terms: ChangeOrderTerms


class EditChangeOrder(PrepareChangeOrder):
    expected_order_revision: StrictInt = Field(ge=1)


class ChangeOrderDecision(ScopeDecision):
    expected_order_revision: StrictInt = Field(ge=1)


class ChangeRequestRead(StrictModel):
    id: UUID
    project_id: UUID
    contract_id: UUID
    contract_sha256: str
    contract_text_sha256: str
    title: str
    requested_changes: list[str]
    conversation_messages: list[ConversationMessage] | None = None
    message_accounting: list[MessageAccounting] = Field(default_factory=list, max_length=MAX_MESSAGES)
    status: Literal["PENDING", "ANALYZING", "REVIEW_REQUIRED", "APPROVED", "REJECTED", "CANCELLED"]
    revision: int
    analysis_revision: int | None
    analysis: ScopeAnalysis | None
    original_ai_output: ScopeComparison | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    reviewed_at: datetime | None
    reviewer_label: str | None
    change_order_id: UUID | None


class ChangeOrderRead(ChangeOrderTerms):
    id: UUID
    change_request_id: UUID
    project_id: UUID
    contract_id: UUID
    contract_sha256: str
    contract_text_sha256: str
    contract_filename: str
    analysis_revision: int
    supporting_evidence: list[SourceExcerpt]
    uncertainties: list[str]
    status: Literal["REVIEW_REQUIRED", "APPROVED", "REJECTED"]
    revision: int
    request_revision: int
    terms_origin: Literal["human_entered"] = "human_entered"
    requires_human_review: Literal[True] = True
    created_at: datetime
    updated_at: datetime
    reviewed_at: datetime | None
    reviewer_label: str | None


class ScopeEventRead(StrictModel):
    id: UUID
    change_request_id: UUID
    change_order_id: UUID | None
    revision: int
    action: str
    reviewer_label: str
    note: str
    before: dict
    after: dict
    created_at: datetime
