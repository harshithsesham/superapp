"""Run only against a disposable database named nano_release_check.

This checks PostgreSQL behavior that the SQLite unit suite cannot exercise.
No model, email, or store provider is contacted.
"""
import os
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

url = os.environ["SUPERAPP_RELEASE_CHECK_DATABASE_URL"]
if make_url(url).database != "nano_release_check":
    raise SystemExit("Refusing to run outside the disposable nano_release_check database")
os.environ["SUPERAPP_DATABASE_URL"] = url
os.environ["SUPERAPP_ANTHROPIC_API_KEY"] = ""
os.environ["SUPERAPP_VOYAGE_API_KEY"] = ""


def migrate(target):
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", target], check=True)


# Start from the already merged main schema, then add the PR 4 grocery schema.
# Preserve existing source text and grocery rows across the recovery upgrade.
migrate("0023")
engine = create_engine(url)
with engine.begin() as db:
    db.execute(text("""INSERT INTO gmail_accounts
        (id,user_id,email,history_id,created_at) VALUES
        ('main-account','main-user','main@example.com','keep-cursor',now())"""))
    db.execute(text("""INSERT INTO memory_chunks
        (user_id,domain,kind,ref_id,content,embedding) VALUES
        ('legacy-user','knowledge','note','old','Old source',CAST(:vec AS vector))"""),
        {"vec": json.dumps([0.0] * 1024)})
migrate("0026")
with engine.begin() as db:
    db.execute(text("""INSERT INTO grocery_items
        (id,user_id,slug,name,created_at,updated_at) VALUES
        ('upgrade-item','upgrade-user','milk','Milk',now(),now())"""))
migrate("head")
migrate("head")

from superapp import memory
from superapp.agents.inbox import _evidence
from superapp.models import GmailAccount, InboxMessage, SavedContext
from superapp.context_notes import save_context, forget_context
from superapp.inbox.history_ingest import ensure_history_import
from superapp.substrate.history import record_message
from superapp.substrate.inbox import upsert_account

with Session(engine) as db:
    assert db.scalar(text("SELECT history_id FROM gmail_accounts WHERE id='main-account'")) == "keep-cursor"
    assert {"grocery_items", "grocery_orders", "grocery_links", "grocery_purchases", "grocery_receipts"} <= set(inspect(engine).get_table_names())
    assert next(c for c in inspect(engine).get_columns("grocery_orders") if c["name"] == "external_id")["type"].length == 1024
    assert db.scalar(text("SELECT name FROM grocery_items WHERE id='upgrade-item'")) == "Milk"
    assert db.scalar(text("SELECT version_num FROM alembic_version")) == "0029"
    assert "saved_context" in inspect(engine).get_table_names()
    assert db.scalar(text("SELECT history_import_state IS NULL FROM gmail_accounts WHERE id='main-account'"))
    old_mail = record_message(db, user_id="outlook-user", account_email="me@example.com", msg={
        "gmail_msg_id": "A" * 180, "thread_id": "B" * 100, "from_addr": "alex@example.com"})
    assert old_mail.gmail_msg_id == "A" * 180 and old_mail.thread_id == "B" * 100
    assert db.scalar(text("SELECT embed_status FROM memory_chunks WHERE user_id='legacy-user'")) == "pending"
    assert {"generation_status", "generation_reason"} <= {
        c["name"] for c in inspect(engine).get_columns("inbox_drafts")}
    assert {"importance", "signals"} <= {
        c["name"] for c in inspect(engine).get_columns("inbox_messages")}

    # A long document must retain its tail without an embedding key.
    body = ("Context paragraph " * 13000) + " UniqueTailMarker investor approval."
    count = memory.remember(db, user_id="check-user", domain="knowledge", kind="note",
                            ref_id="long-note", content=body, title="Investment meeting")
    assert count > 128
    assert db.scalar(text("SELECT count(*) FROM memory_chunks WHERE user_id='check-user'")) == count
    hits = memory.recall_for_agent(db, user_id="check-user", agent="inbox",
                                  query="UniqueTailMarker unrelatedwords")
    assert any("UniqueTailMarker" in r["content"] for r in hits)
    assert all(r["degraded"] for r in hits)
    assert memory.recall_for_agent(db, user_id="other-user", agent="inbox", query="UniqueTailMarker") == []
    assert memory.recall_for_agent(db, user_id="check-user", agent="finance", query="UniqueTailMarker") == []

    # Exercise the dense SQL and pgvector operator with deterministic test
    # vectors; these mocks are never stored in a production path.
    def vectors(texts, **kw): return [[1.0] + [0.0] * (memory.DIMS - 1) for _ in texts], "ok"
    with patch.object(memory, "embed", vectors):
        memory.recall_for_agent(db, user_id="check-user", agent="inbox", query="UniqueTailMarker")
        assert db.info["memory_retrieval_degraded"]  # query works, but source indexing is pending
        assert memory.retry_pending(db, user_id="check-user", limit=400) == count
        hits = memory.recall_for_agent(db, user_id="check-user", agent="inbox", query="UniqueTailMarker")
        assert hits and not any(r["degraded"] for r in hits)

    acct = upsert_account(db, user_id="check-user", email="me@example.com")
    acct.recovery_state = {"phase": "scan", "watermark": "before", "page_token": "second"}
    db.flush()
    assert db.scalar(select(GmailAccount.id).where(GmailAccount.recovery_state.isnot(None))) == acct.id
    acct.recovery_state = None
    db.flush()
    assert db.scalar(select(GmailAccount.id).where(GmailAccount.recovery_state.isnot(None))) is None

    # Background history is selectable on upgraded accounts; its JSON token
    # and window persist together. Chat keeps canonical words even if indexing
    # is unavailable, and its searchable copy carries the private-source hold.
    ensure_history_import(acct)
    acct.history_import_state = {**acct.history_import_state, "page_token": "second", "status": "reading"}
    db.flush()
    assert db.scalar(select(GmailAccount.id).where(
        GmailAccount.user_id == "check-user",
        GmailAccount.history_import_state["status"].as_string() != "completed")) == acct.id
    def broken_index(db, **kw): db.execute(text("SELECT missing_column FROM memory_chunks"))
    with patch.object(memory, "remember", broken_index):
        note = save_context(db, user_id="chat-user", text="Remember Cedarvale's internal pricing.")
        assert not note.indexed
    assert db.scalar(select(SavedContext.text).where(SavedContext.id == note.id)) == note.text
    note = save_context(db, user_id="chat-user", text=note.text)
    assert note.indexed
    hit = memory.recall_for_agent(db, user_id="chat-user", agent="inbox", query="Cedarvale")[0]
    assert hit["source"] == "import" and hit["domain"] == "knowledge"
    assert hit["ref_id"] == note.id
    assert not forget_context(db, user_id="other-user", note_id=note.id)
    assert forget_context(db, user_id="chat-user", note_id=note.id)
    assert db.get(SavedContext, note.id) is None
    assert db.scalar(text("SELECT count(*) FROM memory_chunks WHERE user_id='chat-user'")) == 0

    # Both stores of mail feed the durable receipt consumer. A real PostgreSQL
    # UNION query and the per-user extraction lock run here (SQLite cannot
    # exercise the lock or enforce the original narrow source_ref column).
    from types import SimpleNamespace
    from superapp.agents import grocery
    from superapp.agents.base import ThinkResult
    from superapp.substrate import get_context
    from superapp.models import GroceryPurchase, GroceryReceipt
    long_ref = "graph-" + "A" * 180
    record_message(db, user_id="receipts-user", account_email="me@example.com", msg={
        "gmail_msg_id": long_ref, "from_addr": "receipts@instacart.com",
        "subject": "Grocery receipt", "body_text": "Whole milk, 1 gallon, $4"})
    provider = SimpleNamespace(complete=lambda *a, **kw: SimpleNamespace(stubbed=False, refused=False,
        text=json.dumps({"is_grocery_receipt": True, "suspicious": False, "merchant": "Instacart",
                         "items": [{"name": "Whole milk", "quantity": 1}]})))
    context = get_context(db, agent="grocery", user_id="receipts-user")
    assert grocery._scan_receipts(db, context, provider, ThinkResult())["purchases"] == 1
    assert grocery._scan_receipts(db, context, provider, ThinkResult())["purchases"] == 0
    assert db.scalar(select(GroceryPurchase.source_ref).where(GroceryPurchase.user_id == "receipts-user")) == long_ref
    assert db.scalar(select(GroceryReceipt.status).where(GroceryReceipt.user_id == "receipts-user")) == "done"

    # A retrieval SQL failure is contained by a savepoint; the message remains
    # writable and no caller can interpret the failure as permission to clear.
    msg = InboxMessage(user_id="check-user", account_email="me@example.com", gmail_msg_id="savepoint",
                       thread_id="t", from_addr="alex@example.com", subject="Funding", body_text="Update")
    db.add(msg)
    db.flush()
    def broken(db, **kw): db.execute(text("SELECT missing_column FROM memory_chunks"))
    with patch.object(memory, "recall_for_agent", broken):
        assert _evidence(db, msg, deep=True)["retrieval_incomplete"]
    assert db.scalar(text("SELECT 1")) == 1
    db.rollback()
print("PostgreSQL migration, retrieval, recovery, background history, and chat context checks passed.")
