"""Full-text index for hybrid memory recall. Postgres-only.

Revision ID: 0010
Revises: 0009
"""
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("""
        CREATE INDEX ix_memory_fts ON memory_chunks
        USING gin (to_tsvector('english', content))
    """)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP INDEX ix_memory_fts")
