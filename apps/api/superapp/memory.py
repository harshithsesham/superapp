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
    """Hybrid recall — the 2026 production baseline: dense vectors (paraphrase)
    + Postgres full-text (exact names, rare terms), fused with reciprocal-rank
    fusion. Two cheap queries, fused in code; no extra infrastructure."""
    if not _available(db) or not query.strip():
        return []
    domain_filter = "AND domain = ANY(:doms)" if domains else ""
    dom = {"doms": domains} if domains else {}

    vec = embed([query[:2000]])[0]
    dense = db.execute(text(f"""
        SELECT id, domain, kind, content, created_at
        FROM memory_chunks
        WHERE user_id = :u {domain_filter}
        ORDER BY embedding <=> (:e)::vector
        LIMIT 20
    """), {"u": user_id, "e": json.dumps(vec), **dom}).mappings().all()

    lexical = db.execute(text(f"""
        SELECT id, domain, kind, content, created_at
        FROM memory_chunks
        WHERE user_id = :u {domain_filter}
          AND to_tsvector('english', content) @@ plainto_tsquery('english', :q)
        ORDER BY ts_rank(to_tsvector('english', content),
                         plainto_tsquery('english', :q)) DESC
        LIMIT 20
    """), {"u": user_id, "q": query[:500], **dom}).mappings().all()

    # Reciprocal-rank fusion: rank-only, so the two score scales never fight.
    scores: dict = {}
    rows_by_id: dict = {}
    for result in (dense, lexical):
        for rank, r in enumerate(result):
            rows_by_id[r["id"]] = r
            scores[r["id"]] = scores.get(r["id"], 0.0) + 1.0 / (60 + rank)
    top = sorted(scores, key=scores.get, reverse=True)[:k]
    return [{"domain": rows_by_id[i]["domain"], "kind": rows_by_id[i]["kind"],
             "content": rows_by_id[i]["content"],
             "when": rows_by_id[i]["created_at"].isoformat()
             if rows_by_id[i]["created_at"] else "",
             "score": round(scores[i], 4)} for i in top]
