"""fiber/sugar/sodium per meal (Cal Neo panel 2)

Revision ID: 0011
Revises: 0010
"""
import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("nutrition_meals") as b:
        b.add_column(sa.Column("fiber_g", sa.Float(), nullable=True))
        b.add_column(sa.Column("sugar_g", sa.Float(), nullable=True))
        b.add_column(sa.Column("sodium_mg", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("nutrition_meals") as b:
        b.drop_column("sodium_mg")
        b.drop_column("sugar_g")
        b.drop_column("fiber_g")
