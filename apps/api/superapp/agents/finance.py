"""Finance agent (Phase 2) — "Rocket Money but personal".

think() trigger kinds:
- transactions_sync (webhook/cron/link): Plaid sync -> twins, then the
  deterministic rules engine. New anomalies and budget crossings become events,
  facts, and (if urgent) a push. Recurring bills + income cadence become facts.
- scheduled / user_refresh: the weekly insight — one LLM pass over month
  aggregates + facts. Cron runs use the Batches API.

render() projects: month-to-date vs prior period, budget bars, recent
transactions, latest insight. No linked item -> a link-bank action row.
"""
import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..finance.plaid_client import PlaidClient
from ..finance import rules
from ..llm.provider import LLMProvider
from ..models import PlaidItem
from ..push import send_push
from ..sdui.blocks import (
    Action, ActionRow, InsightCard, ListBlock, ListItem, Screen, Section, Stat, StatRow, TextBlock,
)
from ..substrate import ContextSlice
from ..substrate.finance import upsert_transactions
from ..vault import get_token
from .base import EventWrite, FactWrite, ThinkResult, register_agent

INSIGHT_SYSTEM = (
    "You are the finance agent of a personal money app. Given monthly spending "
    "aggregates, detected patterns, and the user's stated goals, write ONE short "
    "insight (2-4 sentences) that is specific and actionable this week. Reference "
    "concrete numbers. No greetings, no disclaimers, no generic advice."
)


def _budget_facts(context: ContextSlice) -> dict[str, float]:
    return {
        f["key"].removeprefix("budget:"): float(f["value"]["monthly"])
        for f in context.facts
        if f["domain"] == "finance" and f["key"].startswith("budget:")
    }


def _sync_and_apply_rules(db: Session, context: ContextSlice, trigger: dict) -> ThinkResult:
    user_id = context.user_id
    client = PlaidClient()
    result = ThinkResult()

    items = list(db.scalars(select(PlaidItem).where(PlaidItem.user_id == user_id)))
    total_new = 0
    for item in items:
        access_token = get_token(db, user_id=user_id, provider=f"plaid:{item.item_id}")
        if access_token is None:
            continue
        txns, cursor = client.sync_transactions(access_token, item.sync_cursor)
        total_new += upsert_transactions(db, user_id=user_id, txns=txns)
        item.sync_cursor = cursor
    db.flush()
    result.event_writes.append(EventWrite(
        type="transactions_synced", domain="finance",
        payload={"new": total_new, "items": len(items)},
    ))
    if total_new == 0:
        return result

    # Fresh slice of the twin for the rules engine (context predates this sync).
    from ..substrate.finance import finance_context
    data = finance_context(db, user_id)
    recent = data["recent_transactions"]

    recurring = rules.detect_recurring(recent)
    findings = []
    findings += rules.budget_status(
        budgets=_budget_facts(context), mtd_by_category=data["month_to_date"]["by_category"]
    )
    findings += rules.detect_anomalies(
        recent, recurring_merchants={r["merchant"] for r in recurring}
    )
    for f in findings:
        result.event_writes.append(EventWrite(type=f["kind"], domain="finance", payload=f))
    result.fact_writes.append(FactWrite(
        domain="finance", key="active_alerts",
        value={"count": len(findings), "as_of": datetime.now(timezone.utc).date().isoformat()},
        confidence=1.0,
    ))

    if recurring:
        result.fact_writes.append(FactWrite(
            domain="finance", key="recurring_bills",
            value={"monthly_total": round(sum(r["amount"] for r in recurring), 2),
                   "count": len(recurring)},
            confidence=0.8,
        ))
        for r in recurring:
            result.event_writes.append(EventWrite(type="recurring_detected", domain="finance", payload=r))
    income = rules.detect_income_cadence(recent)
    if income:
        result.fact_writes.append(FactWrite(
            domain="finance", key="income_cadence", value=income, confidence=0.8,
        ))

    # Interrupt only for the urgent tier: exceeded budgets and anomalies.
    urgent = [f for f in findings if f["kind"] in ("budget_exceeded", "anomaly")]
    for f in urgent[:2]:
        title = "Budget exceeded" if f["kind"] == "budget_exceeded" else "Unusual charge"
        body = (
            f"{f['category']}: ${f['spent']:.0f} of ${f['limit']:.0f}"
            if f["kind"] == "budget_exceeded"
            else f"{f['name']} — ${f['amount']:.2f}"
        )
        send_push(db, user_id=user_id, title=title, body=body, agent="finance")
        result.actions_taken.append(f"push:{f['kind']}")
    return result


def _weekly_insight(db: Session, context: ContextSlice, trigger: dict) -> ThinkResult:
    data = context.domain_data.get("finance", {})
    if not data.get("recent_transactions"):
        return ThinkResult()

    provider = LLMProvider()
    prompt = json.dumps(
        {
            "month_to_date": data["month_to_date"],
            "prior_period_spend_by_category": data["prior_period_spend_by_category"],
            "facts": [f for f in context.facts if f["domain"] in ("finance", "goals")],
        },
        sort_keys=True,
    )
    if trigger.get("kind") == "scheduled":
        resp = provider.complete_batch(
            db, user_id=context.user_id, agent="finance", task="weekly_insight",
            system=INSIGHT_SYSTEM, prompts={"insight": prompt},
        )["insight"]
    else:
        resp = provider.complete(
            db, user_id=context.user_id, agent="finance", task="weekly_insight",
            system=INSIGHT_SYSTEM, prompt=prompt,
        )
    if resp is None or resp.refused:
        return ThinkResult()

    text = resp.text
    if resp.stubbed:
        mtd = data["month_to_date"]
        top = next(iter(mtd["by_category"].items()), ("nothing", 0))
        text = (f"${mtd['spend']:.0f} spent so far this month; the biggest category is "
                f"{top[0]} at ${top[1]:.0f}.")
    return ThinkResult(fact_writes=[FactWrite(
        domain="finance", key="last_insight",
        value={"date": datetime.now(timezone.utc).date().isoformat(), "text": text[:600]},
        confidence=0.9,
    )])


def finance_think(db: Session, *, trigger: dict, context: ContextSlice, run_id: str) -> ThinkResult:
    if trigger.get("kind") in ("transactions_sync", "link_completed"):
        return _sync_and_apply_rules(db, context, trigger)
    return _weekly_insight(db, context, trigger)


def finance_render(context: ContextSlice) -> Screen:
    data = context.domain_data.get("finance", {})
    accounts = data.get("accounts", [])

    if not accounts:
        return Screen(title="Finance", sections=[Section(title="Get started", blocks=[
            TextBlock(text="Link a bank to pull transactions in automatically."),
            ActionRow(actions=[Action(id="finance.link", label="Link bank")]),
        ])])

    mtd = data["month_to_date"]
    blocks: list = [StatRow(stats=[
        Stat(label="Spent (MTD)", value=f"${mtd['spend']:,.0f}"),
        Stat(label="Top category", value=next(iter(mtd["by_category"]), "—")),
        Stat(label="Accounts", value=str(len(accounts))),
    ])]

    budgets = _budget_facts(context)
    if budgets:
        blocks.append(ListBlock(items=[
            ListItem(
                id=f"budget-{cat}", title=cat.replace("_", " ").title(),
                subtitle=f"${mtd['by_category'].get(cat, 0):,.0f} of ${limit:,.0f}",
                trailing=f"{min(100, round(100 * mtd['by_category'].get(cat, 0) / limit))}%",
            )
            for cat, limit in budgets.items()
        ]))

    insight = next(
        (f for f in context.facts if f["domain"] == "finance" and f["key"] == "last_insight"), None
    )
    if insight:
        blocks.append(InsightCard(
            id="finance-insight", agent="finance",
            title=f"This week — {insight['value'].get('date', '')}",
            body=insight["value"].get("text", ""),
        ))

    txns = data.get("recent_transactions", [])[:12]
    if txns:
        blocks.append(ListBlock(items=[
            ListItem(
                id=t["id"], title=t["merchant"] or t["name"],
                subtitle=f"{t['date']} · {t['category'].replace('_', ' ').title()}",
                trailing=f"{'-' if t['amount'] < 0 else ''}${abs(t['amount']):,.2f}",
            )
            for t in txns
        ]))

    return Screen(title="Finance", sections=[Section(title="This month", blocks=blocks)])


register_agent("finance", render=finance_render, think=finance_think)
