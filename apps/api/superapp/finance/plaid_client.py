"""Thin Plaid client (four endpoints; no SDK). Stub mode when no client_id is
configured: deterministic fake accounts + ~60 days of transactions with the
patterns the agent must detect (recurring bills, biweekly salary, an anomaly),
so the whole vertical runs offline and the tests never touch the network.
"""
import hashlib
import uuid
from datetime import datetime, time, timedelta, timezone

import httpx

from ..config import get_settings

PLAID_HOSTS = {
    "sandbox": "https://sandbox.plaid.com",
    "development": "https://development.plaid.com",
    "production": "https://production.plaid.com",
}


class PlaidClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.stubbed = not self.settings.plaid_client_id

    def _post(self, path: str, payload: dict) -> dict:
        body = {
            "client_id": self.settings.plaid_client_id,
            "secret": self.settings.plaid_secret,
            **payload,
        }
        resp = httpx.post(f"{PLAID_HOSTS[self.settings.plaid_env]}{path}", json=body, timeout=30)
        resp.raise_for_status()
        return resp.json()

    # -- link -----------------------------------------------------------------
    def sandbox_link(self) -> tuple[str, str]:
        """Skip the Link UI (sandbox/stub): returns (access_token, item_id)."""
        if self.stubbed:
            return "stub-access-token", "stub-item-1"
        pub = self._post(
            "/sandbox/public_token/create",
            {"institution_id": "ins_109508", "initial_products": ["transactions"]},
        )
        return self.exchange_public_token(pub["public_token"])

    def hosted_link_url(self, *, user_id: str, webhook_url: str | None) -> str:
        """Hosted Link for real banks — works from Expo Go (opens in the browser)."""
        if self.stubbed:
            return "https://example.com/stub-hosted-link"
        payload = {
            "client_name": "Super App",
            "language": "en",
            "country_codes": ["US"],
            "user": {"client_user_id": user_id},
            "products": ["transactions"],
            "hosted_link": {},
        }
        if webhook_url:
            payload["webhook"] = webhook_url
        return self._post("/link/token/create", payload)["hosted_link_url"]

    def exchange_public_token(self, public_token: str) -> tuple[str, str]:
        data = self._post("/item/public_token/exchange", {"public_token": public_token})
        return data["access_token"], data["item_id"]

    # -- data -----------------------------------------------------------------
    def accounts(self, access_token: str) -> list[dict]:
        if self.stubbed:
            return [
                {"account_id": "stub-checking", "name": "Stub Checking", "type": "depository", "mask": "0000"},
                {"account_id": "stub-credit", "name": "Stub Credit Card", "type": "credit", "mask": "1111"},
            ]
        return [
            {
                "account_id": a["account_id"],
                "name": a.get("name", ""),
                "type": a.get("type", ""),
                "mask": a.get("mask"),
            }
            for a in self._post("/accounts/get", {"access_token": access_token})["accounts"]
        ]

    def sync_transactions(self, access_token: str, cursor: str) -> tuple[list[dict], str]:
        """Returns (added_or_modified, next_cursor). Removed txns ignored for now."""
        if self.stubbed:
            if cursor == "stub-done":  # idempotent: nothing new after first sync
                return [], "stub-done"
            return _stub_transactions(), "stub-done"

        added: list[dict] = []
        while True:
            data = self._post(
                "/transactions/sync",
                {"access_token": access_token, "cursor": cursor or None, "count": 500},
            )
            for t in data["added"] + data["modified"]:
                added.append(
                    {
                        "transaction_id": t["transaction_id"],
                        "account_id": t["account_id"],
                        "date": t.get("authorized_date") or t["date"],
                        "name": t.get("name", ""),
                        "merchant": t.get("merchant_name"),
                        "amount": t["amount"],
                        "category": (t.get("personal_finance_category") or {}).get("primary", "OTHER"),
                        "pending": t.get("pending", False),
                    }
                )
            cursor = data["next_cursor"]
            if not data["has_more"]:
                return added, cursor


def _txn_id(*parts) -> str:
    return "stub-" + hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _stub_transactions() -> list[dict]:
    """~60 days ending today. Deterministic (ids hash from content, dates relative
    to today) so re-syncs upsert cleanly."""
    today = datetime.now(timezone.utc).date()
    txns: list[dict] = []

    def add(days_ago: int, name: str, merchant: str | None, amount: float, category: str,
            account: str = "stub-checking") -> None:
        d = today - timedelta(days=days_ago)
        txns.append({
            "transaction_id": _txn_id(d.isoformat(), name, amount),
            "account_id": account,
            "date": d.isoformat(),
            "name": name,
            "merchant": merchant,
            "amount": amount,
            "category": category,
            "pending": False,
        })

    for days_ago in range(0, 62):
        d = today - timedelta(days=days_ago)
        if d.day == 1:
            add(days_ago, "Rent payment", "Sunrise Property Mgmt", 1800.00, "RENT_AND_UTILITIES")
        if d.day == 5:
            add(days_ago, "Netflix", "Netflix", 15.49, "ENTERTAINMENT", "stub-credit")
        if d.day == 12:
            add(days_ago, "ConEd electric", "ConEd", 84.20, "RENT_AND_UTILITIES")
        if d.day in (1, 15):
            add(days_ago, "ACME Corp payroll", "ACME Corp", -4200.00, "INCOME")
        if d.weekday() == 5:  # Saturdays: groceries
            add(days_ago, "Whole Foods", "Whole Foods", 80.0 + (d.day % 5) * 11.0, "FOOD_AND_DRINK")
        if d.weekday() in (1, 3):  # dining out
            add(days_ago, "Sweetgreen", "Sweetgreen", 16.50 + (d.day % 3) * 2.0, "FOOD_AND_DRINK", "stub-credit")
    # One anomaly, three days ago.
    add(3, "B&H Photo", "B&H", 342.99, "GENERAL_MERCHANDISE", "stub-credit")
    return txns
