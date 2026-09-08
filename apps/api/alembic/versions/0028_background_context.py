"""Three-year history checkpoints and context saved through conversation."""
import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "history_import_state" not in {c["name"] for c in inspector.get_columns("gmail_accounts")}:
        op.add_column("gmail_accounts", sa.Column("history_import_state", sa.JSON(none_as_null=True), nullable=True))
    if "saved_context" not in inspector.get_table_names():
        op.create_table("saved_context",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(64), nullable=False),
            sa.Column("text", sa.Text, nullable=False),
            sa.Column("indexed", sa.Boolean, nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
        op.create_index("ix_saved_context_user", "saved_context", ["user_id", "created_at"])
    # Graph IDs exceed the original Gmail-only record's 32 characters.
    # SQLite ignores VARCHAR widths; PostgreSQL enforces them.
    if op.get_bind().dialect.name != "sqlite":
        op.alter_column("mail_history", "gmail_msg_id", type_=sa.String(512),
                        existing_type=sa.String(32), existing_nullable=False)
        op.alter_column("mail_history", "thread_id", type_=sa.String(256),
                        existing_type=sa.String(32), existing_nullable=False)


def downgrade():
    op.drop_table("saved_context")
    op.drop_column("gmail_accounts", "history_import_state")
