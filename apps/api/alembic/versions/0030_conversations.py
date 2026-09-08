"""durable conversations: every exchange with Nano, on every surface

Revision ID: 0030
Revises: 0029

Idempotent (create_all races alembic on fresh deploys).
"""
import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "conversations" in insp.get_table_names():
        return
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("surface", sa.String(16), nullable=False),
        sa.Column("external_id", sa.String(64), nullable=False),
        sa.Column("turns", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_turn_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_convo_lookup", "conversations",
                    ["user_id", "surface", "external_id"])
    op.create_index("ix_convo_sweep", "conversations", ["settled_at", "last_turn_at"])


def downgrade() -> None:
    op.drop_index("ix_convo_sweep", table_name="conversations")
    op.drop_index("ix_convo_lookup", table_name="conversations")
    op.drop_table("conversations")
