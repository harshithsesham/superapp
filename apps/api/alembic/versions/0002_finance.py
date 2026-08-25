"""Finance twins: plaid_items, finance_accounts, finance_transactions.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-23
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plaid_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("item_id", sa.String(64), nullable=False),
        sa.Column("institution", sa.String(128), nullable=False),
        sa.Column("sync_cursor", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "item_id", name="uq_plaid_item"),
    )
    op.create_table(
        "finance_accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("plaid_account_id", sa.String(64), nullable=False),
        sa.Column("item_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("mask", sa.String(8), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "plaid_account_id", name="uq_fin_account"),
    )
    op.create_table(
        "finance_transactions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("plaid_txn_id", sa.String(64), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("merchant", sa.String(128), nullable=True),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("pending", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "plaid_txn_id", name="uq_fin_txn"),
    )
    op.create_index("ix_txns_user_date", "finance_transactions", ["user_id", "date"])


def downgrade() -> None:
    op.drop_index("ix_txns_user_date", table_name="finance_transactions")
    op.drop_table("finance_transactions")
    op.drop_table("finance_accounts")
    op.drop_table("plaid_items")
