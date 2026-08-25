"""Widen token_vault.provider — gmail:{email} providers exceed 32 chars.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-25
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("token_vault", "provider", type_=sa.String(64),
                    existing_type=sa.String(32), existing_nullable=False)


def downgrade() -> None:
    op.alter_column("token_vault", "provider", type_=sa.String(32),
                    existing_type=sa.String(64), existing_nullable=False)
