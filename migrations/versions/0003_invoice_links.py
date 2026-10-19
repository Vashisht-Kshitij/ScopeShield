"""Durable milestone invoice attempts and audit events; no provider operations."""
from alembic import op
import sqlalchemy as sa

revision = "0003_invoice_links"
down_revision = "0002_milestone_review"
branch_labels = None
depends_on = None


def upgrade():
    money_type = sa.String(40) if op.get_bind().dialect.name == "sqlite" else sa.Numeric(18, 2)
    op.create_table(
        "invoice_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("milestone_id", sa.String(36), sa.ForeignKey("milestones.id"), nullable=False),
        sa.Column("contract_id", sa.String(36), sa.ForeignKey("contracts.id"), nullable=False),
        sa.Column("milestone_revision", sa.Integer(), nullable=False),
        sa.Column("contract_review_revision", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(36), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("invoice_number", sa.String(25), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("amount", money_type, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("invoice_id", sa.String(85), nullable=True),
        sa.Column("paypal_status", sa.String(30), nullable=True),
        sa.Column("recipient_view_url", sa.Text(), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("milestone_id", name="invoice_link_milestone"),
        sa.UniqueConstraint("idempotency_key", name="invoice_link_key"),
        sa.UniqueConstraint("invoice_number", name="invoice_link_number"),
        sa.UniqueConstraint("invoice_id", name="invoice_link_paypal_id"),
        sa.CheckConstraint("state IN ('CREATING', 'CREATED', 'RECONCILIATION_REQUIRED')", name="invoice_link_state"),
        sa.CheckConstraint("revision >= 1", name="invoice_link_revision"),
    )
    for column in ("milestone_id", "contract_id"):
        op.create_index("ix_invoice_links_" + column, "invoice_links", [column])
    op.create_table(
        "invoice_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("milestone_id", sa.String(36), sa.ForeignKey("milestones.id"), nullable=False),
        sa.Column("link_id", sa.String(36), sa.ForeignKey("invoice_links.id"), nullable=True),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("reviewer_label", sa.String(100), nullable=False),
        sa.Column("note", sa.String(1000), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    for column in ("milestone_id", "link_id"):
        op.create_index("ix_invoice_events_" + column, "invoice_events", [column])


def downgrade():
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT COUNT(*) FROM invoice_links")) or connection.scalar(sa.text("SELECT COUNT(*) FROM invoice_events")):
        raise RuntimeError("Cannot downgrade while invoice attempts or audit history exist")
    op.drop_table("invoice_events")
    op.drop_table("invoice_links")
