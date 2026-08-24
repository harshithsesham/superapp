"""Initial schema: substrate core (user_facts, events, token_vault) + nutrition twin.

Revision ID: 0001
Revises:
Create Date: 2026-08-23
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_facts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("domain", sa.String(32), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source_agent", sa.String(32), nullable=False),
        sa.Column("source_run_id", sa.String(36), nullable=True),
        sa.Column("learned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "domain", "key", name="uq_fact_identity"),
    )
    op.create_index("ix_facts_user_domain", "user_facts", ["user_id", "domain"])

    op.create_table(
        "events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("agent", sa.String(32), nullable=True),
        sa.Column("domain", sa.String(32), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_events_user_time", "events", ["user_id", "created_at"])
    op.create_index("ix_events_user_domain", "events", ["user_id", "domain"])

    op.create_table(
        "nutrition_meals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("logged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("photo_id", sa.String(80), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("kcal", sa.Integer(), nullable=True),
        sa.Column("protein_g", sa.Float(), nullable=True),
        sa.Column("carbs_g", sa.Float(), nullable=True),
        sa.Column("fat_g", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_meals_user_time", "nutrition_meals", ["user_id", "logged_at"])

    op.create_table(
        "token_vault",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "provider", name="uq_vault_identity"),
    )


def downgrade() -> None:
    op.drop_table("token_vault")
    op.drop_index("ix_meals_user_time", table_name="nutrition_meals")
    op.drop_table("nutrition_meals")
    op.drop_index("ix_events_user_domain", table_name="events")
    op.drop_index("ix_events_user_time", table_name="events")
    op.drop_table("events")
    op.drop_index("ix_facts_user_domain", table_name="user_facts")
    op.drop_table("user_facts")
