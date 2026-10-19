"""Persist extraction activity and preserve historical upload timestamps."""
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

revision = "0005_contract_activity"
down_revision = "0004_scope_changes"
branch_labels = None
depends_on = None


def upgrade():
    events = op.create_table(
        "contract_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("contract_id", sa.String(36), sa.ForeignKey("contracts.id"), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_contract_events_contract_id", "contract_events", ["contract_id"])
    # Only backfill an upload fact whose exact timestamp is already stored.
    # Older extraction times are unknown and are intentionally not fabricated.
    contracts = sa.table("contracts", sa.column("id", sa.String()), sa.column("filename", sa.String()),
                         sa.column("created_at", sa.DateTime()))
    connection = op.get_bind()
    for row in connection.execute(sa.select(contracts)).mappings():
        connection.execute(events.insert().values(
            id=str(uuid4()), contract_id=row["id"], action="CONTRACT_UPLOADED", created_at=row["created_at"],
            data={"filename": row["filename"], "provenance": "stored_upload_metadata"},
        ))


def downgrade():
    # Explicit operator downgrade removes this informational activity table;
    # original uploads, reviews, financial attempts and scope history remain.
    op.drop_table("contract_events")
