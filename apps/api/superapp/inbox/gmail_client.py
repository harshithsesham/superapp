"""Thin Gmail client (httpx, no SDK) with OAuth, incremental sync via history,
send, and Pub/Sub watch. Stub mode (no google_client_id): a deterministic fake
mailbox spanning every tier — urgent asks, FYIs, newsletters, promos, and a
retailer receipt (the Phase 4d hook) — so the whole vertical runs offline.

Scopes climb the trust ladder with settings.gmail_scope_tier:
  read -> gmail.readonly | send -> +gmail.send | modify -> +gmail.modify
"""
import base64
import json
import time
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.utils import parseaddr

import httpx

from ..config import get_settings

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


class GmailClient:
    """token: dict {access_token, refresh_token, expiry_ts} (vault-stored JSON)."""

    def __init__(self, token: dict | None = None) -> None:
        self.settings = get_settings()
        self.stubbed = not self.settings.google_client_id
        self.token = token or {}

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
        if self.stubbed:
            return {"emailAddress": "stub@example.com", "historyId": "1000"}
        return self._get("/profile")

    def new_messages(self, history_id: str) -> tuple[list[dict], str]:
        """Returns (messages, new_history_id). Empty history_id = fresh connect:
        NO backfill — set the watermark to now and only ever process new mail
        arriving in the Primary inbox from this point on."""
        if self.stubbed:
            if history_id:  # incremental after stub backfill: nothing new
                return [], history_id
            return _stub_mailbox(), "1000"

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
                # Gmail expires old history cursors (404). After a long outage
                # the only honest move is to reset the watermark to now.
                if exc.response.status_code == 404 and not page:
                    return [], str(self.profile()["historyId"])
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
                if exc.response.status_code in (403, 404, 410):
                    continue
                raise
        return [m for m in msgs if m], new_hid

    def backfill(self, n: int = 40) -> list[dict]:
        """Recent Primary-inbox mail for a first fill: plain list + fetch,
        no history cursor. Category tabs (promos/social/updates) are
        filtered by _parse, same as live sync."""
        if self.stubbed:
            return []
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

    def _parse(self, raw: dict) -> dict | None:
        labels = set(raw.get("labelIds", []))
        if "INBOX" not in labels or labels & SKIP_LABELS:
            return None  # not new Primary-inbox mail
        headers = {h["name"].lower(): h["value"]
                   for h in raw.get("payload", {}).get("headers", [])}
        name, addr = parseaddr(headers.get("from", ""))

        def body_of(part) -> str:
            if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                return base64.urlsafe_b64decode(part["body"]["data"]).decode(errors="ignore")
            return "".join(body_of(p) for p in part.get("parts", []))

        return {
            "gmail_msg_id": raw["id"], "thread_id": raw.get("threadId", ""),
            "from_name": name or addr, "from_addr": addr,
            "subject": headers.get("subject", ""),
            "body_text": (body_of(raw.get("payload", {})) or raw.get("snippet", ""))[:MAX_BODY_CHARS],
            "received_at": datetime.fromtimestamp(
                int(raw.get("internalDate", 0)) / 1000, tz=timezone.utc
            ).isoformat(),
        }

    # -- actions -------------------------------------------------------------
    def send_reply(self, *, to_addr: str, subject: str, body: str, thread_id: str) -> str:
        if self.stubbed:
            return f"stub-sent-{int(time.time())}"
        mime = MIMEText(body)
        mime["To"] = to_addr
        mime["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
        return self._post("/messages/send", {"raw": raw, "threadId": thread_id})["id"]

    def send_new(self, *, to_addr: str, subject: str, body: str) -> str:
        if self.stubbed:
            return f"stub-sent-new-{int(time.time())}"
        mime = MIMEText(body)
        mime["To"] = to_addr
        mime["Subject"] = subject
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
        return self._post("/messages/send", {"raw": raw})["id"]

    def archive(self, gmail_msg_id: str) -> None:
        if self.stubbed:
            return
        self._post(f"/messages/{gmail_msg_id}/modify", {"removeLabelIds": ["INBOX"]})

    def watch(self) -> datetime | None:
        """Register Pub/Sub push. Re-call before expiry (~7 days)."""
        if self.stubbed or not self.settings.gmail_pubsub_topic:
            return None
        data = self._post("/watch", {"topicName": self.settings.gmail_pubsub_topic,
                                     "labelIds": ["INBOX"]})
        return datetime.fromtimestamp(int(data["expiration"]) / 1000, tz=timezone.utc)


def _stub_mailbox() -> list[dict]:
    now = datetime.now(timezone.utc)

    def m(hours_ago, mid, name, addr, subject, body):
        return {"gmail_msg_id": f"stub-{mid}", "thread_id": f"stub-t-{mid}",
                "from_name": name, "from_addr": addr, "subject": subject,
                "body_text": body,
                "received_at": (now - timedelta(hours=hours_ago)).isoformat()}

    return [
        m(9, "eureka", "Priya Sharma", "priya@eureka.io", "Eureka! submission — deadline today EOD",
          "Hi — final reminder that your Eureka! accelerator application closes today at 6pm. "
          "You still need to confirm your demo slot. Can you reply with a yes/no and a time?"),
        m(11, "marcus", "Marcus Reed", "marcus@sunriseprop.com", "Lease renewal — need your decision by Friday",
          "Hi, your lease is up at the end of next month. Happy to renew for twelve months at the "
          "same rent. Could you confirm by Friday so I can send the paperwork?"),
        m(6, "mom", "Amma", "amma@gmail.com", "Sunday?",
          "Are you coming home on Sunday? Making biryani. Let me know by tomorrow."),
        m(14, "aws", "AWS Billing", "no-reply@aws.amazon.com", "Your AWS bill is available",
          "Your invoice for August is now available. Total: $12.40. No action is required."),
        m(20, "figma", "Figma", "team@figma.com", "Your file was moved",
          "A file you own was moved to a new project by a teammate. No action needed."),
        m(8, "myntra", "Myntra", "orders@myntra.com", "Order shipped: Navy oxford shirt",
          "Your order #MN4821 (Roadster Navy Oxford Shirt, size M, Rs. 1,299) has shipped and "
          "arrives Thursday."),
        m(26, "substack", "Money Stuff", "mattlevine@substack.com", "Private credit is eating the world",
          "Long newsletter about private credit markets..."),
        m(30, "linkedin", "LinkedIn", "notifications@linkedin.com", "You appeared in 12 searches",
          "See who's looking at your profile. Upgrade to Premium."),
        m(33, "uniqlo", "UNIQLO", "promo@uniqlo.com", "48 HOURS ONLY: extra 30% off",
          "Flash sale on everything. Shop now before it ends."),
        m(40, "zomato", "Zomato", "offers@zomato.com", "Craving something? 60% off tonight",
          "Use code HUNGRY60 tonight only."),
        m(45, "medium", "Medium Daily", "digest@medium.com", "Stories for you",
          "Today's picks based on your reading history."),
        m(50, "twitter", "X", "info@x.com", "You have 3 new followers",
          "See who followed you this week."),
    ]
