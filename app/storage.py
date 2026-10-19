"""Persistent projects, uploaded text, processing state, and unapproved milestones."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, JSON, LargeBinary, String, Text, UniqueConstraint, false, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, ExactMoney


def new_id() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    # Database timestamps use UTC consistently, including SQLite's naive datetimes.
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (CheckConstraint("status IN ('DRAFT', 'REVIEW_REQUIRED')", name="project_status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    # NULL means a pre-authentication record quarantined until a local operator assigns it.
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str | None] = mapped_column(String(200))
    currency: Mapped[str | None] = mapped_column(String(3))
    total_value: Mapped[Decimal | None] = mapped_column(ExactMoney())
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now)
    contracts: Mapped[list[Contract]] = relationship(back_populates="project")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('freelancer', 'client')", name="user_role"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now)


class LoginLimit(Base):
    __tablename__ = "login_limits"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer())
    reset_at: Mapped[datetime] = mapped_column(DateTime())


class ResourceCounter(Base):
    __tablename__ = "resource_counters"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    used: Mapped[int] = mapped_column(Integer())
    reset_at: Mapped[datetime] = mapped_column(DateTime(), index=True)


class AIUsage(Base):
    __tablename__ = "ai_usage"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    model: Mapped[str] = mapped_column(String(100))
    reserved_input: Mapped[int] = mapped_column(Integer())
    reserved_output: Mapped[int] = mapped_column(Integer())
    reported_usage: Mapped[dict | None] = mapped_column(JSON())
    outcome: Mapped[str] = mapped_column(String(20))
    error_code: Mapped[str | None] = mapped_column(String(64))
    http_status: Mapped[int | None] = mapped_column(Integer())
    created_at: Mapped[datetime] = mapped_column(DateTime())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime())


class ProjectShare(Base):
    __tablename__ = "project_shares"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now)


class ManualInvoiceOwner(Base):
    """Ownership receipts for the historical manual sandbox API; no retry permission."""
    __tablename__ = "manual_invoice_owners"
    invoice_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now)


class ClientDecision(Base):
    __tablename__ = "client_decisions"
    __table_args__ = (UniqueConstraint("order_id", "client_id", "order_revision", name="client_order_response"),
                      CheckConstraint("decision IN ('APPROVED', 'REJECTED')", name="client_decision"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(ForeignKey("change_orders.id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    order_revision: Mapped[int] = mapped_column(Integer())
    decision: Mapped[str] = mapped_column(String(20))
    note: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now)


class SecurityEvent(Base):
    __tablename__ = "security_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(50))
    resource_id: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now)


class Contract(Base):
    __tablename__ = "contracts"
    __table_args__ = (CheckConstraint("status IN ('UPLOADED', 'PROCESSING', 'REVIEW_REQUIRED', 'FAILED')", name="contract_status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    filename: Mapped[str] = mapped_column(String(180))
    media_type: Mapped[str] = mapped_column(String(30))
    size_bytes: Mapped[int] = mapped_column(Integer())
    sha256: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text())
    page_count: Mapped[int | None] = mapped_column(Integer())
    status: Mapped[str] = mapped_column(String(20), default="UPLOADED")
    processing_token: Mapped[str | None] = mapped_column(String(36))
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime())
    error_code: Mapped[str | None] = mapped_column(String(60))
    extraction: Mapped[dict | None] = mapped_column(JSON())
    original_ai_output: Mapped[dict | None] = mapped_column(JSON())
    original_output_kind: Mapped[str | None] = mapped_column(String(20))
    review_revision: Mapped[int] = mapped_column(Integer(), default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, onupdate=utc_now)
    project: Mapped[Project] = relationship(back_populates="contracts")
    document: Mapped[ContractDocument | None] = relationship(cascade="all, delete-orphan", uselist=False)
    milestones: Mapped[list[Milestone]] = relationship(
        back_populates="contract", cascade="all, delete-orphan", order_by="Milestone.ordinal",
    )


class ContractDocument(Base):
    """Private, byte-exact accepted PDF. BLOB is never loaded by ordinary reads."""
    __tablename__ = "contract_documents"
    __table_args__ = (CheckConstraint("length(data) > 0 AND length(data) <= 5242880", name="pdf_original_size"),)
    contract_id: Mapped[str] = mapped_column(ForeignKey("contracts.id"), primary_key=True)
    data: Mapped[bytes] = mapped_column(LargeBinary(), deferred=True)
    provenance: Mapped[dict | None] = mapped_column(JSON())


class Milestone(Base):
    __tablename__ = "milestones"
    __table_args__ = (
        UniqueConstraint("contract_id", "ordinal", name="milestone_order"),
        CheckConstraint("status IN ('DRAFT', 'REVIEW_REQUIRED', 'APPROVED', 'REJECTED')", name="milestone_status"),
        CheckConstraint("revision >= 1", name="milestone_revision"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    contract_id: Mapped[str] = mapped_column(ForeignKey("contracts.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer())
    name: Mapped[str | None] = mapped_column(String(200))
    amount: Mapped[Decimal | None] = mapped_column(ExactMoney())
    currency: Mapped[str | None] = mapped_column(String(3))
    payment_conditions: Mapped[str | None] = mapped_column(Text())
    deadline: Mapped[date | None] = mapped_column(Date())
    deliverables: Mapped[list] = mapped_column(JSON())
    acceptance_criteria: Mapped[list] = mapped_column(JSON())
    evidence: Mapped[dict] = mapped_column(JSON())
    status: Mapped[str] = mapped_column(String(20), default="REVIEW_REQUIRED")
    description: Mapped[str | None] = mapped_column(Text())
    revision: Mapped[int] = mapped_column(Integer(), default=1, server_default="1")
    has_human_edits: Mapped[bool] = mapped_column(default=False, server_default=false())
    updated_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, onupdate=utc_now, server_default=text("CURRENT_TIMESTAMP"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime())
    reviewer_label: Mapped[str | None] = mapped_column(String(100))
    contract: Mapped[Contract] = relationship(back_populates="milestones")


class ReviewEvent(Base):
    __tablename__ = "review_events"
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    __table_args__ = (
        UniqueConstraint("milestone_id", "revision", name="review_event_revision"),
        CheckConstraint("action IN ('EDITED', 'APPROVED', 'REJECTED', 'REOPENED')", name="review_action"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    contract_id: Mapped[str] = mapped_column(ForeignKey("contracts.id"), index=True)
    milestone_id: Mapped[str] = mapped_column(ForeignKey("milestones.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer())
    contract_review_revision: Mapped[int] = mapped_column(Integer())
    action: Mapped[str] = mapped_column(String(20))
    reviewer_label: Mapped[str] = mapped_column(String(100))
    note: Mapped[str] = mapped_column(String(1000))
    before: Mapped[dict] = mapped_column(JSON())
    after: Mapped[dict] = mapped_column(JSON())
    acknowledged_issues: Mapped[list] = mapped_column(JSON())
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now)


class InvoiceLink(Base):
    """One durable creation attempt per milestone, including uncertain outcomes."""
    __tablename__ = "invoice_links"
    __table_args__ = (
        UniqueConstraint("milestone_id", name="invoice_link_milestone"),
        UniqueConstraint("idempotency_key", name="invoice_link_key"),
        UniqueConstraint("invoice_number", name="invoice_link_number"),
        UniqueConstraint("invoice_id", name="invoice_link_paypal_id"),
        CheckConstraint("state IN ('CREATING', 'CREATED', 'RECONCILIATION_REQUIRED')", name="invoice_link_state"),
        CheckConstraint("revision >= 1", name="invoice_link_revision"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    milestone_id: Mapped[str] = mapped_column(ForeignKey("milestones.id"), index=True)
    contract_id: Mapped[str] = mapped_column(ForeignKey("contracts.id"), index=True)
    milestone_revision: Mapped[int] = mapped_column(Integer())
    contract_review_revision: Mapped[int] = mapped_column(Integer())
    idempotency_key: Mapped[str] = mapped_column(String(36))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    invoice_number: Mapped[str] = mapped_column(String(25))
    snapshot: Mapped[dict] = mapped_column(JSON())
    amount: Mapped[Decimal] = mapped_column(ExactMoney())
    currency: Mapped[str] = mapped_column(String(3))
    state: Mapped[str] = mapped_column(String(30), default="CREATING")
    invoice_id: Mapped[str | None] = mapped_column(String(85))
    paypal_status: Mapped[str | None] = mapped_column(String(30))
    recipient_view_url: Mapped[str | None] = mapped_column(Text())
    verified: Mapped[bool] = mapped_column(default=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    revision: Mapped[int] = mapped_column(Integer(), default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, onupdate=utc_now)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime())


class InvoiceEvent(Base):
    __tablename__ = "invoice_events"
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    milestone_id: Mapped[str] = mapped_column(ForeignKey("milestones.id"), index=True)
    link_id: Mapped[str | None] = mapped_column(ForeignKey("invoice_links.id"), index=True)
    action: Mapped[str] = mapped_column(String(40))
    reviewer_label: Mapped[str] = mapped_column(String(100))
    note: Mapped[str] = mapped_column(String(1000))
    data: Mapped[dict] = mapped_column(JSON())
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now)


class ChangeRequest(Base):
    """An immutable contract version reference and revisioned scope proposal."""
    __tablename__ = "change_requests"
    __table_args__ = (
        CheckConstraint("status IN ('PENDING', 'ANALYZING', 'REVIEW_REQUIRED', 'APPROVED', 'REJECTED', 'CANCELLED')", name="change_request_status"),
        CheckConstraint("revision >= 1", name="change_request_revision"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    contract_id: Mapped[str] = mapped_column(ForeignKey("contracts.id"), index=True)
    contract_sha256: Mapped[str] = mapped_column(String(64))
    contract_text_sha256: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200))
    requested_changes: Mapped[list] = mapped_column(JSON())
    conversation_messages: Mapped[list | None] = mapped_column(JSON())
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    revision: Mapped[int] = mapped_column(Integer(), default=1)
    analysis_revision: Mapped[int | None] = mapped_column(Integer())
    analysis: Mapped[dict | None] = mapped_column(JSON())
    original_ai_output: Mapped[dict | None] = mapped_column(JSON())
    error_code: Mapped[str | None] = mapped_column(String(60))
    processing_token: Mapped[str | None] = mapped_column(String(36))
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime())
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime())
    reviewer_label: Mapped[str | None] = mapped_column(String(100))


class ChangeOrder(Base):
    __tablename__ = "change_orders"
    __table_args__ = (
        UniqueConstraint("change_request_id", name="change_order_request"),
        CheckConstraint("status IN ('REVIEW_REQUIRED', 'APPROVED', 'REJECTED')", name="change_order_status"),
        CheckConstraint("revision >= 1", name="change_order_revision"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    change_request_id: Mapped[str] = mapped_column(ForeignKey("change_requests.id"), index=True)
    analysis_revision: Mapped[int] = mapped_column(Integer())
    proposed_deliverables: Mapped[list] = mapped_column(JSON())
    proposed_price: Mapped[Decimal | None] = mapped_column(ExactMoney())
    currency: Mapped[str | None] = mapped_column(String(3))
    proposed_deadline: Mapped[date | None] = mapped_column(Date())
    supporting_evidence: Mapped[list] = mapped_column(JSON())
    status: Mapped[str] = mapped_column(String(20), default="REVIEW_REQUIRED")
    revision: Mapped[int] = mapped_column(Integer(), default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime())
    reviewer_label: Mapped[str | None] = mapped_column(String(100))


class ScopeEvent(Base):
    __tablename__ = "scope_events"
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    __table_args__ = (UniqueConstraint("change_request_id", "revision", name="scope_event_revision"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    change_request_id: Mapped[str] = mapped_column(ForeignKey("change_requests.id"), index=True)
    change_order_id: Mapped[str | None] = mapped_column(ForeignKey("change_orders.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer())
    action: Mapped[str] = mapped_column(String(40))
    reviewer_label: Mapped[str] = mapped_column(String(100))
    note: Mapped[str] = mapped_column(String(1000))
    before: Mapped[dict] = mapped_column(JSON())
    after: Mapped[dict] = mapped_column(JSON())
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now)


class ContractEvent(Base):
    __tablename__ = "contract_events"
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    contract_id: Mapped[str] = mapped_column(ForeignKey("contracts.id"), index=True)
    action: Mapped[str] = mapped_column(String(40))
    data: Mapped[dict] = mapped_column(JSON())
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now)
