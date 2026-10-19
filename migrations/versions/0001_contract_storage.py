"""Initial project, contract, and milestone storage."""
from alembic import op
import sqlalchemy as sa

from app.database import ExactMoney

revision = "0001_contract_storage"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("total_value", ExactMoney(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('DRAFT', 'REVIEW_REQUIRED')", name="project_status"),
    )
    op.create_table(
        "contracts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("filename", sa.String(180), nullable=False),
        sa.Column("media_type", sa.String(30), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("processing_token", sa.String(36), nullable=True),
        sa.Column("processing_started_at", sa.DateTime(), nullable=True),
        sa.Column("error_code", sa.String(60), nullable=True),
        sa.Column("extraction", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('UPLOADED', 'PROCESSING', 'REVIEW_REQUIRED', 'FAILED')", name="contract_status"),
    )
    op.create_index("ix_contracts_project_id", "contracts", ["project_id"])
    op.create_table(
        "milestones",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("contract_id", sa.String(36), sa.ForeignKey("contracts.id"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("amount", ExactMoney(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("payment_conditions", sa.Text(), nullable=True),
        sa.Column("deadline", sa.Date(), nullable=True),
        sa.Column("deliverables", sa.JSON(), nullable=False),
        sa.Column("acceptance_criteria", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.UniqueConstraint("contract_id", "ordinal", name="milestone_order"),
        sa.CheckConstraint("status IN ('DRAFT', 'REVIEW_REQUIRED')", name="milestone_status"),
    )
    op.create_index("ix_milestones_contract_id", "milestones", ["contract_id"])


def downgrade():
    op.drop_table("milestones")
    op.drop_table("contracts")
    op.drop_table("projects")
