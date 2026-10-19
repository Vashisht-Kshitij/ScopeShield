"""Preserve private original PDFs and physical-page text mapping for new uploads."""
from alembic import op
import sqlalchemy as sa

revision = "0008_pdf_provenance"
down_revision = "0007_resource_controls"
branch_labels = depends_on = None


def upgrade():
    op.create_table("contract_documents",
        sa.Column("contract_id", sa.String(36), sa.ForeignKey("contracts.id"), primary_key=True),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("provenance", sa.JSON()),
        sa.CheckConstraint("length(data) > 0 AND length(data) <= 5242880", name="pdf_original_size"))


def downgrade():
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT count(*) FROM contract_documents")):
        raise RuntimeError("Downgrade refused: original PDF documents must be preserved")
    # SQLite DDL can persist before a later migration refuses. Preflight existing
    # history here too, so a refused multi-step downgrade leaves the head usable.
    if any(connection.scalar(sa.text(f"SELECT count(*) FROM {table}"))
           for table in ("users", "ai_usage", "resource_counters")):
        raise RuntimeError("Downgrade refused: accounts, ownership and resource history must be preserved")
    op.drop_table("contract_documents")
