"""A basket link is a URL, and 120 characters is not a URL.

Revision ID: 0026
Revises: 0025

`grocery_orders.external_id` holds a platform order id for a placed order and
a basket link for a handed-off one. Instacart's links exceed 120 characters, so
the column silently truncated them into dead links the person would tap.
"""
import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite does not enforce VARCHAR length, so this is a no-op there and the
    # real fix for Postgres. alter_column on SQLite rewrites the table, which
    # is why it is skipped rather than run for symmetry.
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column("grocery_orders", "external_id",
                        existing_type=sa.String(120), type_=sa.String(1024),
                        existing_nullable=False)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("UPDATE grocery_orders SET external_id = left(external_id, 120)")
        op.alter_column("grocery_orders", "external_id",
                        existing_type=sa.String(1024), type_=sa.String(120),
                        existing_nullable=False)
