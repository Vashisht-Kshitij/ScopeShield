"""Add exact chronological message snapshots to the existing scope aggregate."""
from alembic import op
import sqlalchemy as sa

revision = "0009_conversations"
down_revision = "0008_pdf_provenance"
branch_labels = depends_on = None


def upgrade():
    op.add_column("change_requests", sa.Column("conversation_messages", sa.JSON(), nullable=True))


def downgrade():
    connection = op.get_bind()
    # Preflight before SQLite DDL: a refused multi-step downgrade must leave head usable.
    if connection.scalar(sa.text("SELECT count(*) FROM change_requests WHERE conversation_messages IS NOT NULL AND conversation_messages != 'null'")):
        raise RuntimeError("Downgrade refused: conversation messages and their evidence must be preserved")
    if connection.scalar(sa.text("SELECT count(*) FROM contract_documents")):
        raise RuntimeError("Downgrade refused: original PDF documents must be preserved")
    if any(connection.scalar(sa.text(f"SELECT count(*) FROM {table}")) for table in ("users", "ai_usage", "resource_counters")):
        raise RuntimeError("Downgrade refused: accounts, ownership and resource history must be preserved")
    op.drop_column("change_requests", "conversation_messages")
