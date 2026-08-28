"""Semantic memory (north star step 4): pgvector recall over what happened.

Postgres-only by design — the memory_chunks table lives outside the ORM so
SQLite dev/test environments simply recall nothing. Embeddings come from
Voyage (voyage-3.5-lite, 1024 dims); without a key, a deterministic
hash-projection stub keeps the whole path exercisable offline.
"""
import hashlib
import json
import math

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from .config import get_settings

DIMS = 1024


def _stub_embed(texts: list[str]) -> list[list[float]]:
    out = []
    for t in texts:
        seed = hashlib.sha256(t.lower().encode()).digest()
        vec = []
        for i in range(DIMS):
            h = hashlib.sha256(seed + i.to_bytes(2, "big")).digest()
            vec.append(int.from_bytes(h[:4], "big") / 2**31 - 1.0)
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        out.append([v / norm for v in vec])
    return out


def embed(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    settings = get_settings()
    if not settings.voyage_api_key:
        return _stub_embed(texts)
    try:
        resp = httpx.post(
            "https://api.voyageai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {settings.voyage_api_key}"},
            json={"model": "voyage-3.5-lite", "input": texts[:128],
                  "output_dimension": DIMS},
            timeout=30,
        )
        resp.raise_for_status()
        return [d["embedding"] for d in resp.json()["data"]]
    except httpx.HTTPError:
        return _stub_embed(texts)


def _available(db: Session) -> bool:
    return db.get_bind().dialect.name == "postgresql"


def remember(db: Session, *, user_id: str, domain: str, kind: str,
             ref_id: str, content: str) -> None:
    """Store one memory chunk, embedded. Idempotent per (user, kind, ref_id)."""
    if not _available(db) or not content.strip():
        return
    vec = embed([content[:2000]])[0]
    db.execute(text("""
        INSERT INTO memory_chunks (user_id, domain, kind, ref_id, content, embedding)
        VALUES (:u, :d, :k, :r, :c, (:e)::vector)
        ON CONFLICT (user_id, kind, ref_id) DO UPDATE
          SET content = :c, embedding = (:e)::vector
    """), {"u": user_id, "d": domain, "k": kind, "r": ref_id,
           "c": content[:2000], "e": json.dumps(vec)})


def recall(db: Session, *, user_id: str, query: str, k: int = 5,
           domains: list[str] | None = None) -> list[dict]:
    """The k memories closest to the query, cosine distance."""
    if not _available(db) or not query.strip():
        return []
    vec = embed([query[:2000]])[0]
    domain_filter = "AND domain = ANY(:doms)" if domains else ""
    rows = db.execute(text(f"""
        SELECT domain, kind, content, created_at,
               1 - (embedding <=> (:e)::vector) AS similarity
        FROM memory_chunks
        WHERE user_id = :u {domain_filter}
        ORDER BY embedding <=> (:e)::vector
        LIMIT :k
    """), {"u": user_id, "e": json.dumps(vec), "k": k,
           **({"doms": domains} if domains else {})}).mappings()
    return [{"domain": r["domain"], "kind": r["kind"], "content": r["content"],
             "when": r["created_at"].isoformat() if r["created_at"] else "",
             "similarity": round(float(r["similarity"]), 3)} for r in rows]
