"""Local users, revocable sessions, explicit ownership and client responses."""
from alembic import op
import sqlalchemy as sa

revision = "0006_authentication"
down_revision = "0005_contract_activity"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("username", sa.String(100), nullable=False, unique=True),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("password_hash", sa.String(300), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("role IN ('freelancer', 'client')", name="user_role"))
    # SQLite supports adding a nullable FK without rebuilding historical financial tables.
    if op.get_bind().dialect.name == "sqlite":
        op.execute("ALTER TABLE projects ADD COLUMN owner_id VARCHAR(36) REFERENCES users(id)")
    else:
        op.add_column("projects", sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id")))
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"])
    for table in ("contract_events", "review_events", "scope_events", "invoice_events"):
        if op.get_bind().dialect.name == "sqlite":
            op.execute(f"ALTER TABLE {table} ADD COLUMN actor_id VARCHAR(36) REFERENCES users(id)")
        else:
            op.add_column(table, sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id")))
    op.create_table("auth_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    op.create_table("login_limits", sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("attempts", sa.Integer(), nullable=False), sa.Column("reset_at", sa.DateTime(), nullable=False))
    op.create_table("project_shares",
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), primary_key=True),
        sa.Column("client_id", sa.String(36), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_table("manual_invoice_owners",
        sa.Column("invoice_id", sa.String(100), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_index("ix_manual_invoice_owners_owner_id", "manual_invoice_owners", ["owner_id"])
    op.create_table("client_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("order_id", sa.String(36), sa.ForeignKey("change_orders.id"), nullable=False),
        sa.Column("client_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("order_revision", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("note", sa.String(1000), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("order_id", "client_id", "order_revision", name="client_order_response"),
        sa.CheckConstraint("decision IN ('APPROVED', 'REJECTED')", name="client_decision"))
    op.create_index("ix_client_decisions_order_id", "client_decisions", ["order_id"])
    op.create_table("security_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("resource_id", sa.String(100)),
        sa.Column("created_at", sa.DateTime(), nullable=False))


def downgrade():
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT COUNT(*) FROM users")):
        raise RuntimeError("Refusing to discard accounts, ownership and authentication history")
    for name in ("security_events", "client_decisions", "manual_invoice_owners", "project_shares", "login_limits", "auth_sessions"):
        op.drop_table(name)
    op.drop_index("ix_projects_owner_id", "projects")
    op.drop_column("projects", "owner_id")
    for table in ("contract_events", "review_events", "scope_events", "invoice_events"):
        op.drop_column(table, "actor_id")
    op.drop_table("users")
