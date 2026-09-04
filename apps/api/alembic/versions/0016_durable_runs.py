"""Durable runs (Nano 2.0 Phase B): retries, leases and steps on agent_tasks,
a campaigns table for standing scout goals, and the injection tripwire flag
on inbox_messages.

Revision ID: 0016
Revises: 0015

Idempotent (create_all races alembic).
"""
import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())

    task_cols = [c["name"] for c in insp.get_columns("agent_tasks")]
    if "attempts" not in task_cols:
        op.add_column("agent_tasks",
                      sa.Column("attempts", sa.Integer, nullable=False,
                                server_default="0"))
    if "next_attempt_at" not in task_cols:
        op.add_column("agent_tasks",
                      sa.Column("next_attempt_at", sa.DateTime(timezone=True),
                                nullable=True))
    if "steps" not in task_cols:
        op.add_column("agent_tasks", sa.Column("steps", sa.JSON, nullable=True))
    if "campaign_id" not in task_cols:
        op.add_column("agent_tasks",
                      sa.Column("campaign_id", sa.String(36), nullable=True))

    msg_cols = [c["name"] for c in insp.get_columns("inbox_messages")]
    if "suspicious" not in msg_cols:
        op.add_column("inbox_messages",
                      sa.Column("suspicious", sa.Boolean, nullable=False,
                                server_default=sa.false()))

    if "campaigns" not in insp.get_table_names():
        op.create_table(
            "campaigns",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(64), nullable=False),
            sa.Column("goal", sa.Text, nullable=False),
            sa.Column("kind", sa.String(32), nullable=False,
                      server_default="research"),
            sa.Column("cadence_hours", sa.Integer, nullable=False,
                      server_default="24"),
            sa.Column("active", sa.Boolean, nullable=False,
                      server_default=sa.true()),
            sa.Column("state", sa.JSON, nullable=True),
            sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_campaigns_user", "campaigns", ["user_id", "active"])


def downgrade() -> None:
    op.drop_table("campaigns")
    op.drop_column("inbox_messages", "suspicious")
    op.drop_column("agent_tasks", "campaign_id")
    op.drop_column("agent_tasks", "steps")
    op.drop_column("agent_tasks", "next_attempt_at")
    op.drop_column("agent_tasks", "attempts")
