"""Google sign-in: users + auth_sessions.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-26
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("google_sub", sa.String(64), nullable=True),
        sa.Column("email", sa.String(128), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("google_sub", name="uq_user_google_sub"),
        sa.UniqueConstraint("email", name="uq_user_email"),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_sessions_user", "auth_sessions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_sessions_user", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_table("users")
