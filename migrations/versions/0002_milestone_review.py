"""Preserve extracted facts and add optimistic human reviews with an audit trail."""
from alembic import op
import sqlalchemy as sa

revision = "0002_milestone_review"
down_revision = "0001_contract_storage"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("contracts", sa.Column("original_ai_output", sa.JSON(), nullable=True))
    op.add_column("contracts", sa.Column("original_output_kind", sa.String(20), nullable=True))
    op.add_column("contracts", sa.Column("review_revision", sa.Integer(), nullable=False, server_default="0"))
    with op.batch_alter_table("milestones") as batch:
        batch.drop_constraint("milestone_status", type_="check")
        batch.create_check_constraint("milestone_status", "status IN ('DRAFT', 'REVIEW_REQUIRED', 'APPROVED', 'REJECTED')")
        batch.add_column(sa.Column("description", sa.Text(), nullable=True))
        batch.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("has_human_edits", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")))
        batch.add_column(sa.Column("reviewed_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("reviewer_label", sa.String(100), nullable=True))
        batch.create_check_constraint("milestone_revision", "revision >= 1")
    connection = op.get_bind()
    contracts = sa.table("contracts", sa.column("id", sa.String), sa.column("extraction", sa.JSON),
                         sa.column("original_ai_output", sa.JSON), sa.column("original_output_kind", sa.String))
    for row in connection.execute(sa.select(contracts.c.id, contracts.c.extraction)):
        if isinstance(row.extraction, dict) and isinstance(row.extraction.get("data"), dict):
            connection.execute(contracts.update().where(contracts.c.id == row.id).values(
                original_ai_output=row.extraction["data"], original_output_kind="legacy_validated"))
    op.create_table(
        "review_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("contract_id", sa.String(36), sa.ForeignKey("contracts.id"), nullable=False),
        sa.Column("milestone_id", sa.String(36), sa.ForeignKey("milestones.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("contract_review_revision", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("reviewer_label", sa.String(100), nullable=False),
        sa.Column("note", sa.String(1000), nullable=False),
        sa.Column("before", sa.JSON(), nullable=False),
        sa.Column("after", sa.JSON(), nullable=False),
        sa.Column("acknowledged_issues", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("milestone_id", "revision", name="review_event_revision"),
        sa.CheckConstraint("action IN ('EDITED', 'APPROVED', 'REJECTED', 'REOPENED')", name="review_action"),
    )
    op.create_index("ix_review_events_contract_id", "review_events", ["contract_id"])
    op.create_index("ix_review_events_milestone_id", "review_events", ["milestone_id"])


def downgrade():
    # Downgrading must not silently erase reviewed decisions or audit history.
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT COUNT(*) FROM review_events")):
        raise RuntimeError("Cannot downgrade while milestone review history exists")
    op.drop_table("review_events")
    with op.batch_alter_table("milestones") as batch:
        batch.drop_constraint("milestone_revision", type_="check")
        batch.drop_constraint("milestone_status", type_="check")
        batch.create_check_constraint("milestone_status", "status IN ('DRAFT', 'REVIEW_REQUIRED')")
        for column in ("description", "revision", "has_human_edits", "updated_at", "reviewed_at", "reviewer_label"):
            batch.drop_column(column)
    for column in ("original_ai_output", "original_output_kind", "review_revision"):
        op.drop_column("contracts", column)
