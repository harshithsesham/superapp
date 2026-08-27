"""decision ledger + autonomy grants (north star step 3)

Revision ID: 0008
Revises: 0007
"""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("agent", sa.String(32), nullable=False),
        sa.Column("action_key", sa.String(64), nullable=False),
        sa.Column("decided_by", sa.String(8), nullable=False),
        sa.Column("verdict", sa.String(16), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_decisions_user_key", "decisions",
                    ["user_id", "action_key", "created_at"])
    op.create_table(
        "autonomy_grants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("action_key", sa.String(64), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("granted_by", sa.String(8), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(128), nullable=False),
    )
    op.create_index("ix_grants_user_key", "autonomy_grants", ["user_id", "action_key"])


def downgrade() -> None:
    op.drop_index("ix_grants_user_key", table_name="autonomy_grants")
    op.drop_table("autonomy_grants")
    op.drop_index("ix_decisions_user_key", table_name="decisions")
    op.drop_table("decisions")
