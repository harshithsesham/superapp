"""agent task queue (the cloud scout)

Revision ID: 0012
Revises: 0011
"""
import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tasks_user", "agent_tasks", ["user_id", "created_at"])
    op.create_index("ix_tasks_status", "agent_tasks", ["status"])


def downgrade() -> None:
    op.drop_index("ix_tasks_status", table_name="agent_tasks")
    op.drop_index("ix_tasks_user", table_name="agent_tasks")
    op.drop_table("agent_tasks")
