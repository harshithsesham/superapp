"""Regressions for evidence-backed decisions and recovery of missing sync history."""
import json
import uuid
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from test_spine import SessionLocal
from superapp.agents import inbox
from superapp.config import get_settings
from superapp.inbox.base import HistoryExpired
from superapp.inbox.gmail_client import GmailClient
from superapp.models import GmailAccount, InboxMessage, utcnow
from superapp.substrate import get_context
from superapp.substrate.inbox import inbox_context, upsert_account


def message(db, uid):
    row = InboxMessage(user_id=uid, account_email="me@example.com", gmail_msg_id="current",
                       thread_id="thread", from_name="Alex", from_addr="alex@example.com",
                       subject="Pier9 update", body_text="There is news on the Pier9 project.")
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def evidence(monkeypatch):
    monkeypatch.setattr("superapp.memory.available", lambda db: True)
    note = {"when": "2026-09-01", "source": "import", "domain": "knowledge", "author": "user",
            "title": "Investor meeting", "project": "Pier9", "source_ref": "notes:investor",
            "content": "Alex is our lead investor. Escalate every Pier9 funding update.",
            "degraded": False}
    monkeypatch.setattr("superapp.memory.recall_for_agent", lambda *args, **kw: [note])
    return note


def test_prior_note_reaches_triage_archive_verifier_and_draft(evidence):
    seen = []
    def complete(db, **kw):
        seen.append(json.loads(kw["prompt"]))
        text = json.dumps({"tier": "worth_knowing"}) if kw["task"] == "inbox_triage" else (
            json.dumps({"veto": True}) if kw["task"] == "clear_verification" else "Thanks, I will review this.")
        return SimpleNamespace(stubbed=False, refused=False, text=text)
    with SessionLocal() as db:
        uid = str(uuid.uuid4())
        row = message(db, uid)
        ctx = get_context(db, agent="inbox", user_id=uid)
        provider = SimpleNamespace(complete=complete)
        inbox._triage_one(db, ctx, provider, row)
        assert inbox._verify_clear(db, ctx, provider, row) is False
        inbox._draft_reply(db, ctx, provider, row)
    assert len(seen) == 3
    for payload in seen:
        assert payload["evidence"]["related_context"][0]["text"] == evidence["content"]


@pytest.mark.parametrize("author,ref", [
    ("malex@example.com", "https://mail.google.com/mail/u/0/#all/other"),
    ("banker@example.com", "https://mail.google.com/mail/u/0/#all/other-thread"),
])
def test_reply_context_rejects_partial_correspondent_and_thread_matches(monkeypatch, author, ref):
    from superapp import memory
    monkeypatch.setattr(memory, "available", lambda db: True)
    hit = {"when": "", "source": "gmail", "domain": "inbox", "kind": "mail",
           "author": author, "title": "Private mail", "project": "", "source_ref": ref,
           "content": "An unrelated correspondent's private balance.", "degraded": False}
    monkeypatch.setattr(memory, "recall_for_agent", lambda *a, **kw: [hit])
    with SessionLocal() as db:
        row = message(db, str(uuid.uuid4()))
        assert inbox._evidence(db, row, deep=True)["related_context"] == []


def test_imported_context_stays_held_until_user_reviews_the_draft(monkeypatch, evidence):
    from test_spine import _model_replies_with, _sync_delegated_sender
    from superapp import autosend
    from superapp.llm.provider import LLMResponse
    from superapp.routers.inbox import DraftEdit, edit_draft, send_draft, send_matching_pending_drafts
    monkeypatch.setattr(get_settings(), "gmail_scope_tier", "send")
    monkeypatch.setattr(autosend, "TIMERS_ENABLED", False)
    # The evidence fixture supplies retrieval on SQLite; pgvector storage is
    # exercised separately by the PostgreSQL check.
    monkeypatch.setattr("superapp.memory.remember", lambda *a, **kw: 0)
    _model_replies_with(monkeypatch, lambda kw: LLMResponse(
        text="Thanks Priya, I will review the investor update.", model="test",
        input_tokens=1, output_tokens=1))
    calls = []
    def send(db, user_id, msg, body, **kw):
        calls.append(body)
        return "reviewed-send"
    monkeypatch.setattr("superapp.inbox.factory.send_via", send)
    uid = "private-context-" + str(uuid.uuid4())
    db, row, draft = _sync_delegated_sender(uid)
    try:
        assert draft.generation_status == "ready" and draft.used_imported_context
        assert draft.status == "waiting" and draft.auto_send_at is None
        with pytest.raises(ValueError, match="own notes"):
            autosend.schedule(db, draft=draft, msg=row, gate_tier=1)
        assert send_matching_pending_drafts(db, uid, sender=row.from_addr) == 0
        assert calls == []

        reviewed = "Thanks Priya, I reviewed this. Let's discuss tomorrow."
        edit_draft(draft.id, DraftEdit(body=reviewed), user_id=uid, db=db)
        assert not draft.used_imported_context
        send_draft(draft.id, user_id=uid, db=db)
        assert calls == [reviewed] and draft.status == "sent"
    finally:
        db.close()


@pytest.mark.parametrize("mode", ["refused", "stubbed", "exception", "malformed", "string", "missing"])
def test_failed_verification_never_authorizes_clearing(evidence, mode):
    def complete(*args, **kw):
        if mode == "exception":
            raise httpx.ConnectError("offline")
        return SimpleNamespace(refused=mode == "refused", stubbed=mode == "stubbed",
            text={"malformed": "{", "string": '{"veto":"false"}', "missing": '{}'}
                 .get(mode, '{"veto":false}'))
    with SessionLocal() as db:
        row = message(db, str(uuid.uuid4()))
        assert not inbox._verify_clear(db, get_context(db, agent="inbox", user_id=row.user_id),
                                       SimpleNamespace(complete=complete), row)


def test_missing_context_never_authorizes_clearing(monkeypatch):
    monkeypatch.setattr("superapp.memory.available", lambda db: False)
    with SessionLocal() as db:
        row = message(db, str(uuid.uuid4()))
        assert not inbox._verify_clear(db, get_context(db, agent="inbox", user_id=row.user_id),
                                       None, row)


def http_error(status):
    request = httpx.Request("GET", "https://gmail.googleapis.com/gmail/v1/users/me/history")
    return httpx.HTTPStatusError("provider error", request=request,
                                response=httpx.Response(status, request=request))


def test_expired_cursor_requires_recovery_without_looking_up_a_new_cursor():
    client = object.__new__(GmailClient)
    client._get = lambda *a, **k: (_ for _ in ()).throw(http_error(404))
    client.profile = lambda: pytest.fail("must not skip forward")
    with pytest.raises(HistoryExpired):
        client.new_messages("old-cursor")


def test_recovery_page_preserves_a_forbidden_fetch_for_retry():
    client = object.__new__(GmailClient)
    def get(path, **kw):
        if path == "/messages":
            return {"messages": [{"id": "cannot-read"}], "nextPageToken": "next"}
        raise http_error(403)
    client._get = get
    with pytest.raises(httpx.HTTPStatusError):
        client.recovery_page()


def raw(mid):
    return {"gmail_msg_id": mid, "thread_id": mid, "from_name": "Alex",
            "from_addr": "alex@example.com", "subject": "Update", "body_text": "Review this?",
            "received_at": utcnow().isoformat()}


def test_recovery_checkpoint_survives_restarts_and_failed_classification(monkeypatch):
    from superapp.inbox.recovery import resume_recoveries
    uid = str(uuid.uuid4())
    archived, scheduled, cursor_reads, pages = [], [], [], []
    class Client:
        def profile(self): return {"historyId": "scan-start"}
        def new_messages(self, cursor):
            cursor_reads.append(cursor)
            if cursor == "expired": raise HistoryExpired("expired")
            assert cursor == "scan-start"
            return [raw("arrived-during-scan")], "caught-up"
        def recovery_page(self, page):
            pages.append(page)
            return ([raw("first"), raw("second")], "page-2") if not page else ([raw("third")], "")
        def archive(self, mid): archived.append(mid)
    monkeypatch.setattr("superapp.inbox.factory.client_for", lambda *a: Client())
    monkeypatch.setattr("superapp.people.update_person", lambda *a, **k: None)
    monkeypatch.setattr("superapp.autosend.send_due", lambda *a, **k: 0)
    monkeypatch.setattr("superapp.autosend.schedule", lambda *a, **k: scheduled.append(k))
    monkeypatch.setattr(inbox, "_auto_reply_match", lambda *a, **k: True)
    monkeypatch.setattr(inbox, "_verify_clear", lambda *a, **k: True)
    monkeypatch.setattr(inbox, "_draft_reply", lambda *a: inbox.DraftResult(status="ready", body="Thanks."))
    monkeypatch.setattr(get_settings(), "gmail_scope_tier", "modify")
    fail = [False]
    def triage(db, ctx, provider, msg):
        if fail[0] and msg.gmail_msg_id == "second": raise RuntimeError("crashed mid-page")
        return {"tier": "cleared" if msg.gmail_msg_id == "first" else "needs_reply",
                "gist": "Update", "why_now": "", "clear_reason": "automated"}
    monkeypatch.setattr(inbox, "_triage_one", triage)
    with SessionLocal() as db:
        acct = upsert_account(db, user_id=uid, email="me@example.com")
        acct.history_id = "expired"
        db.commit()
        aid = acct.id
    def tick():
        with SessionLocal() as db:
            inbox._sync(db, get_context(db, agent="inbox", user_id=uid), {"kind": "email_sync"})
            db.commit()
    tick()  # capture the pre-scan boundary, keeping the old cursor
    with SessionLocal() as db:
        assert db.get(GmailAccount, aid).history_id == "expired"
        assert inbox_context(db, uid)["sync_incomplete"]
    fail[0] = True
    with pytest.raises(RuntimeError): tick()
    with SessionLocal() as db:
        assert db.get(GmailAccount, aid).recovery_state["page_token"] == ""
        assert not db.scalars(select(InboxMessage).where(InboxMessage.user_id == uid)).all()
    fail[0] = False
    tick()
    with SessionLocal() as db:
        assert db.get(GmailAccount, aid).recovery_state["page_token"] == "page-2"
        assert db.get(GmailAccount, aid).history_id == "expired"
    assert resume_recoveries() >= 1  # dispatcher, using a fresh session
    tick()  # catch up arrivals from the original boundary
    with SessionLocal() as db:
        acct = db.get(GmailAccount, aid)
        assert acct.history_id == "caught-up" and acct.recovery_state is None
        assert not inbox_context(db, uid)["sync_incomplete"]
        rows = db.scalars(select(InboxMessage).where(InboxMessage.user_id == uid)).all()
        assert len(rows) == 4
        assert next(m for m in rows if m.gmail_msg_id == "first").tier == "worth_knowing"
    assert archived == scheduled == []
    assert cursor_reads == ["expired", "scan-start"]
    assert pages == ["", "", "page-2"]


def test_embedding_batches_preserve_the_tail_and_reject_missing_vectors(monkeypatch):
    from superapp import memory
    monkeypatch.setattr(get_settings(), "voyage_api_key", "offline-test-key")
    batches = []
    def post(url, **kw):
        batch = kw["json"]["input"]
        batches.append(len(batch))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"data": [
            {"index": i, "embedding": [float(t)] * memory.DIMS} for i, t in reversed(list(enumerate(batch)))]})
    monkeypatch.setattr(memory.httpx, "post", post)
    vecs, status = memory.embed([str(i) for i in range(150)])
    assert batches == [128, 22] and status == "ok"
    assert len(vecs) == 150 and vecs[-1][0] == 149
    monkeypatch.setattr(memory.httpx, "post", lambda *a, **k: SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: {"data": []}))
    with pytest.raises(memory.EmbeddingUnavailable): memory.embed(["lost"])


@pytest.mark.parametrize("signal", ["recovered", "context_incomplete"])
def test_context_hold_applies_to_all_automatic_send_entrypoints(monkeypatch, signal):
    from superapp import autosend
    from superapp.routers.inbox import send_matching_pending_drafts
    from superapp.substrate.inbox import create_draft, draft_unsendable
    monkeypatch.setattr(get_settings(), "gmail_scope_tier", "send")
    monkeypatch.setattr(autosend, "_la", lambda *a, **k: None)
    monkeypatch.setattr("superapp.inbox.factory.send_via", lambda *a, **k: pytest.fail("must not send"))
    with SessionLocal() as db:
        row = message(db, str(uuid.uuid4()))
        row.tier = "needs_reply"
        row.signals = {signal: True}
        draft = create_draft(db, user_id=row.user_id, message_id=row.id,
                             body="Thanks, I will review this.", generation_status="ready")
        db.flush()
        assert draft_unsendable(draft) is None  # a person may still review and send
        with pytest.raises(ValueError): autosend.schedule(db, draft=draft, msg=row, gate_tier=1)
        assert send_matching_pending_drafts(db, row.user_id, sender=row.from_addr) == 0
        assert not autosend._send_one(db, draft)
        assert draft.status == "waiting" and draft.sent_at is None
