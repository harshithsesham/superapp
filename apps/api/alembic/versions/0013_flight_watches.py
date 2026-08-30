"""flight watches (the Flycatcher) + task->watch link

Revision ID: 0013
Revises: 0012

Idempotent on purpose: the app's create_all can race alembic on a fresh
deploy and pre-create the table (it cannot add columns to existing tables),
so both steps check before acting.
"""
import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "flight_watches" not in insp.get_table_names():
        op.create_table(
            "flight_watches",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(64), nullable=False),
            sa.Column("instruction", sa.Text(), nullable=False),
            sa.Column("target_price", sa.Integer(), nullable=True),
            sa.Column("best_price", sa.Integer(), nullable=True),
            sa.Column("active", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "watch_id" not in [c["name"] for c in insp.get_columns("agent_tasks")]:
        op.add_column("agent_tasks", sa.Column("watch_id", sa.String(36), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_tasks", "watch_id")
    op.drop_table("flight_watches")
