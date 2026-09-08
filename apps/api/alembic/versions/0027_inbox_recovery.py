"""Persist full-sync recovery progress and expose incomplete inbox coverage."""
import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade():
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("gmail_accounts")}
    for column in (
        sa.Column("recovery_state", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("sync_error", sa.String(200), nullable=False, server_default=""),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
    ):
        if column.name not in columns:
            op.add_column("gmail_accounts", column)

    # Main's earlier provenance migration labelled pre-existing vectors "ok",
    # including possible hash stubs. Their origin cannot be reconstructed, so
    # re-index them conservatively once before allowing semantic decisions.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql" and "memory_chunks" in sa.inspect(bind).get_table_names():
        op.execute("ALTER TABLE memory_chunks ALTER COLUMN embed_status SET DEFAULT 'pending'")
        op.execute("UPDATE memory_chunks SET embed_status = 'pending' WHERE embed_status = 'ok'")


def downgrade():
    for name in ("last_sync_at", "sync_error", "recovery_state"):
        op.drop_column("gmail_accounts", name)
