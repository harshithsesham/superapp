"""Purchases remember what size they were, and items can pin a real product.

Revision ID: 0025
Revises: 0024

Two gaps the first cut left.

Package size was thrown away, so buying a half-gallon instead of a gallon
looked like the same purchase and the household appeared to slow down. The
forecast now measures amounts, and it needs the amount.

`product_ref` holds a UPC or a store product id, resolved once. A handoff
basket built from names is a search engine guessing what "milk" means; built
from the UPC on this household's own receipts, it is the thing they buy.
"""
import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    cols = {c["name"] for c in insp.get_columns("grocery_purchases")}
    if "pack_amount" not in cols:
        op.add_column("grocery_purchases", sa.Column("pack_amount", sa.Float(), nullable=True))
    if "pack_unit" not in cols:
        op.add_column("grocery_purchases",
                      sa.Column("pack_unit", sa.String(8), nullable=False, server_default=""))

    icols = {c["name"] for c in insp.get_columns("grocery_items")}
    if "product_ref" not in icols:
        op.add_column("grocery_items",
                      sa.Column("product_ref", sa.String(64), nullable=False, server_default=""))
    if "product_ref_kind" not in icols:
        op.add_column("grocery_items",
                      sa.Column("product_ref_kind", sa.String(12), nullable=False, server_default=""))


def downgrade() -> None:
    for t, c in (("grocery_purchases", "pack_amount"), ("grocery_purchases", "pack_unit"),
                 ("grocery_items", "product_ref"), ("grocery_items", "product_ref_kind")):
        op.drop_column(t, c)
