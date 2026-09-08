"""Chat memory and automatic history survive failures without acting on old mail."""
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import BackgroundTasks
from sqlalchemy import func, select

from test_spine import SessionLocal
from superapp import context_notes, memory
from superapp.inbox import history_ingest
from superapp.inbox.gmail_client import GmailClient
from superapp.inbox.outlook_client import OutlookClient
from superapp.models import GmailAccount, InboxDraft, InboxMessage, MailHistory, SavedContext
from superapp.routers import voice
from superapp.substrate.inbox import upsert_account


@pytest.fixture
def mailbox(monkeypatch):
    monkeypatch.setattr(history_ingest, "SessionLocal", SessionLocal)
    uid = "history-" + str(uuid.uuid4())
    with SessionLocal() as db:
        acct = upsert_account(db, user_id=uid, email="me@example.com", provider="gmail")
        db.commit()
        return uid, acct.id


def historical(mid):
    return {"gmail_msg_id": mid, "thread_id": "old-thread", "from_addr": "alex@example.com",
            "to_addrs": "me@example.com", "subject": "Prior agreement",
            "body_text": "We agreed to meet next Tuesday.", "received_at": "2025-06-03T09:00:00+00:00"}


def state(aid):
    with SessionLocal() as db:
        return dict(db.get(GmailAccount, aid).history_import_state)


@pytest.mark.parametrize("provider", ["gmail", "outlook"])
def test_history_window_fixed_once_including_leap_day(monkeypatch, provider):
    """The window is derived from HISTORY_MONTHS rather than written out
    longhand beside it: the constant used to reach only the state dict and the
    event payload, so changing it moved the window we REPORTED and not the one
    we fetched. Asserting the relationship keeps them married."""
    now = datetime(2024, 2, 29, 9, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(history_ingest, "utcnow", lambda: now)
    acct = SimpleNamespace(provider=provider, history_import_state=None)
    history_ingest.ensure_history_import(acct)
    since = datetime.fromisoformat(acct.history_import_state["since"])
    assert since == history_ingest.window_start(now)
    # 29 February has no counterpart in a non-leap year: walk the day back.
    assert (since.year, since.month) == (2023, 2) and since.day == 28
    assert since.hour == 9 and since.minute == 30
    assert acct.history_import_state["until"] == now.isoformat()
    assert acct.history_import_state["months"] == history_ingest.HISTORY_MONTHS == 12
    original = dict(acct.history_import_state)
    monkeypatch.setattr(history_ingest, "utcnow", lambda: now.replace(year=2026, day=28))
    history_ingest.ensure_history_import(acct)
    assert acct.history_import_state == original
    stub = SimpleNamespace(provider="stub", history_import_state=None)
    history_ingest.ensure_history_import(stub)
    assert stub.history_import_state is None


@pytest.mark.parametrize("provider", ["gmail", "outlook"])
def test_connection_sets_up_history_before_first_triage(monkeypatch, provider):
    from superapp.routers import inbox as routes
    uid = str(uuid.uuid4())
    monkeypatch.setattr(routes, "store_token", lambda *a, **kw: None)
    monkeypatch.setattr("superapp.inbox.factory.client_for", lambda *a: SimpleNamespace(subscribe=lambda: (None, "")))
    def think(db, **kw):
        acct = db.scalar(select(GmailAccount).where(GmailAccount.user_id == uid))
        assert acct.history_import_state["months"] == history_ingest.HISTORY_MONTHS
        assert acct.history_import_state["status"] == "pending"
    monkeypatch.setattr(routes, "run_think", think)
    monkeypatch.setattr(routes, "render_screen", lambda *a, **kw: SimpleNamespace(model_dump=lambda: {}))
    with SessionLocal() as db:
        routes._connect(db, user_id=uid, email="new@example.com", token={}, provider=provider)


@pytest.mark.parametrize("provider", ["gmail", "outlook"])
def test_oauth_callback_commits_account_then_queues_automatic_import(monkeypatch, provider):
    from superapp.routers import inbox as routes
    monkeypatch.setattr(routes, "_verify_state", lambda s: "connected-user")
    link = SimpleNamespace(exchange_code=lambda code: {}, address=lambda: "me@example.com")
    monkeypatch.setattr("superapp.inbox.factory.link_client", lambda p: link)
    monkeypatch.setattr(routes, "GmailClient", lambda token: link)
    monkeypatch.setattr("superapp.inbox.outlook_client.OutlookClient", lambda token: link)
    monkeypatch.setattr(routes, "_connect", lambda *a, **kw: {})
    monkeypatch.setattr("superapp.agents.inbox._heal_reauth", lambda *a: None)
    background = BackgroundTasks()
    committed = []
    db = SimpleNamespace(commit=lambda: committed.append(True))
    callback = routes.gmail_callback if provider == "gmail" else routes.outlook_callback
    callback(background=background, code="code", state="signed", db=db)
    assert committed == [True] and len(background.tasks) == 1
    assert background.tasks[0].func is history_ingest.run_history_imports
    assert background.tasks[0].kwargs == {"user_id": "connected-user", "pages": 4}


def test_existing_mailbox_resumes_each_page_without_old_mail_actions(monkeypatch, mailbox):
    uid, aid = mailbox
    calls = []
    def page(**kw):
        calls.append(kw)
        if not kw["page_token"]:
            return [historical("first")], "second"
        return [historical("first"), historical("last")], ""  # provider overlap
    monkeypatch.setattr(history_ingest, "client_for", lambda *a: SimpleNamespace(history_page=page))
    assert history_ingest.run_history_imports(user_id=uid) == 1
    assert state(aid)["page_token"] == "second"
    assert history_ingest.run_history_imports(user_id=uid) == 1  # independent worker/session
    assert state(aid)["status"] == "completed" and state(aid)["recorded"] == 2
    assert history_ingest.run_history_imports(user_id=uid) == 0
    assert calls[0]["since"] == calls[1]["since"] and calls[0]["until"] == calls[1]["until"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(MailHistory).where(MailHistory.user_id == uid)) == 2
        for model in (InboxMessage, InboxDraft):
            assert db.scalar(select(func.count()).select_from(model).where(model.user_id == uid)) == 0


def test_failed_page_rolls_back_records_and_retries_committed_token(monkeypatch, mailbox):
    uid, aid = mailbox
    monkeypatch.setattr(history_ingest, "client_for", lambda *a: SimpleNamespace(
        history_page=lambda **kw: ([historical("partial")], "next")))
    real_import = history_ingest.import_history
    def interrupted(db, **kw):
        real_import(db, **kw)
        raise RuntimeError("process interrupted before checkpoint")
    monkeypatch.setattr(history_ingest, "import_history", interrupted)
    assert history_ingest.run_history_imports(user_id=uid) == 0
    failed = state(aid)
    assert failed["status"] == "retrying" and failed["page_token"] == "" and failed["recorded"] == 0
    with SessionLocal() as db:
        assert db.scalar(select(MailHistory.id).where(MailHistory.user_id == uid)) is None
    monkeypatch.setattr(history_ingest, "import_history", real_import)
    assert history_ingest.run_history_imports(user_id=uid) == 1
    assert state(aid)["since"] == failed["since"] and state(aid)["recorded"] == 1


def test_expired_page_token_restarts_same_window_without_resetting_progress(monkeypatch, mailbox):
    uid, aid = mailbox
    with SessionLocal() as db:
        acct = db.get(GmailAccount, aid)
        history_ingest.ensure_history_import(acct)
        acct.history_import_state = {**acct.history_import_state, "page_token": "expired", "recorded": 6000}
        db.commit()
    def page(**kw):
        if kw["page_token"]:
            request = httpx.Request("GET", "https://gmail.googleapis.com/messages")
            raise httpx.HTTPStatusError("expired", request=request, response=httpx.Response(400, request=request))
        return [historical("after-six-thousand")], ""
    monkeypatch.setattr(history_ingest, "client_for", lambda *a: SimpleNamespace(history_page=page))
    window = state(aid)["since"]
    assert history_ingest.run_history_imports(user_id=uid) == 0
    assert state(aid)["page_token"] == "" and state(aid)["since"] == window
    assert history_ingest.run_history_imports(user_id=uid) == 1
    assert state(aid)["recorded"] == 6001 and state(aid)["status"] == "completed"


def test_gmail_history_page_bounds_and_failed_message_do_not_skip_page():
    client = object.__new__(GmailClient)
    calls = []
    def get(path, **kw):
        calls.append((path, kw))
        if path == "/messages":
            return {"messages": [{"id": "blocked"}], "nextPageToken": "next"}
        req = httpx.Request("GET", "https://gmail.googleapis.com/messages/blocked")
        raise httpx.HTTPStatusError("temporary", request=req, response=httpx.Response(503, request=req))
    client._get = get
    since = datetime(2023, 9, 7, tzinfo=timezone.utc)
    until = datetime(2026, 9, 7, tzinfo=timezone.utc)
    with pytest.raises(httpx.HTTPStatusError):
        client.history_page(since=since, until=until, page_token="prior")
    assert calls[0][1]["pageToken"] == "prior"
    assert f"after:{int(since.timestamp())} before:{int(until.timestamp())}" in calls[0][1]["q"]


def test_outlook_history_keeps_sent_mail_and_uses_provider_next_link():
    client = object.__new__(OutlookClient)
    client._me_address = "me@example.com"
    calls = []
    link = "https://graph.microsoft.com/v1.0/me/messages?$skiptoken=second"
    def get(path, **kw):
        calls.append((path, kw))
        return {"value": [{"id": "sent", "from": {"emailAddress": {"address": "me@example.com"}},
                           "body": {"content": "Previously agreed"}, "receivedDateTime": "2025-01-01T00:00:00Z"}],
                "@odata.nextLink": link}
    client._get = get
    bounds = {"since": datetime(2023, 9, 7, tzinfo=timezone.utc),
              "until": datetime(2026, 9, 7, tzinfo=timezone.utc)}
    messages, token = client.history_page(**bounds)
    assert messages[0]["direction"] == "outbound" and messages[0]["labels"] == []
    assert "2023-09-07" in calls[0][1]["$filter"] and "2026-09-07" in calls[0][1]["$filter"]
    client.history_page(**bounds, page_token=token)
    assert calls[1] == (link, {})


def test_outlook_context_retains_correct_source_link(monkeypatch):
    from superapp.substrate.history import import_history
    captured = []
    monkeypatch.setattr(memory, "remember", lambda *a, **kw: captured.append(kw) or 1)
    with SessionLocal() as db:
        import_history(db, user_id=str(uuid.uuid4()), account_email="me@example.com",
                       messages=[historical("graph/id+")], mail_provider="outlook")
    assert captured[0]["source"] == "outlook"
    assert captured[0]["source_ref"] == "https://outlook.office.com/mail/deeplink/read/graph%2Fid%2B"


def test_chat_saves_exact_words_durably_and_reuses_them_next_turn(monkeypatch):
    uid = "chat-" + str(uuid.uuid4())
    words = "Remember my food preference: I like Panera for road trips."
    prompts = []
    def complete(self, db, **kw):
        prompts.append(json.loads(kw["prompt"]))
        return SimpleNamespace(stubbed=True, refused=False, text="")
    monkeypatch.setattr(voice.LLMProvider, "complete", complete)
    with SessionLocal() as db:
        result = voice.converse(voice.ConverseBody(messages=[voice.Turn(role="user", text=words)]), user_id=uid, db=db)
        assert result["acted"] and result["say"] == "I'll remember that."
    with SessionLocal() as db:
        voice.converse(voice.ConverseBody(messages=[voice.Turn(role="user", text="What do you know about me?")]), user_id=uid, db=db)
        context_notes.save_context(db, user_id=uid, text=words)  # retry does not duplicate
        db.commit()
        assert len(context_notes.recent_context(db, uid)) == 1
        assert context_notes.recent_context(db, "other-user") == []
    assert prompts[-1]["saved_context"][0]["text"] == words


def test_saved_context_ignores_model_invented_text_and_missing_user_turn():
    uid = "exact-" + str(uuid.uuid4())
    parsed = {"action_type": "remember_context", "reply_body": "Invented bank account details"}
    with SessionLocal() as db:
        assert not voice._execute(db, uid, parsed).get("acted")
        voice._execute(db, uid, parsed, user_text="My colleague James manages the Seattle office.")
        db.commit()
        assert context_notes.recent_context(db, uid)[0]["text"] == "My colleague James manages the Seattle office."


def test_saved_context_survives_index_failure_and_retries_as_private_reference(monkeypatch):
    uid = "index-" + str(uuid.uuid4())
    monkeypatch.setattr(memory, "available", lambda db: True)
    def broken(*a, **kw):
        raise RuntimeError("index unavailable")
    monkeypatch.setattr(memory, "remember", broken)
    with SessionLocal() as db:
        note = context_notes.save_context(db, user_id=uid, text="Internal pricing: $90")
        db.commit()
        assert not note.indexed and note.text == "Internal pricing: $90"
        nid = note.id
    captured = []
    def remember(*a, **kw):
        captured.append(kw)
        return 1
    monkeypatch.setattr(memory, "remember", remember)
    with SessionLocal() as db:
        note = context_notes.save_context(db, user_id=uid, text="Internal pricing: $90")
        db.commit()
        assert note.id == nid and note.indexed
    assert captured[0]["source"] == "import" and captured[0]["domain"] == "knowledge"


def test_history_loading_does_not_block_healthy_reads_but_unindexed_notes_do(monkeypatch, mailbox):
    from superapp.agents import inbox
    from test_inbox_release import message
    uid, aid = mailbox
    monkeypatch.setattr(memory, "available", lambda db: True)
    monkeypatch.setattr(memory, "recall_for_agent", lambda *a, **kw: [])
    with SessionLocal() as db:
        msg = message(db, uid)
        assert not inbox._evidence(db, msg, deep=False)["retrieval_incomplete"]
        assert inbox._evidence(db, msg, deep=False)["history_incomplete"]
        acct = db.get(GmailAccount, aid)
        history_ingest.ensure_history_import(acct)
        acct.history_import_state = {**acct.history_import_state, "status": "completed"}
        db.flush()
        assert not inbox._evidence(db, msg, deep=False)["retrieval_incomplete"]
        db.add(SavedContext(user_id=uid, text="not yet indexed"))
        db.flush()
        assert inbox._evidence(db, msg, deep=False)["retrieval_incomplete"]
