"""Finance domain twin operations — the only module that touches the finance
tables. Sync/upsert from Plaid, plus the aggregates agents see in their slice.
"""
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import FinanceAccount, FinanceTransaction, PlaidItem


def upsert_item(db: Session, *, user_id: str, item_id: str, institution: str) -> PlaidItem:
    item = db.scalar(select(PlaidItem).where(PlaidItem.user_id == user_id, PlaidItem.item_id == item_id))
    if item is None:
        item = PlaidItem(user_id=user_id, item_id=item_id, institution=institution)
        db.add(item)
        db.flush()
    return item


def upsert_accounts(db: Session, *, user_id: str, item_id: str, accounts: list[dict]) -> None:
    for a in accounts:
        existing = db.scalar(
            select(FinanceAccount).where(
                FinanceAccount.user_id == user_id,
                FinanceAccount.plaid_account_id == a["account_id"],
            )
        )
        if existing is None:
            db.add(FinanceAccount(
                user_id=user_id, plaid_account_id=a["account_id"], item_id=item_id,
                name=a.get("name", ""), type=a.get("type", ""), mask=a.get("mask"),
            ))
    db.flush()


def upsert_transactions(db: Session, *, user_id: str, txns: list[dict]) -> int:
    """Idempotent upsert by plaid_txn_id. Returns how many were new."""
    new = 0
    for t in txns:
        existing = db.scalar(
            select(FinanceTransaction).where(
                FinanceTransaction.user_id == user_id,
                FinanceTransaction.plaid_txn_id == t["transaction_id"],
            )
        )
        parsed_date = datetime.combine(date.fromisoformat(t["date"]), time.min, tzinfo=timezone.utc)
        if existing is None:
            db.add(FinanceTransaction(
                user_id=user_id, plaid_txn_id=t["transaction_id"], account_id=t["account_id"],
                date=parsed_date, name=t["name"], merchant=t.get("merchant"),
                amount=t["amount"], category=t.get("category", "OTHER"),
                pending=t.get("pending", False),
            ))
            new += 1
        else:
            existing.amount = t["amount"]
            existing.category = t.get("category", existing.category)
            existing.pending = t.get("pending", False)
    db.flush()
    return new


def recent_transactions(db: Session, *, user_id: str, days: int = 62) -> list[FinanceTransaction]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    return list(db.scalars(
        select(FinanceTransaction)
        .where(FinanceTransaction.user_id == user_id, FinanceTransaction.date >= since)
        .order_by(FinanceTransaction.date.desc())
    ))


def finance_context(db: Session, user_id: str) -> dict:
    """The finance slice of ContextSlice.domain_data: month-to-date by category,
    last month for comparison, recent transactions, linked accounts."""
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    txns = recent_transactions(db, user_id=user_id)

    def spend_by_category(rows) -> dict:
        out: dict[str, float] = {}
        for t in rows:
            if t.amount > 0 and t.category != "INCOME":  # outflows only
                out[t.category] = round(out.get(t.category, 0) + t.amount, 2)
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    def aware(dt: datetime) -> datetime:
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    mtd = [t for t in txns if aware(t.date) >= month_start]
    last_month = [t for t in txns if aware(t.date) < month_start]
    accounts = list(db.scalars(select(FinanceAccount).where(FinanceAccount.user_id == user_id)))

    return {
        "month_to_date": {
            "spend": round(sum(t.amount for t in mtd if t.amount > 0 and t.category != "INCOME"), 2),
            "income": round(-sum(t.amount for t in mtd if t.amount < 0), 2),
            "by_category": spend_by_category(mtd),
        },
        "prior_period_spend_by_category": spend_by_category(last_month),
        "recent_transactions": [
            {
                "id": t.id, "date": t.date.date().isoformat(), "name": t.name,
                "merchant": t.merchant, "amount": t.amount, "category": t.category,
            }
            for t in txns[:40]
        ],
        "accounts": [{"name": a.name, "type": a.type, "mask": a.mask} for a in accounts],
    }
