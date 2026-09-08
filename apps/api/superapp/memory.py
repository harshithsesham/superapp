"""Semantic memory: pgvector recall over what happened, and over what the
person already knew.

Postgres-only by design — memory_chunks lives outside the ORM, so SQLite
dev/test environments recall nothing. Callers must treat an empty recall as
"this environment has no memory", never as "there is nothing to know": see
`available()`, which exists so an eval can refuse to score rather than
silently measure a system with its memory switched off.

Embeddings come from Voyage (voyage-3.5-lite, 1024 dims). Missing credentials
or a provider failure leave text pending for retry and available to lexical
search. Legacy stub vectors are excluded from semantic search and retried.
"""
import json
import math
import re
import uuid
from datetime import datetime

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from .config import get_settings

DIMS = 1024
CHUNK_CHARS = 1400        # ~350 tokens: one idea, small enough to rank precisely
CHUNK_OVERLAP = 150       # so a sentence spanning a boundary is findable from both
MAX_CHUNKS = 400          # a runaway document cannot flood one person's memory


class EmbeddingUnavailable(RuntimeError):
    """The embedding provider was configured but did not answer."""


def embed(texts: list[str], *, input_type: str = "document") -> tuple[list[list[float]], str]:
    """All batches or an explicit failure; never return fabricated vectors."""
    if not texts:
        return [], "ok"
    settings = get_settings()
    if not settings.voyage_api_key:
        raise EmbeddingUnavailable("No embedding provider configured")
    try:
        vectors = []
        for offset in range(0, len(texts), 128):
            batch = texts[offset:offset + 128]
            resp = httpx.post(
                "https://api.voyageai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {settings.voyage_api_key}"},
                json={"model": "voyage-3.5-lite", "input": batch,
                      "input_type": input_type, "output_dimension": DIMS}, timeout=30)
            resp.raise_for_status()
            rows = sorted(resp.json()["data"], key=lambda d: d["index"])
            if [d["index"] for d in rows] != list(range(len(batch))):
                raise ValueError("Incomplete embedding batch")
            for row in rows:
                vec = row["embedding"]
                if len(vec) != DIMS or not all(math.isfinite(x) for x in vec):
                    raise ValueError("Invalid embedding vector")
                vectors.append(vec)
        return vectors, "ok"
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        raise EmbeddingUnavailable("Embedding request failed or returned incomplete data") from e


def available(db: Session) -> bool:
    """False on SQLite. An eval harness should refuse to score when this is
    False rather than conclude that context did not help."""
    return db.get_bind().dialect.name == "postgresql"


_available = available   # back-compat for existing callers


def _split_long(para: str) -> list[str]:
    """Break one over-long paragraph at the last whitespace before the limit,
    hard-cutting only when there is no whitespace to break on.

    Written as a loop rather than a regex on purpose: a regex of the form
    `.{1,N}(?:\\s|$)` looks like it splits a paragraph and in fact DISCARDS
    everything it cannot match, so a 5,000-character block with no blank lines
    kept 1,400 characters and lost the rest — silently, which is the same
    failure as the truncation this whole function replaces.
    """
    out: list[str] = []
    i = 0
    while i < len(para):
        end = min(i + CHUNK_CHARS, len(para))
        if end < len(para):
            brk = para.rfind(" ", i + CHUNK_CHARS // 2, end)
            if brk > i:
                end = brk
        out.append(para[i:end])
        i = end
        while i < len(para) and para[i] == " ":
            i += 1
    return out


def chunk(content: str) -> list[str]:
    """Split on paragraph boundaries, packing up to CHUNK_CHARS, with a little
    overlap. Truncation used to drop everything after 2,000 characters, so a
    decision at the end of a long note was unfindable.

    Every character of the input lands in some chunk. That is the invariant
    worth testing, because losing text here loses it everywhere downstream.
    """
    body = (content or "").strip()
    if not body:
        return []
    if len(body) <= CHUNK_CHARS:
        return [body]
    paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    chunks: list[str] = []
    cur = ""
    for para in paras:
        for piece in ([para] if len(para) <= CHUNK_CHARS else _split_long(para)):
            piece = piece.strip()
            if not piece:
                continue
            if len(cur) + len(piece) + 2 <= CHUNK_CHARS:
                cur = f"{cur}\n\n{piece}" if cur else piece
            else:
                if cur:
                    chunks.append(cur)
                    tail = cur[-CHUNK_OVERLAP:]
                    cur = f"{tail}\n\n{piece}" if len(tail) + len(piece) < CHUNK_CHARS else piece
                else:
                    cur = piece
    if cur:
        chunks.append(cur)
    return chunks[:MAX_CHUNKS]


def remember(db: Session, *, user_id: str, domain: str, kind: str,
             ref_id: str, content: str, source: str = "", author: str = "",
             title: str = "", source_ref: str = "", project: str = "",
             event_at: datetime | None = None) -> int:
    """Store one piece of source material, chunked and embedded. Idempotent per
    (user, kind, ref_id): re-remembering replaces every chunk of it.

    `event_at` is when the thing HAPPENED, not when it was imported — without
    it, a decade of imported history all looks like today and recency ranking
    becomes noise. `source_ref` links a chunk back to the original so a citation
    can point at something.

    Returns the number of chunks written. An embedding outage stores the text
    with embed_status='pending' and a zero vector that is excluded from dense
    search, so nothing is lost and nothing is faked.
    """
    if not available(db) or not (content or "").strip():
        return 0
    pieces = chunk(content)
    if not pieces:
        return 0
    try:
        vecs, status = embed(pieces)
    except EmbeddingUnavailable:
        vecs, status = [[0.0] * DIMS for _ in pieces], "pending"

    db.execute(text("DELETE FROM memory_chunks WHERE user_id = :u AND kind = :k AND ref_id = :r"),
               {"u": user_id, "k": kind, "r": ref_id})
    for i, (piece, vec) in enumerate(zip(pieces, vecs)):
        db.execute(text("""
            INSERT INTO memory_chunks
              (user_id, domain, kind, ref_id, content, embedding, source, author,
               title, source_ref, project, chunk_index, event_at, embed_status)
            VALUES (:u, :d, :k, :r, :c, (:e)::vector, :src, :au, :ti, :sr, :pr,
                    :ci, :ev, :st)
        """), {"u": user_id, "d": domain, "k": kind, "r": ref_id, "c": piece,
               "e": json.dumps(vec), "src": source[:32], "au": author[:320],
               "ti": title[:256], "sr": source_ref[:512], "pr": project[:120],
               "ci": i, "ev": event_at, "st": status})
    return len(pieces)


def retry_pending(db: Session, *, user_id: str | None = None, limit: int = 200) -> int:
    """Re-embed rows whose provider call failed. Cron this; an outage should
    cost latency, never silent holes in what can be found."""
    if not available(db):
        return 0
    where = "embed_status IN ('pending', 'stub')" + (" AND user_id = :u" if user_id else "")
    rows = db.execute(text(f"SELECT id, content FROM memory_chunks WHERE {where} LIMIT :n"),
                      {"n": limit, **({"u": user_id} if user_id else {})}).mappings().all()
    if not rows:
        return 0
    try:
        vecs, status = embed([r["content"] for r in rows])
    except EmbeddingUnavailable:
        return 0
    for row, vec in zip(rows, vecs):
        db.execute(text("UPDATE memory_chunks SET embedding = (:e)::vector, embed_status = :st "
                        "WHERE id = :i"),
                   {"e": json.dumps(vec), "st": status, "i": row["id"]})
    return len(rows)


def recall(db: Session, *, user_id: str, query: str, k: int = 5,
           domains: list[str] | None = None) -> list[dict]:
    """Hybrid recall: dense vectors (paraphrase) + Postgres full-text (exact
    names, rare terms), fused by reciprocal rank. Two cheap queries, fused in
    code, no extra infrastructure.

    Every result carries its provenance, so a caller can cite what it used and a
    reader can go and check. Rows still waiting to embed are excluded from the
    dense arm but remain findable lexically — a degraded search, not a blind one.
    """
    if not available(db) or not (query or "").strip():
        return []
    db.info["memory_retrieval_degraded"] = False
    domain_filter = "AND domain = ANY(:doms)" if domains else ""
    dom = {"doms": domains} if domains else {}
    # A healthy query embedding does not mean the source index has caught up.
    # Pending sources may match only by meaning, so callers must retain the
    # incomplete-context hold until this user's permitted sources are indexed.
    db.info["memory_retrieval_degraded"] = bool(db.scalar(text(f"""
        SELECT EXISTS(SELECT 1 FROM memory_chunks
          WHERE user_id = :u AND embed_status <> 'ok' {domain_filter})
    """), {"u": user_id, **dom}))
    cols = ("id, ref_id, domain, kind, content, created_at, event_at, source, author, "
            "title, source_ref, project, embed_status")

    try:
        vecs, _ = embed([query[:2000]], input_type="query")
        dense = db.execute(text(f"""
            SELECT {cols} FROM memory_chunks
            WHERE user_id = :u AND embed_status = 'ok' {domain_filter}
            ORDER BY embedding <=> (:e)::vector
            LIMIT 20
        """), {"u": user_id, "e": json.dumps(vecs[0]), **dom}).mappings().all()
    except EmbeddingUnavailable:
        db.info["memory_retrieval_degraded"] = True
        dense = []   # lexical still works; the caller sees fewer, not wrong, results

    # A subject plus body is not an AND query: one unrelated word must not
    # exclude a relevant project note when dense search is unavailable.
    terms = list(dict.fromkeys(re.findall(r"[^\W_]+", query.lower())))[:40]
    lexical_query = " | ".join(terms) or "nanonomatch"
    lexical = db.execute(text(f"""
        SELECT {cols} FROM memory_chunks
        WHERE user_id = :u {domain_filter}
          AND to_tsvector('english', content) @@ to_tsquery('english', :q)
        ORDER BY ts_rank(to_tsvector('english', content),
                         to_tsquery('english', :q)) DESC
        LIMIT 20
    """), {"u": user_id, "q": lexical_query, **dom}).mappings().all()

    scores: dict = {}
    rows_by_id: dict = {}
    for result in (dense, lexical):
        for rank, r in enumerate(result):
            rows_by_id[r["id"]] = r
            scores[r["id"]] = scores.get(r["id"], 0.0) + 1.0 / (60 + rank)
    top = sorted(scores, key=scores.get, reverse=True)[:k]

    out = []
    for i in top:
        r = rows_by_id[i]
        when = r["event_at"] or r["created_at"]
        out.append({
            "domain": r["domain"], "kind": r["kind"], "content": r["content"], "ref_id": r["ref_id"],
            "when": when.isoformat() if when else "",
            "source": r["source"], "author": r["author"], "title": r["title"],
            "source_ref": r["source_ref"], "project": r["project"],
            "degraded": r["embed_status"] != "ok" or db.info["memory_retrieval_degraded"],
            "score": round(scores[i], 4),
        })
    return out


def recall_for_agent(db: Session, *, agent: str, user_id: str, query: str,
                     k: int = 5) -> list[dict]:
    """Recall, entitlement-scoped exactly like facts and events.

    Unscoped recall would quietly undo the architecture's one hard rule: the
    inbox agent could retrieve a health note or a bank statement because a
    reply happened to mention money. The scope list is the same one the context
    API uses, so the two can never drift apart.
    """
    from .substrate.context import AGENT_SCOPES

    if agent not in AGENT_SCOPES:
        raise ValueError(f"Unknown agent {agent!r}; register it in AGENT_SCOPES")
    return recall(db, user_id=user_id, query=query, k=k, domains=AGENT_SCOPES[agent])


def import_source(db: Session, *, user_id: str, kind: str, title: str, text_body: str,
                  author: str = "", occurred_at: datetime | None = None,
                  project: str = "", source_ref: str = "",
                  ref_id: str | None = None) -> dict:
    """The one door for context that did not arrive as email: notes, meeting
    transcripts, documents, text pulled out of slides.

    Everything imported is TREATED AS UNTRUSTED CONTENT, exactly like an email
    body: it is evidence to reason over, never instructions to follow, and its
    provenance travels with it so a draft can say where a claim came from.
    """
    ref = ref_id or f"import-{uuid.uuid4().hex[:16]}"
    n = remember(db, user_id=user_id, domain="knowledge", kind=kind, ref_id=ref,
                 content=text_body, source="import", author=author, title=title,
                 source_ref=source_ref, project=project, event_at=occurred_at)
    # One source cannot flood a person's whole memory. If the cap bit, say so:
    # a quiet drop here is the same bug as the truncation this replaces.
    capped = len(chunk(text_body)) >= MAX_CHUNKS
    return {"ref_id": ref, "chunks": n, "stored": bool(n), "truncated": capped,
            "reason": ("" if not capped else
                       f"stored the first {MAX_CHUNKS} sections; split the source and re-import the rest")
                      if n else ("memory needs Postgres in this environment"
                                 if not available(db) else "nothing to store")}
