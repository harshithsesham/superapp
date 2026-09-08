"""Automatic historical context. Pages and checkpoints commit together.

The window is fixed at connection/start time. There is no total message cap:
the dispatcher continues until the provider has no next page. Nothing here
triages, drafts, archives, or sends mail.
"""
from datetime import datetime

import httpx
from sqlalchemy import or_, select

from ..db import SessionLocal
from ..models import GmailAccount, utcnow
from ..substrate.events import append_event
from ..substrate.history import import_history
from .factory import client_for

HISTORY_MONTHS = 36


def ensure_history_import(account) -> None:
    if account.provider not in ("gmail", "outlook") or account.history_import_state:
        return
    now = utcnow()
    try:
        since = now.replace(year=now.year - 3)
    except ValueError:  # leap-day connection
        since = now.replace(year=now.year - 3, day=28)
    account.history_import_state = {
        "status": "pending", "months": HISTORY_MONTHS,
        "since": since.isoformat(), "until": now.isoformat(),
        "page_token": "", "processed": 0, "recorded": 0, "chunks": 0,
        "error": "", "updated_at": now.isoformat(),
    }


def run_history_imports(*, user_id: str | None = None, limit: int = 5, pages: int = 1) -> int:
    """A few pages per invocation, including previously connected accounts.

    Row locks serialize workers. A crash rolls back the page; the next tick
    retries from the last committed token. Completed accounts are left alone.
    """
    completed = 0
    for _ in range(pages):
        with SessionLocal() as db:
            query = select(GmailAccount.id).where(
                GmailAccount.provider.in_(("gmail", "outlook")),
                or_(GmailAccount.history_import_state.is_(None),
                    GmailAccount.history_import_state["status"].as_string() != "completed"))
            if user_id:
                query = query.where(GmailAccount.user_id == user_id)
            ids = list(db.scalars(query.order_by(
                GmailAccount.history_import_state["updated_at"].as_string().nullsfirst(),
                GmailAccount.created_at, GmailAccount.id).limit(limit)))
        for account_id in ids:
            with SessionLocal() as db:
                account = db.scalar(select(GmailAccount).where(GmailAccount.id == account_id)
                                    .with_for_update(skip_locked=True))
                if account is None or (account.history_import_state or {}).get("status") == "completed":
                    continue
                ensure_history_import(account)
                # Persist the window before the first external call. Reacquire
                # the row lock so another worker cannot advance the same page.
                db.commit()
                account = db.scalar(select(GmailAccount).where(GmailAccount.id == account_id)
                                    .with_for_update(skip_locked=True).execution_options(populate_existing=True))
                if account is None or (account.history_import_state or {}).get("status") == "completed":
                    continue
                state = dict(account.history_import_state)
                previous = dict(state)
                try:
                    messages, token = client_for(db, account.user_id, account).history_page(
                        since=datetime.fromisoformat(state["since"]),
                        until=datetime.fromisoformat(state["until"]), page_token=state["page_token"])
                    from ..llm.provider import LLMProvider
                    stats = import_history(db, user_id=account.user_id,
                                           account_email=account.email, messages=messages,
                                           provider=LLMProvider(), enrich_recent=0,
                                           mail_provider=account.provider)
                    state.update(status="reading" if token else "completed", page_token=token,
                                 processed=state["processed"] + len(messages),
                                 recorded=state["recorded"] + stats["recorded"],
                                 chunks=state["chunks"] + stats["chunks"], error="",
                                 updated_at=utcnow().isoformat())
                    account.history_import_state = state
                    if not token:
                        append_event(db, user_id=account.user_id, type="history_imported",
                                     agent="inbox", domain="inbox", payload={
                                         "account": account.email, "months": HISTORY_MONTHS,
                                         "seen": state["processed"], "recorded": state["recorded"],
                                         "chunks": state["chunks"], "failed_accounts": []})
                    db.commit()
                    completed += 1
                except Exception as exc:
                    db.rollback()
                    account = db.scalar(select(GmailAccount).where(GmailAccount.id == account_id)
                                        .with_for_update())
                    # Do not overwrite progress made by another worker after
                    # our transaction rolled back.
                    if account is None or account.history_import_state != previous:
                        continue
                    state = dict(account.history_import_state)
                    if (isinstance(exc, httpx.HTTPStatusError)
                            and exc.response.status_code in (400, 410) and state["page_token"]):
                        state["page_token"] = ""  # expired page token; replay deduplicates
                    state.update(status="retrying", error=type(exc).__name__,
                                 updated_at=utcnow().isoformat())
                    account.history_import_state = state
                    db.commit()
    return completed
