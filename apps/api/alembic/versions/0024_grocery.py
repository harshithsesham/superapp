"""The grocery vertical: a shelf, a purchase record, and baskets nobody placed yet.

Revision ID: 0024
Revises: 0023

Four tables, and the reason each is separate.

`grocery_items` is the shelf — what this household keeps. `grocery_purchases`
is the evidence — what was actually bought, and when. The forecast reads only
the second, because a shelf edited by hand is a wish and a receipt is a fact,
and mixing them makes "you run out of milk every six days" unfalsifiable.

`grocery_orders` exists as a row before any money moves. Assembling a basket
and paying for it are different acts with different risk: `grocery.place_order`
is tier 3, so Nano may do the first and never the second. The row carries
`confirmed_by`, and placing refuses without it.

`grocery_links` records connected platforms. Note what is NOT here: no payment
details, no card, no stored checkout credential. The vault holds platform
tokens under "grocery:{platform}" like every other secret.
"""
import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    have = set(insp.get_table_names())

    if "grocery_items" not in have:
        op.create_table(
            "grocery_items",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(64), nullable=False),
            sa.Column("slug", sa.String(120), nullable=False),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("category", sa.String(40), nullable=False, server_default=""),
            sa.Column("brand", sa.String(80), nullable=False, server_default=""),
            sa.Column("size", sa.String(40), nullable=False, server_default=""),
            sa.Column("unit", sa.String(24), nullable=False, server_default=""),
            sa.Column("image_ref", sa.String(200), nullable=False, server_default=""),
            sa.Column("on_list", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("declared_out_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_purchased_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("user_id", "slug", name="uq_grocery_item"),
        )
        op.create_index("ix_grocery_user", "grocery_items", ["user_id", "category"])

    if "grocery_purchases" not in have:
        op.create_table(
            "grocery_purchases",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(64), nullable=False),
            sa.Column("item_id", sa.String(36), nullable=False),
            sa.Column("source", sa.String(16), nullable=False, server_default="manual"),
            sa.Column("source_ref", sa.String(120), nullable=False, server_default=""),
            sa.Column("merchant", sa.String(80), nullable=False, server_default=""),
            sa.Column("quantity", sa.Float(), nullable=False, server_default="1"),
            sa.Column("unit_price_cents", sa.Integer(), nullable=True),
            sa.Column("purchased_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            # Re-reading the same receipt must not look like buying it twice;
            # a duplicated purchase halves the measured interval and puts a
            # stocked item on the red shelf.
            sa.UniqueConstraint("user_id", "source", "source_ref", "item_id",
                                name="uq_grocery_purchase"),
        )
        op.create_index("ix_grocery_purchase_item", "grocery_purchases",
                        ["user_id", "item_id", "purchased_at"])

    if "grocery_orders" not in have:
        op.create_table(
            "grocery_orders",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(64), nullable=False),
            sa.Column("platform", sa.String(24), nullable=False, server_default="list"),
            sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
            sa.Column("lines", sa.JSON(), nullable=True),
            sa.Column("subtotal_cents", sa.Integer(), nullable=True),
            sa.Column("reason", sa.String(300), nullable=False, server_default=""),
            sa.Column("external_id", sa.String(120), nullable=False, server_default=""),
            sa.Column("error", sa.String(300), nullable=False, server_default=""),
            sa.Column("confirmed_by", sa.String(8), nullable=False, server_default=""),
            sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("placed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_grocery_order_user", "grocery_orders", ["user_id", "created_at"])

    if "grocery_links" not in have:
        op.create_table(
            "grocery_links",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(64), nullable=False),
            sa.Column("platform", sa.String(24), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="linked"),
            sa.Column("account_label", sa.String(120), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("user_id", "platform", name="uq_grocery_link"),
        )


def downgrade() -> None:
    for t in ("grocery_links", "grocery_orders", "grocery_purchases", "grocery_items"):
        op.drop_table(t)
