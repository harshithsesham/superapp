"""Gmail as a MailClient (httpx, no SDK): OAuth, incremental sync via history,
send, and Pub/Sub watch.

This class no longer knows how to be fake. The offline mailbox moved to
stub_client.StubMailClient and is selected by the ACCOUNT's provider, so a
real mailbox missing its token can no longer quietly become a fake one that
reports imaginary sends as delivered.

Scopes climb the trust ladder with settings.gmail_scope_tier:
  read -> gmail.readonly | send -> +gmail.send | modify -> +gmail.modify
"""
import base64
import html as html_mod
import json
import re as re_mod
import time
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.utils import getaddresses, parseaddr

import httpx

from ..config import get_settings
from typing import Callable

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"

SCOPES_BY_TIER = {
    "read": ["https://www.googleapis.com/auth/gmail.readonly"],
    "send": ["https://www.googleapis.com/auth/gmail.readonly",
             "https://www.googleapis.com/auth/gmail.send"],
    "modify": ["https://www.googleapis.com/auth/gmail.readonly",
               "https://www.googleapis.com/auth/gmail.send",
               "https://www.googleapis.com/auth/gmail.modify"],
}

MAX_BODY_CHARS = 8000

# Skip only what is unambiguously not the person's mail: spam/trash, their own
# sent mail, and the two pure-noise tabs. CATEGORY_UPDATES stays IN — Gmail
# hangs that label on mail people actually see in Primary (receipts, banks,
# humans via services), and deciding what's noise is triage's job, not a
# label heuristic's.
SKIP_LABELS = {"SPAM", "TRASH", "SENT", "DRAFT", "CATEGORY_SOCIAL",
               "CATEGORY_PROMOTIONS"}


# The shared cleaner; _html_to_text is the name used at ingest.
from .text import clean_email_text  # noqa: E402
_html_to_text = clean_email_text


class GmailClient:
    """token: dict {access_token, refresh_token, expiry_ts} (vault-stored JSON).

    on_token_refresh, when given, is called with the whole token dict after a
    refresh so the caller can persist it. Google does not rotate refresh
    tokens, so for Gmail this is bookkeeping; the protocol carries it because
    other providers do rotate and would otherwise die silently.
    """

    provider = "gmail"

    def __init__(self, token: dict | None = None,
                 on_token_refresh: Callable[[dict], None] | None = None) -> None:
        self.settings = get_settings()
        self.token = token or {}
        self._on_token_refresh = on_token_refresh

    # -- oauth ---------------------------------------------------------------
    def auth_url(self, state: str) -> str:
        scopes = " ".join(SCOPES_BY_TIER[self.settings.gmail_scope_tier])
        params = httpx.QueryParams({
            "client_id": self.settings.google_client_id,
            "redirect_uri": self.settings.google_redirect_uri,
            "response_type": "code", "scope": scopes, "state": state,
            "access_type": "offline", "prompt": "consent",
        })
        return f"{AUTH_URL}?{params}"

    def exchange_code(self, code: str) -> dict:
        data = httpx.post(TOKEN_URL, data={
            "client_id": self.settings.google_client_id,
            "client_secret": self.settings.google_client_secret,
            "redirect_uri": self.settings.google_redirect_uri,
            "grant_type": "authorization_code", "code": code,
        }, timeout=30).raise_for_status().json()
        return {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", ""),
            "expiry_ts": time.time() + data.get("expires_in", 3600) - 60,
        }

    def _access_token(self) -> str:
        if self.token.get("expiry_ts", 0) < time.time() and self.token.get("refresh_token"):
            data = httpx.post(TOKEN_URL, data={
                "client_id": self.settings.google_client_id,
                "client_secret": self.settings.google_client_secret,
                "grant_type": "refresh_token",
                "refresh_token": self.token["refresh_token"],
            }, timeout=30).raise_for_status().json()
            self.token["access_token"] = data["access_token"]
            self.token["expiry_ts"] = time.time() + data.get("expires_in", 3600) - 60
            if data.get("refresh_token"):
                self.token["refresh_token"] = data["refresh_token"]
            if self._on_token_refresh:
                self._on_token_refresh(dict(self.token))
        return self.token["access_token"]

    def _get(self, path: str, **params) -> dict:
        resp = httpx.get(f"{GMAIL}{path}", params=params or None, timeout=30,
                         headers={"Authorization": f"Bearer {self._access_token()}"})
        return resp.raise_for_status().json()

    def _post(self, path: str, payload: dict) -> dict:
        resp = httpx.post(f"{GMAIL}{path}", json=payload, timeout=30,
                          headers={"Authorization": f"Bearer {self._access_token()}"})
        return resp.raise_for_status().json()

    # -- profile / sync ------------------------------------------------------
    def profile(self) -> dict:
        return self._get("/profile")

    def address(self) -> str:
        return self.profile()["emailAddress"]

    def new_messages(self, history_id: str) -> tuple[list[dict], str]:
        """Returns (messages, new_history_id). Empty history_id = fresh connect:
        NO backfill — set the watermark to now and only ever process new mail
        arriving in the Primary inbox from this point on."""
        if not history_id:
            return [], str(self.profile()["historyId"])

        ids, page = [], None
        data = {}
        while True:
            params = {"startHistoryId": history_id, "historyTypes": "messageAdded"}
            if page:
                params["pageToken"] = page
            try:
                data = self._get("/history", **params)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    from .base import HistoryExpired
                    raise HistoryExpired("Gmail history expired; full inbox recovery is required") from exc
                raise
            for h in data.get("history", []):
                ids += [m["message"]["id"] for m in h.get("messagesAdded", [])]
            page = data.get("nextPageToken")
            if not page:
                break
        new_hid = str(data.get("historyId", history_id))
        msgs = []
        for mid in dict.fromkeys(ids):
            try:
                msgs.append(self._parse(self._get(f"/messages/{mid}", format="full")))
            except httpx.HTTPStatusError as exc:
                # Mail can vanish or be policy-blocked between the history
                # listing and the fetch (spam purges, immediate deletes,
                # 403-forbidden ghosts). Skip it; never let one message
                # kill the whole sync.
                if exc.response.status_code in (404, 410):
                    continue
                raise
        return [m for m in msgs if m], new_hid

    def recovery_page(self, page_token: str = "") -> tuple[list[dict], str]:
        """One page of the current inbox. A 403 is a gap, not an empty message.

        A separate history watermark is captured before this scan begins;
        incremental catch-up from it covers arrivals during pagination.
        Recovery deliberately has no send/archive operations.
        """
        params = {"labelIds": "INBOX", "maxResults": 25}
        if page_token:
            params["pageToken"] = page_token
        data = self._get("/messages", **params)
        messages = []
        for ref in data.get("messages", []):
            try:
                parsed = self._parse(self._get(f"/messages/{ref['id']}", format="full"))
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (404, 410):
                    continue  # deleted since listing; there is no content to recover
                raise
            if parsed:
                messages.append(parsed)
        return messages, data.get("nextPageToken", "")

    def backfill(self, n: int = 40) -> list[dict]:
        """Recent Primary-inbox mail for a first fill: plain list + fetch,
        no history cursor. Category tabs (promos/social/updates) are
        filtered by _parse, same as live sync."""
        # Ask for Primary directly: recent INBOX ids are mostly category-tab
        # noise, which starves the fill after filtering.
        data = self._get("/messages", q="category:primary", maxResults=min(n * 2, 100))
        msgs: list[dict] = []
        for ref in data.get("messages", []):
            if len(msgs) >= n:
                break
            try:
                parsed = self._parse(self._get(f"/messages/{ref['id']}", format="full"))
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (403, 404, 410):
                    continue
                raise
            if parsed:
                msgs.append(parsed)
        return msgs

    def history_page(self, *, since: datetime, until: datetime, page_token: str = "") -> tuple[list[dict], str]:
        """One bounded page of the fixed historical window, never the live queue."""
        params = {"q": f"after:{int(since.timestamp())} before:{int(until.timestamp())} -in:chats -in:drafts -in:spam -in:trash",
                  "maxResults": 50}
        if page_token:
            params["pageToken"] = page_token
        data = self._get("/messages", **params)
        messages = []
        for ref in data.get("messages", []):
            try:
                parsed = self._parse(self._get(f"/messages/{ref['id']}", format="full"), queue=False)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (404, 410):
                    continue
                raise
            if parsed:
                messages.append(parsed)
        return messages, data.get("nextPageToken", "")

    def history(self, *, months: int = 36, limit: int = 1500,
                page_size: int = 100) -> list[dict]:
        """Past conversation for context: sent AND received, inbox and archive,
        going back `months`. Read-only and inert.

        This is deliberately NOT `backfill`. backfill feeds the queue, so what
        it returns gets triaged, drafted for and possibly archived — which is
        why it is small, recent, and Primary-only. Everything here goes to
        mail_history instead, where nothing acts on it. That separation is what
        makes "import two years of mail" a safe thing to offer: the import
        cannot reply to a 2024 email, because the reply path never reads this.

        `-in:chats -in:drafts` keeps Hangouts noise and unsent fragments out.
        """
        after = (datetime.now(timezone.utc) - timedelta(days=months * 31)).strftime("%Y/%m/%d")
        q = f"after:{after} -in:chats -in:drafts -in:spam -in:trash"
        out: list[dict] = []
        page = None
        while len(out) < limit:
            params = {"q": q, "maxResults": min(page_size, limit - len(out))}
            if page:
                params["pageToken"] = page
            data = self._get("/messages", **params)
            refs = data.get("messages", [])
            if not refs:
                break
            for ref in refs:
                try:
                    parsed = self._parse(self._get(f"/messages/{ref['id']}", format="full"),
                                         queue=False)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in (403, 404, 410):
                        continue
                    raise
                if parsed:
                    out.append(parsed)
            page = data.get("nextPageToken")
            if not page:
                break
        return out

    def _parse(self, raw: dict, *, queue: bool = True) -> dict | None:
        """queue=True is the working set: new Primary-inbox mail, the only
        thing that may ever be triaged, drafted for or archived.

        queue=False is the historical record — sent mail, archived mail, old
        threads. Same header parsing, no label filter, and the result is only
        ever written to mail_history, which nothing downstream treats as work.
        """
        labels = set(raw.get("labelIds", []))
        if queue and ("INBOX" not in labels or labels & SKIP_LABELS):
            return None  # not new Primary-inbox mail
        if not queue and labels & {"SPAM", "TRASH", "DRAFT"}:
            return None  # never a record of a real exchange
        headers = {h["name"].lower(): h["value"]
                   for h in raw.get("payload", {}).get("headers", [])}
        name, addr = parseaddr(headers.get("from", ""))

        def part_text(part, mime: str) -> str:
            if part.get("mimeType") == mime and part.get("body", {}).get("data"):
                return base64.urlsafe_b64decode(part["body"]["data"]).decode(errors="ignore")
            return "".join(part_text(p, mime) for p in part.get("parts", []))

        payload = raw.get("payload", {})
        body = part_text(payload, "text/plain")
        if not body.strip():
            body = part_text(payload, "text/html")
        body = _html_to_text(body) or raw.get("snippet", "")

        # RFC 3834: mail that announces itself as automatic (our own auto-replies
        # included) must never be auto-answered, or two assistants loop forever.
        auto_sub = headers.get("auto-submitted", "").strip().lower()

        def addrs(header: str) -> str:
            """Comma-joined addresses, lowercased. Display names are dropped:
            they are attacker-chosen and we only ever match on the address."""
            raw_v = headers.get(header, "")
            out = [a.strip().lower() for _, a in getaddresses([raw_v]) if a and "@" in a]
            return ",".join(dict.fromkeys(out))[:2000]

        return {
            "gmail_msg_id": raw["id"], "thread_id": raw.get("threadId", ""),
            "from_name": name or addr, "from_addr": addr,
            "subject": headers.get("subject", ""),
            # The envelope. Kept because who else was on it, and whether this is
            # a reply inside a thread, decide importance far better than a body.
            "to_addrs": addrs("to"),
            "cc_addrs": addrs("cc"),
            "reply_to": (parseaddr(headers.get("reply-to", ""))[1] or "").lower()[:320],
            "message_id_hdr": headers.get("message-id", "")[:320],
            "in_reply_to": headers.get("in-reply-to", "")[:320],
            "list_id": headers.get("list-id", "")[:320],
            "precedence": headers.get("precedence", "").strip().lower()[:32],
            "has_list_unsubscribe": bool(headers.get("list-unsubscribe", "").strip()),
            "auto_submitted": (auto_sub not in ("", "no")) or headers.get("x-nano-auto", "") == "1",
            "body_text": body[:MAX_BODY_CHARS],
            "received_at": datetime.fromtimestamp(
                int(raw.get("internalDate", 0)) / 1000, tz=timezone.utc
            ).isoformat(),
            "labels": sorted(labels),
            # The half the queue never had. A record of what the user WROTE is
            # what turns "unanswered" from a guess into a fact.
            "direction": "outbound" if "SENT" in labels else "inbound",
        }

    # -- actions -------------------------------------------------------------
    def send_reply(self, *, to_addr: str, subject: str, body: str, thread_id: str,
                   external_id: str = "", auto: bool = False) -> str:
        # external_id is unused here: Gmail attaches a reply to the THREAD.
        # It travels for providers that reply to a specific message instead.
        mime = self.build_reply(to_addr=to_addr, subject=subject, body=body, auto=auto)
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
        return self._post("/messages/send", {"raw": raw, "threadId": thread_id})["id"]

    @staticmethod
    def build_reply(*, to_addr: str, subject: str, body: str, auto: bool = False) -> MIMEText:
        mime = MIMEText(body)
        mime["To"] = to_addr
        mime["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        if auto:
            # Declared so the other side's assistant (ours included) leaves it alone.
            mime["Auto-Submitted"] = "auto-replied"
            mime["X-Nano-Auto"] = "1"
        return mime

    def send_new(self, *, to_addr: str, subject: str, body: str) -> str:
        mime = MIMEText(body)
        mime["To"] = to_addr
        mime["Subject"] = subject
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
        return self._post("/messages/send", {"raw": raw})["id"]

    def archive(self, external_id: str) -> str | None:
        """Gmail keeps the id when mail leaves the inbox, so nothing to return."""
        self._post(f"/messages/{external_id}/modify", {"removeLabelIds": ["INBOX"]})
        return None

    def watch(self) -> datetime | None:
        """Register Pub/Sub push. Re-call before expiry (~7 days)."""
        if not self.settings.gmail_pubsub_topic:
            return None
        data = self._post("/watch", {"topicName": self.settings.gmail_pubsub_topic,
                                     "labelIds": ["INBOX"]})
        return datetime.fromtimestamp(int(data["expiration"]) / 1000, tz=timezone.utc)

    def subscribe(self) -> tuple[datetime | None, str]:
        """Protocol form of watch(). Gmail's registration has no id of its own."""
        return self.watch(), ""


# The offline mailbox now belongs to the stub PROVIDER, not to this client.
from .stub_client import stub_mailbox as _stub_mailbox  # noqa: E402,F401  (back-compat)
