"""Wardrobe twins: wardrobe_garments, outfit_suggestions.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wardrobe_garments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("photo_id", sa.String(80), nullable=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("brand", sa.String(128), nullable=True),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("primary_color", sa.String(32), nullable=False),
        sa.Column("secondary_color", sa.String(32), nullable=True),
        sa.Column("pattern", sa.String(32), nullable=False),
        sa.Column("material", sa.String(48), nullable=True),
        sa.Column("formality", sa.String(24), nullable=False),
        sa.Column("seasons", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_garments_user", "wardrobe_garments", ["user_id", "created_at"])
    op.create_table(
        "outfit_suggestions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("title", sa.String(128), nullable=False),
        sa.Column("occasion", sa.String(64), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_outfits_user_day", "outfit_suggestions", ["user_id", "day"])


def downgrade() -> None:
    op.drop_index("ix_outfits_user_day", table_name="outfit_suggestions")
    op.drop_table("outfit_suggestions")
    op.drop_index("ix_garments_user", table_name="wardrobe_garments")
    op.drop_table("wardrobe_garments")
