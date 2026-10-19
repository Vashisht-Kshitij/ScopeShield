"""Durable rate counters and nonrefundable AI attempt reservations."""
from alembic import op
import sqlalchemy as sa

revision = "0007_resource_controls"
down_revision = "0006_authentication"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("resource_counters", sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("used", sa.Integer(), nullable=False), sa.Column("reset_at", sa.DateTime(), nullable=False))
    op.create_index("ix_resource_counters_reset_at", "resource_counters", ["reset_at"])
    op.create_table("ai_usage", sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("reserved_input", sa.Integer(), nullable=False), sa.Column("reserved_output", sa.Integer(), nullable=False),
        sa.Column("reported_usage", sa.JSON()), sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("error_code", sa.String(64)), sa.Column("http_status", sa.Integer()),
        sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("completed_at", sa.DateTime()))
    op.create_index("ix_ai_usage_user_id", "ai_usage", ["user_id"])


def downgrade():
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT count(*) FROM users")):
        raise RuntimeError("Downgrade refused: accounts, ownership and resource usage must be preserved")
    if connection.scalar(sa.text("SELECT count(*) FROM ai_usage")) or connection.scalar(sa.text("SELECT count(*) FROM resource_counters")):
        raise RuntimeError("Downgrade refused: resource usage and admission history must be preserved")
    op.drop_table("ai_usage")
    op.drop_table("resource_counters")
