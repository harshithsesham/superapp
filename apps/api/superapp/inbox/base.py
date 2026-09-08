"""The mail-provider seam.

Everything above this line — triage, drafting, the auto-reply window, the
rules engine — deals in a `MailClient`, never in Gmail. A provider is a class
implementing this protocol plus an entry in `factory.client_for`.

Two rules the protocol exists to enforce, both learned the hard way:

1. A client is built FOR AN ACCOUNT, never from ambient config. The old code
   decided "am I a stub?" from a global setting, so a real mailbox with no
   stored token quietly became a fake one and reported imaginary sends as
   real. `factory.client_for` raises instead.

2. A refresh may rotate the refresh token. Google's does not; Microsoft's
   does, and a client that mutates its token in memory without writing back
   kills the mailbox days later with no obvious cause. Hence
   `on_token_refresh`, supplied by the factory.
"""
from typing import Callable, Protocol, runtime_checkable

from datetime import datetime


class MailError(Exception):
    """Base for provider problems the app is expected to handle."""


class MailNotConnected(MailError):
    """No usable credential for this mailbox: never connected, or signed out.

    Raised INSTEAD of falling back to a stub, so a send that cannot happen
    fails loudly rather than being reported as delivered.
    """


class ReauthRequired(MailError):
    """The provider rejected our credential; the person must sign in again."""


class HistoryExpired(MailError):
    """Incremental history is gone. Retain the cursor until full recovery finishes."""


TokenWriter = Callable[[dict], None]


@runtime_checkable
class MailClient(Protocol):
    """What the inbox pipeline needs from a mail provider.

    `provider` is the stable key stored on the account row and used as the
    vault namespace, so "gmail:someone@example.com" keeps naming exactly what
    it named before this protocol existed.
    """

    provider: str

    # -- linking -------------------------------------------------------------
    def auth_url(self, state: str) -> str:
        """Where to send the person to grant access. `state` is HMAC-signed."""
        ...

    def exchange_code(self, code: str) -> dict:
        """Authorization code -> {access_token, refresh_token, expiry_ts}."""
        ...

    def address(self) -> str:
        """The mailbox's own address, used as its identity everywhere."""
        ...

    # -- reading -------------------------------------------------------------
    def new_messages(self, cursor: str) -> tuple[list[dict], str]:
        """Mail since `cursor`, plus the next cursor. The cursor is opaque to
        callers: Gmail stores a history id, Graph stores a delta link.

        An empty cursor means a fresh connection: return no mail and a cursor
        pointing at now, so linking a mailbox never floods the inbox.
        """
        ...

    def backfill(self, n: int = 40) -> list[dict]:
        """Recent mail for a first fill, ignoring the cursor."""
        ...

    def history_page(self, *, since: datetime, until: datetime, page_token: str = "") -> tuple[list[dict], str]:
        """Historical context page; a separate path from actionable inbox sync."""
        ...

    def history(self, *, months: int = 36, limit: int = 1500) -> list[dict]:
        """Past conversation for context: sent AND received, inbox and archive.

        Deliberately not a bigger `backfill`. What backfill returns enters the
        QUEUE, so it gets triaged, drafted for and possibly archived — which is
        why it stays small and recent. What this returns goes only to
        `mail_history`, which no action path reads. A provider that cannot
        offer this returns an empty list; it must never return queue mail here.

        Each dict carries `direction`: "outbound" for mail the user sent,
        "inbound" for mail they received. The outbound half is the whole point
        — without it, "have I ever replied to this person" is a guess.
        """
        ...

    # -- writing -------------------------------------------------------------
    def send_reply(self, *, to_addr: str, subject: str, body: str, thread_id: str,
                   external_id: str = "", auto: bool = False) -> str:
        """Reply within an existing conversation; returns the sent message id.

        Both identifiers are passed because providers disagree about what a
        reply attaches to: Gmail threads on `thread_id`, Graph replies to a
        specific message, which is `external_id`.

        `auto` stamps the automatic-mail headers that stop two assistants
        answering each other forever.
        """
        ...

    def send_new(self, *, to_addr: str, subject: str, body: str) -> str:
        """Start a new conversation; returns the sent message id."""
        ...

    def archive(self, external_id: str) -> str | None:
        """Move mail out of the inbox. Returns a NEW id when the provider
        changes it on move, otherwise None. Gmail keeps the id; Graph may not.
        """
        ...

    def subscribe(self) -> tuple[datetime | None, str]:
        """Register push notifications. Returns (expiry, subscription_id);
        (None, "") when the provider or deployment has none configured.
        """
        ...
