"""Outlook as a MailClient, over Microsoft Graph (httpx, no SDK).

Reads the same shape Gmail's client produces, so everything above the seam —
triage, drafting, the auto-reply window, the rules engine — cannot tell which
provider a mailbox belongs to.

Four things differ from Gmail and are worth knowing before changing this:

1. THE CURSOR IS A URL. Gmail hands back a history id; Graph hands back a
   delta link, a whole URL that already encodes the folder and the token. It
   is opaque to callers either way, which is why `history_id` is Text.

2. A REPLY ATTACHES TO A MESSAGE, NOT A THREAD. Gmail takes a threadId on
   send. Graph has no equivalent, so a reply is created FROM the message being
   answered, which is why `send_reply` needs `external_id`.

3. THE REFRESH TOKEN ROTATES. Microsoft issues a new one on every refresh and
   expects the old discarded. A client that only mutates memory would leave a
   dead token in the vault and the mailbox would stop syncing days later,
   looking like a mystery disconnection. Hence `on_token_refresh`.

4. IDS CHANGE WHEN MAIL MOVES, unless every request asks for immutable ones.
   Without that, archiving a message would change its id and the next sync
   would ingest it again as new mail. `Prefer: IdType="ImmutableId"` is sent
   on every call, deliberately.
"""
import time
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr

import httpx

from ..config import get_settings
from .base import ReauthRequired
from .text import clean_email_text

GRAPH = "https://graph.microsoft.com/v1.0"
MAX_BODY_CHARS = 8000

# The trust ladder, in Graph's vocabulary. offline_access is what buys a
# refresh token at all; without it the mailbox dies at the first expiry.
SCOPES_BY_TIER = {
    "read": ["offline_access", "User.Read", "Mail.Read"],
    "send": ["offline_access", "User.Read", "Mail.Read", "Mail.Send"],
    # ReadWrite is what permits moving mail out of the inbox. It also permits
    # deleting it — Graph offers no read-and-move-only scope — so this tier is
    # a real escalation, not a formality.
    "modify": ["offline_access", "User.Read", "Mail.ReadWrite", "Mail.Send"],
}

# What a message must carry for _parse to do its job. Asking explicitly keeps
# the payload small and makes the header round-trip possible at all.
MESSAGE_FIELDS = ("id,conversationId,subject,receivedDateTime,from,sender,"
                  "toRecipients,ccRecipients,replyTo,body,bodyPreview,"
                  "internetMessageHeaders,isDraft")


class OutlookClient:
    """token: dict {access_token, refresh_token, expiry_ts} (vault-stored JSON)."""

    provider = "outlook"

    def __init__(self, token: dict | None = None,
                 on_token_refresh=None) -> None:
        self.settings = get_settings()
        self.token = token or {}
        self._on_token_refresh = on_token_refresh
        self._me_address = ""   # filled lazily; used to mark a message outbound

    # -- oauth ---------------------------------------------------------------
    @property
    def _authority(self) -> str:
        tenant = getattr(self.settings, "microsoft_tenant", "") or "common"
        return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0"

    def _scopes(self) -> str:
        tier = getattr(self.settings, "gmail_scope_tier", "read")
        return " ".join(SCOPES_BY_TIER.get(tier, SCOPES_BY_TIER["read"]))

    def auth_url(self, state: str) -> str:
        params = httpx.QueryParams({
            "client_id": self.settings.microsoft_client_id,
            "response_type": "code",
            "redirect_uri": self.settings.microsoft_redirect_uri,
            "response_mode": "query",
            "scope": self._scopes(),
            "state": state,
            # Ask every time, so a person can pick which account to connect
            # and a second mailbox does not silently reuse the first.
            "prompt": "select_account",
        })
        return f"{self._authority}/authorize?{params}"

    def exchange_code(self, code: str) -> dict:
        data = httpx.post(f"{self._authority}/token", data={
            "client_id": self.settings.microsoft_client_id,
            "client_secret": self.settings.microsoft_client_secret,
            "redirect_uri": self.settings.microsoft_redirect_uri,
            "grant_type": "authorization_code",
            "code": code,
            "scope": self._scopes(),
        }, timeout=30).raise_for_status().json()
        return {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", ""),
            "expiry_ts": time.time() + data.get("expires_in", 3600) - 60,
        }

    def _access_token(self) -> str:
        if self.token.get("expiry_ts", 0) < time.time() and self.token.get("refresh_token"):
            resp = httpx.post(f"{self._authority}/token", data={
                "client_id": self.settings.microsoft_client_id,
                "client_secret": self.settings.microsoft_client_secret,
                "grant_type": "refresh_token",
                "refresh_token": self.token["refresh_token"],
                "scope": self._scopes(),
            }, timeout=30)
            if resp.status_code in (400, 401):
                # A revoked grant, a changed password, or a consent withdrawn.
                # Say so plainly rather than retrying into the same wall.
                raise ReauthRequired("Microsoft signed Nano out of this mailbox.")
            data = resp.raise_for_status().json()
            self.token["access_token"] = data["access_token"]
            self.token["expiry_ts"] = time.time() + data.get("expires_in", 3600) - 60
            # Microsoft rotates this. Keeping the old one is how a mailbox
            # dies quietly a few days after it was linked.
            if data.get("refresh_token"):
                self.token["refresh_token"] = data["refresh_token"]
            if self._on_token_refresh:
                self._on_token_refresh(dict(self.token))
        return self.token["access_token"]

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._access_token()}",
            # Ids that survive a move, so archiving cannot make old mail look new.
            "Prefer": 'IdType="ImmutableId"',
        }

    def _get(self, path_or_url: str, **params) -> dict:
        url = path_or_url if path_or_url.startswith("http") else f"{GRAPH}{path_or_url}"
        resp = httpx.get(url, params=params or None, timeout=30, headers=self._headers())
        if resp.status_code == 401:
            raise ReauthRequired("Microsoft rejected the credential for this mailbox.")
        return resp.raise_for_status().json()

    def _post(self, path: str, payload: dict | None = None) -> dict:
        resp = httpx.post(f"{GRAPH}{path}", json=payload, timeout=30, headers=self._headers())
        if resp.status_code == 401:
            raise ReauthRequired("Microsoft rejected the credential for this mailbox.")
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    def _patch(self, path: str, payload: dict) -> dict:
        resp = httpx.patch(f"{GRAPH}{path}", json=payload, timeout=30, headers=self._headers())
        if resp.status_code == 401:
            raise ReauthRequired("Microsoft rejected the credential for this mailbox.")
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    # -- identity ------------------------------------------------------------
    def address(self) -> str:
        me = self._get("/me", **{"$select": "mail,userPrincipalName"})
        # Work accounts sometimes carry only a principal name; personal
        # accounts sometimes carry only mail. Either is the address.
        self._me_address = (me.get("mail") or me.get("userPrincipalName") or "").strip()
        return self._me_address

    # -- reading -------------------------------------------------------------
    def new_messages(self, cursor: str) -> tuple[list[dict], str]:
        """Mail since `cursor`, plus the next cursor.

        An empty cursor means a fresh connection: page to the end of the delta
        WITHOUT keeping anything, so linking a mailbox records a watermark at
        now and never floods the inbox with old mail.
        """
        first_run = not cursor
        url = cursor or f"{GRAPH}/me/mailFolders/inbox/messages/delta"
        params = {"$select": MESSAGE_FIELDS} if first_run or "/delta" in url and "?" not in url else {}
        out: list[dict] = []
        seen_delta = ""
        while True:
            try:
                data = self._get(url, **params)
            except httpx.HTTPStatusError as exc:
                # 410 Gone: the delta token aged out. The honest move is the
                # same as Gmail's expired history id — reset to now, take
                # nothing, and start clean rather than re-ingesting a mailbox.
                if exc.response.status_code == 410:
                    return [], self._fresh_delta()
                raise
            params = {}
            if not first_run:
                for raw in data.get("value", []):
                    parsed = self._parse(raw)
                    if parsed:
                        out.append(parsed)
            nxt = data.get("@odata.nextLink")
            if nxt:
                url = nxt
                continue
            seen_delta = data.get("@odata.deltaLink", "") or cursor
            break
        return out, seen_delta

    def _fresh_delta(self) -> str:
        """Page to the end of a delta run and keep only the cursor."""
        url = f"{GRAPH}/me/mailFolders/inbox/messages/delta"
        params = {"$select": "id"}
        while True:
            data = self._get(url, **params)
            params = {}
            nxt = data.get("@odata.nextLink")
            if not nxt:
                return data.get("@odata.deltaLink", "")
            url = nxt

    def backfill(self, n: int = 40) -> list[dict]:
        """Recent inbox mail for a first fill. Outlook has no promotions or
        social tabs to filter, so the inbox folder IS the working set."""
        data = self._get("/me/mailFolders/inbox/messages",
                         **{"$top": min(n, 100), "$orderby": "receivedDateTime desc",
                            "$select": MESSAGE_FIELDS})
        out = []
        for raw in data.get("value", []):
            parsed = self._parse(raw)
            if parsed:
                out.append(parsed)
            if len(out) >= n:
                break
        return out

    def history_page(self, *, since: datetime, until: datetime, page_token: str = "") -> tuple[list[dict], str]:
        if not self._me_address:
            self.address()
        params = {} if page_token else {
            "$filter": f"receivedDateTime ge {since.strftime('%Y-%m-%dT%H:%M:%SZ')} and receivedDateTime lt {until.strftime('%Y-%m-%dT%H:%M:%SZ')}",
            "$orderby": "receivedDateTime desc", "$top": 50, "$select": MESSAGE_FIELDS}
        data = self._get(page_token or "/me/messages", **params)
        messages = [parsed for raw in data.get("value", [])
                    if (parsed := self._parse(raw, queue=False))]
        return messages, data.get("@odata.nextLink", "")

    def history(self, *, months: int = 36, limit: int = 1500) -> list[dict]:
        """Past conversation for the record: sent AND received, inbox and
        archive. Deliberately not `backfill` — nothing here is ever triaged,
        drafted for or replied to, which is what makes importing years of
        mail a safe thing to offer."""
        if not self._me_address:
            try:
                self.address()   # so sent mail is recorded as outbound, not inbound
            except Exception:  # noqa: BLE001 — direction is a signal, not a gate
                pass
        after = (datetime.now(timezone.utc) - timedelta(days=months * 31)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        url = "/me/messages"
        params = {"$filter": f"receivedDateTime ge {after}",
                  "$orderby": "receivedDateTime desc",
                  "$top": 100, "$select": MESSAGE_FIELDS}
        out: list[dict] = []
        while len(out) < limit:
            data = self._get(url, **params)
            params = {}
            for raw in data.get("value", []):
                if raw.get("isDraft"):
                    continue          # never a record of a real exchange
                parsed = self._parse(raw, queue=False)
                if parsed:
                    out.append(parsed)
                if len(out) >= limit:
                    break
            nxt = data.get("@odata.nextLink")
            if not nxt or len(out) >= limit:
                break
            url = nxt
        return out

    def _parse(self, raw: dict, *, queue: bool = True) -> dict | None:
        """Graph's message resource into the one shape the pipeline reads."""
        if raw.get("@removed") or raw.get("isDraft"):
            return None   # a delta deletion, or an unsent fragment
        frm = ((raw.get("from") or raw.get("sender") or {})
               .get("emailAddress") or {})
        addr = (frm.get("address") or "").strip()
        name = (frm.get("name") or "").strip()
        if not addr:
            name, addr = parseaddr(name)
        if not addr:
            return None

        body = raw.get("body") or {}
        text = body.get("content") or ""
        if (body.get("contentType") or "").lower() == "html" or "<" in text[:400]:
            text = clean_email_text(text)
        text = (text or raw.get("bodyPreview") or "").strip()

        # RFC 3834 and our own marker. Graph restricts custom headers on SEND
        # to x-prefixed names, so outbound stamping leans on X-Nano-Auto; both
        # are read here because mail from other systems does use the standard
        # header, and answering an automatic message is how loops start.
        headers = {(h.get("name") or "").lower(): (h.get("value") or "")
                   for h in (raw.get("internetMessageHeaders") or [])}
        auto_sub = headers.get("auto-submitted", "").strip().lower()
        auto = (auto_sub not in ("", "no")) or headers.get("x-nano-auto", "") == "1"

        when = raw.get("receivedDateTime") or ""
        try:
            received = datetime.fromisoformat(when.replace("Z", "+00:00"))
        except ValueError:
            received = datetime.now(timezone.utc)

        def recipients(field: str) -> str:
            """Comma-joined addresses, lowercased. Display names are dropped:
            they are chosen by the sender and only the address is matched on."""
            out = [((r.get("emailAddress") or {}).get("address") or "").strip().lower()
                   for r in (raw.get(field) or [])]
            return ",".join(dict.fromkeys(a for a in out if "@" in a))[:2000]

        # Graph exposes the raw internet headers, so the envelope signals
        # triage reads work identically on both providers. Without these,
        # Outlook mail would arrive with every signal blank and the model
        # would be back to guessing importance from the body alone.
        reply_to = ""
        for r in (raw.get("replyTo") or []):
            reply_to = ((r.get("emailAddress") or {}).get("address") or "").lower()
            break

        # A message the person SENT is the negative signal that makes
        # "unanswered" a fact rather than a guess. Graph has no SENT label, so
        # it is inferred from whether they are the author.
        me = (self._me_address or "").lower()
        direction = "outbound" if (me and addr == me) else "inbound"

        return {
            "gmail_msg_id": raw["id"],
            "thread_id": raw.get("conversationId", "") or "",
            "from_name": name or addr,
            "from_addr": addr,
            "subject": raw.get("subject") or "",
            "to_addrs": recipients("toRecipients"),
            "cc_addrs": recipients("ccRecipients"),
            "reply_to": reply_to[:320],
            "message_id_hdr": headers.get("message-id", "")[:320],
            "in_reply_to": headers.get("in-reply-to", "")[:320],
            "list_id": headers.get("list-id", "")[:320],
            "precedence": headers.get("precedence", "").strip().lower()[:32],
            "has_list_unsubscribe": bool(headers.get("list-unsubscribe", "").strip()),
            "auto_submitted": auto,
            "body_text": text[:MAX_BODY_CHARS],
            "received_at": received.isoformat(),
            # Outlook has no label model; the folder is the fact. Only the
            # working set is read from the inbox, so that is what this says.
            "labels": ["INBOX"] if queue else [],
            "direction": direction,
        }

    # -- writing -------------------------------------------------------------
    def send_reply(self, *, to_addr: str, subject: str, body: str, thread_id: str,
                   external_id: str = "", auto: bool = False) -> str:
        """Reply inside the existing conversation.

        Graph cannot be told "put this in that thread" the way Gmail can, so
        the reply is created FROM the message being answered. That is what
        `external_id` is for, and without it the reply would start a new
        conversation the recipient reads as a non sequitur.
        """
        if not external_id:
            return self.send_new(to_addr=to_addr, subject=subject, body=body)
        draft = self._post(f"/me/messages/{external_id}/createReply")
        draft_id = draft.get("id")
        if not draft_id:
            raise RuntimeError("Graph returned no draft for the reply")
        payload: dict = {"body": {"contentType": "Text", "content": body}}
        if auto:
            # Only x-prefixed custom headers survive Graph's JSON send, so the
            # RFC 3834 header cannot be stamped here. Our own marker can, and
            # the per-thread cap backs it up.
            payload["internetMessageHeaders"] = [
                {"name": "x-nano-auto", "value": "1"},
            ]
        self._patch(f"/me/messages/{draft_id}", payload)
        self._post(f"/me/messages/{draft_id}/send")
        return draft_id

    def send_new(self, *, to_addr: str, subject: str, body: str) -> str:
        created = self._post("/me/messages", {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": to_addr}}],
        })
        msg_id = created.get("id")
        if not msg_id:
            raise RuntimeError("Graph returned no draft for the new message")
        self._post(f"/me/messages/{msg_id}/send")
        return msg_id

    def archive(self, external_id: str) -> str | None:
        """Move out of the inbox. Immutable ids are requested on every call,
        so the id does not change and there is nothing to hand back."""
        self._post(f"/me/messages/{external_id}/move",
                   {"destinationId": "archive"})
        return None

    def subscribe(self) -> tuple[datetime | None, str]:
        """Graph change notifications need a publicly reachable endpoint that
        answers a synchronous validation handshake, which the Pub/Sub route
        cannot serve. Outlook mail arrives on the dispatcher tick instead, so
        this is a deliberate no-op rather than a missing feature."""
        return None, ""
