"""Receipt extraction checkpoints for live mail and historical imports."""
import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade():
    if "grocery_receipts" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table("grocery_receipts",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(64), nullable=False),
            sa.Column("source_ref", sa.String(512), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("user_id", "source_ref", name="uq_grocery_receipt"))
    if op.get_bind().dialect.name != "sqlite":
        op.alter_column("grocery_purchases", "source_ref", type_=sa.String(512),
                        existing_type=sa.String(120), existing_nullable=False)


def downgrade():
    op.drop_table("grocery_receipts")
