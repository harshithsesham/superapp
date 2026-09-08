"""The offline mailbox, as a first-class provider.

This used to be a mode that any client could silently fall into: `stubbed`
was computed from a global setting, so a real mailbox missing its token
became a fake one, and `send_reply` returned an invented id that the app
recorded as a delivered message. A person was told "Sent." for mail that
never left.

Now being fake is a property of the ACCOUNT, not of the environment. A
mailbox linked through /v1/inbox/connect/stub carries provider="stub" and
gets this client. Everything else gets a real client or an error.

Sends are recorded in `SENT`, which is what tests assert against and what
makes "did it actually go out" answerable offline.
"""
import time
from datetime import datetime, timedelta, timezone

STUB_ADDRESS = "stub@example.com"

# Every send this process has made through a stub mailbox, newest last.
SENT: list[dict] = []


def stub_mailbox() -> list[dict]:
    """A deterministic mailbox spanning every triage tier: urgent asks, FYIs,
    receipts, newsletters and promos, so the whole vertical runs offline."""
    now = datetime.now(timezone.utc)

    def m(hours_ago, mid, name, addr, subject, body):
        return {"gmail_msg_id": f"stub-{mid}", "thread_id": f"stub-t-{mid}",
                "from_name": name, "from_addr": addr, "subject": subject,
                "auto_submitted": False,
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


class StubMailClient:
    """Implements MailClient against an in-process fake mailbox."""

    provider = "stub"

    def __init__(self, token: dict | None = None, on_token_refresh=None,
                 address: str = STUB_ADDRESS) -> None:
        self.token = token or {}
        self._address = address or STUB_ADDRESS

    # -- linking -------------------------------------------------------------
    def auth_url(self, state: str) -> str:
        return f"https://example.invalid/stub-consent?state={state}"

    def exchange_code(self, code: str) -> dict:
        return {"access_token": "stub", "refresh_token": "stub",
                "expiry_ts": time.time() + 3600}

    def address(self) -> str:
        return self._address

    # -- reading -------------------------------------------------------------
    def new_messages(self, cursor: str) -> tuple[list[dict], str]:
        if cursor:  # already filled once; the fake mailbox never grows
            return [], cursor
        if self._address != STUB_ADDRESS:
            # The fixture is THE demo mailbox. A second offline mailbox is a
            # placeholder for hand-made rows, and must not deal the same
            # messages a second time under a different address.
            return [], "1000"
        return stub_mailbox(), "1000"

    def backfill(self, n: int = 40) -> list[dict]:
        return []

    def history_page(self, *, since: datetime, until: datetime, page_token: str = "") -> tuple[list[dict], str]:
        return [], ""

    def history(self, *, months: int = 36, limit: int = 1500) -> list[dict]:
        return []   # a fake mailbox has no past worth recording

    # -- writing -------------------------------------------------------------
    def send_reply(self, *, to_addr: str, subject: str, body: str, thread_id: str,
                   external_id: str = "", auto: bool = False) -> str:
        sent_id = f"stub-sent-{len(SENT)}-{int(time.time())}"
        SENT.append({"id": sent_id, "kind": "reply", "to": to_addr, "subject": subject,
                     "body": body, "thread_id": thread_id, "auto": auto})
        return sent_id

    def send_new(self, *, to_addr: str, subject: str, body: str) -> str:
        sent_id = f"stub-sent-new-{len(SENT)}-{int(time.time())}"
        SENT.append({"id": sent_id, "kind": "new", "to": to_addr,
                     "subject": subject, "body": body, "auto": False})
        return sent_id

    def archive(self, external_id: str) -> str | None:
        return None

    def subscribe(self) -> tuple[datetime | None, str]:
        return None, ""
