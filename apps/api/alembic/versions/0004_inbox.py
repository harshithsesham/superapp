"""Inbox twins: gmail_accounts, inbox_messages, inbox_drafts.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-25
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gmail_accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("email", sa.String(128), nullable=False),
        sa.Column("history_id", sa.String(32), nullable=False),
        sa.Column("watch_expiry", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "email", name="uq_gmail_account"),
    )
    op.create_table(
        "inbox_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("account_email", sa.String(128), nullable=False),
        sa.Column("gmail_msg_id", sa.String(32), nullable=False),
        sa.Column("thread_id", sa.String(32), nullable=False),
        sa.Column("from_name", sa.String(128), nullable=False),
        sa.Column("from_addr", sa.String(128), nullable=False),
        sa.Column("subject", sa.String(256), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tier", sa.String(16), nullable=False),
        sa.Column("gist", sa.String(256), nullable=False),
        sa.Column("why_now", sa.String(128), nullable=False),
        sa.Column("clear_reason", sa.String(128), nullable=False),
        sa.Column("verified_clear", sa.Boolean(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("settled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "gmail_msg_id", name="uq_inbox_msg"),
    )
    op.create_index("ix_inbox_user_time", "inbox_messages", ["user_id", "received_at"])
    op.create_table(
        "inbox_drafts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("message_id", sa.String(36), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("defer_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_drafts_user_status", "inbox_drafts", ["user_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_drafts_user_status", table_name="inbox_drafts")
    op.drop_table("inbox_drafts")
    op.drop_index("ix_inbox_user_time", table_name="inbox_messages")
    op.drop_table("inbox_messages")
    op.drop_table("gmail_accounts")
