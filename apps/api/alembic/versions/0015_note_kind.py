"""note_kind: the recurring-stream label behind 'stop showing <kind>'

Revision ID: 0015
Revises: 0014

Idempotent (create_all races alembic).
"""
import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    cols = [c["name"] for c in insp.get_columns("inbox_messages")]
    if "note_kind" not in cols:
        op.add_column("inbox_messages",
                      sa.Column("note_kind", sa.String(120), nullable=False,
                                server_default=""))


def downgrade() -> None:
    op.drop_column("inbox_messages", "note_kind")
