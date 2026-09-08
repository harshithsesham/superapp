"""Read a bounded recovery page; the caller commits its checkpoint with its rows."""
from dataclasses import dataclass

from .base import HistoryExpired
from ..models import utcnow


@dataclass
class SyncBatch:
    messages: list[dict]
    cursor: str
    recovery_state: dict | None = None
    recovered: bool = False


def _start(acct, client) -> SyncBatch:
    # First persist the watermark. Do not fetch a page in this transaction:
    # a crash on the first page must not recapture a later starting boundary.
    state = {"phase": "scan", "watermark": str(client.profile()["historyId"]),
             "page_token": "", "processed": 0, "started_at": utcnow().isoformat()}
    return SyncBatch([], acct.history_id, state, recovered=True)


def read_batch(acct, client) -> SyncBatch:
    state = dict(acct.recovery_state or {})
    if state:
        if state["phase"] == "scan":
            try:
                messages, page = client.recovery_page(state["page_token"])
            except Exception as exc:
                # An invalid saved page token restarts the full scan while
                # retaining the original boundary. Known messages deduplicate.
                import httpx
                if (isinstance(exc, httpx.HTTPStatusError)
                        and exc.response.status_code == 400 and state["page_token"]):
                    state["page_token"] = ""
                    return SyncBatch([], acct.history_id, state, recovered=True)
                raise
            state.update(page_token=page, phase="scan" if page else "catchup",
                         processed=state["processed"] + len(messages))
            return SyncBatch(messages, acct.history_id, state, recovered=True)
        try:
            messages, cursor = client.new_messages(state["watermark"])
        except HistoryExpired:
            return _start(acct, client)
        return SyncBatch(messages, cursor, recovered=True)

    # Bootstrap uses the same conservative scan instead of an empty inbox
    # being presented as a successful initial sync.
    if not acct.history_id and getattr(acct, "provider", "gmail") == "gmail":
        return _start(acct, client)
    try:
        messages, cursor = client.new_messages(acct.history_id)
    except HistoryExpired:
        return _start(acct, client)
    return SyncBatch(messages, cursor)


def resume_recoveries(limit: int = 10) -> int:
    """Dispatcher backstop, with a separate transaction per user's page(s)."""
    from sqlalchemy import or_, select
    from ..agents.base import run_think
    from ..db import SessionLocal
    from ..models import GmailAccount
    with SessionLocal() as db:
        users = list(db.scalars(select(GmailAccount.user_id).where(
            or_(GmailAccount.recovery_state.isnot(None), GmailAccount.sync_error != ""))
            .distinct().order_by(GmailAccount.user_id).limit(limit)))
    completed = 0
    for user_id in users:
        with SessionLocal() as db:
            try:
                run_think(db, agent="inbox", user_id=user_id,
                          trigger={"kind": "recovery"})
                completed += 1
            except Exception:
                # No checkpoint commits on a failed page. The next tick retries.
                import logging
                logging.getLogger(__name__).warning("Inbox recovery page failed", exc_info=False)
    return completed
