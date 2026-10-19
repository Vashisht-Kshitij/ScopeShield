"""Persistent scope requests, reviewed draft orders, and append-only API history."""
from alembic import op
import sqlalchemy as sa

revision = "0004_scope_changes"
down_revision = "0003_invoice_links"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "change_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("contract_id", sa.String(36), sa.ForeignKey("contracts.id"), nullable=False),
        sa.Column("contract_sha256", sa.String(64), nullable=False),
        sa.Column("contract_text_sha256", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("requested_changes", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("analysis_revision", sa.Integer(), nullable=True),
        sa.Column("analysis", sa.JSON(), nullable=True),
        sa.Column("original_ai_output", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(60), nullable=True),
        sa.Column("processing_token", sa.String(36), nullable=True),
        sa.Column("processing_started_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("reviewer_label", sa.String(100), nullable=True),
        sa.CheckConstraint("status IN ('PENDING', 'ANALYZING', 'REVIEW_REQUIRED', 'APPROVED', 'REJECTED', 'CANCELLED')", name="change_request_status"),
        sa.CheckConstraint("revision >= 1", name="change_request_revision"),
    )
    money = sa.String(40) if op.get_bind().dialect.name == "sqlite" else sa.Numeric(18, 2)
    op.create_table(
        "change_orders",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("change_request_id", sa.String(36), sa.ForeignKey("change_requests.id"), nullable=False),
        sa.Column("analysis_revision", sa.Integer(), nullable=False),
        sa.Column("proposed_deliverables", sa.JSON(), nullable=False),
        sa.Column("proposed_price", money, nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("proposed_deadline", sa.Date(), nullable=True),
        sa.Column("supporting_evidence", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("reviewer_label", sa.String(100), nullable=True),
        sa.UniqueConstraint("change_request_id", name="change_order_request"),
        sa.CheckConstraint("status IN ('REVIEW_REQUIRED', 'APPROVED', 'REJECTED')", name="change_order_status"),
        sa.CheckConstraint("revision >= 1", name="change_order_revision"),
    )
    op.create_table(
        "scope_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("change_request_id", sa.String(36), sa.ForeignKey("change_requests.id"), nullable=False),
        sa.Column("change_order_id", sa.String(36), sa.ForeignKey("change_orders.id"), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("reviewer_label", sa.String(100), nullable=False),
        sa.Column("note", sa.String(1000), nullable=False),
        sa.Column("before", sa.JSON(), nullable=False),
        sa.Column("after", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("change_request_id", "revision", name="scope_event_revision"),
    )
    for table, columns in {"change_requests": ("project_id", "contract_id"),
                           "change_orders": ("change_request_id",),
                           "scope_events": ("change_request_id", "change_order_id")}.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade():
    if any(op.get_bind().scalar(sa.text(f"SELECT COUNT(*) FROM {table}"))
           for table in ("scope_events", "change_orders", "change_requests")):
        raise RuntimeError("Cannot downgrade while scope requests, orders or audit history exist")
    op.drop_table("scope_events")
    op.drop_table("change_orders")
    op.drop_table("change_requests")
