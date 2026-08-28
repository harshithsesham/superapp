"""pgvector semantic memory (north star step 4). Postgres-only.

Revision ID: 0009
Revises: 0008
"""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return  # SQLite dev/test: semantic memory simply recalls nothing
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("""
        CREATE TABLE memory_chunks (
            id BIGSERIAL PRIMARY KEY,
            user_id VARCHAR(64) NOT NULL,
            domain VARCHAR(32) NOT NULL,
            kind VARCHAR(32) NOT NULL,
            ref_id VARCHAR(64) NOT NULL,
            content TEXT NOT NULL,
            embedding vector(1024) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (user_id, kind, ref_id)
        )
    """)
    op.execute("CREATE INDEX ix_memory_user ON memory_chunks (user_id, domain)")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP TABLE memory_chunks")
