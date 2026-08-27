"""(Re)register Gmail Pub/Sub watches for every connected account.

Watches expire after ~7 days; cron this daily. Also run once at setup to
start push delivery. Requires SUPERAPP_GMAIL_PUBSUB_TOPIC.
"""
import json

from superapp.db import SessionLocal
from superapp.inbox.gmail_client import GmailClient
from superapp.models import GmailAccount
from superapp.vault import get_token


def main() -> None:
    db = SessionLocal()
    for acct in db.query(GmailAccount).all():
        token = get_token(db, user_id=acct.user_id, provider=f"gmail:{acct.email}")
        if not token:
            print(f"{acct.email}: no token, skipped")
            continue
        expiry = GmailClient(json.loads(token)).watch()
        acct.watch_expiry = expiry
        print(f"{acct.email}: watch until {expiry}")
    db.commit()
    db.close()


if __name__ == "__main__":
    main()
