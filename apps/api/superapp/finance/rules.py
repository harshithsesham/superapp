"""Procedural rules engine (roadmap Phase 2): deterministic, not LLM.

Runs on every transactions sync. Three detectors:
- Budget thresholds: budget facts (finance/budget:{CATEGORY}) vs month-to-date
  spend; warn once per month at 80% and at 100%.
- Anomalies: an outflow far outside the merchant-free typical range.
- Recurring charges: same merchant, similar amount, ~monthly cadence.

Pure functions over twin rows + facts. The finance agent turns findings into
events/facts/pushes; this module only detects.
"""
import statistics
from collections import defaultdict
from datetime import datetime, timezone


def budget_status(*, budgets: dict[str, float], mtd_by_category: dict[str, float]) -> list[dict]:
    """budgets: {category: monthly_limit}. Returns threshold crossings."""
    findings = []
    for category, limit in budgets.items():
        spent = mtd_by_category.get(category, 0.0)
        if limit <= 0:
            continue
        pct = spent / limit
        if pct >= 1.0:
            findings.append({"kind": "budget_exceeded", "category": category,
                             "spent": round(spent, 2), "limit": limit, "pct": round(pct * 100)})
        elif pct >= 0.8:
            findings.append({"kind": "budget_warning", "category": category,
                             "spent": round(spent, 2), "limit": limit, "pct": round(pct * 100)})
    return findings


def detect_anomalies(txns: list[dict], *, recurring_merchants: set[str] | None = None,
                     min_amount: float = 100.0, factor: float = 2.0) -> list[dict]:
    """Unusually large one-off outflows. Known recurring merchants (rent, bills)
    are excluded from both the baseline and the findings — they're big but
    expected. Threshold: factor x the 90th percentile of ordinary outflows."""
    recurring_merchants = recurring_merchants or set()
    ordinary = [
        t["amount"] for t in txns
        if t["amount"] > 0 and (t.get("merchant") or t["name"]) not in recurring_merchants
    ]
    if len(ordinary) < 10:
        return []
    p90 = statistics.quantiles(ordinary, n=10)[8]
    threshold = max(min_amount, factor * p90)
    recent_cut = 7  # only flag things from the last week; older ones were already seen
    now = datetime.now(timezone.utc).date()
    return [
        {"kind": "anomaly", "name": t["name"], "merchant": t.get("merchant"),
         "amount": t["amount"], "date": t["date"], "threshold": round(threshold, 2)}
        for t in txns
        if t["amount"] >= threshold
        and (t.get("merchant") or t["name"]) not in recurring_merchants
        and (now - datetime.strptime(t["date"], "%Y-%m-%d").date()).days <= recent_cut
    ]


def detect_recurring(txns: list[dict], *, tolerance: float = 0.15) -> list[dict]:
    """Groups outflows by merchant; recurring = 2+ charges, similar amounts,
    25–35 day spacing (monthly-ish). Returns one finding per merchant."""
    by_merchant: dict[str, list[dict]] = defaultdict(list)
    for t in txns:
        if t["amount"] > 0 and t.get("merchant"):
            by_merchant[t["merchant"]].append(t)

    findings = []
    for merchant, rows in by_merchant.items():
        if len(rows) < 2:
            continue
        rows.sort(key=lambda t: t["date"])
        amounts = [t["amount"] for t in rows]
        mean_amt = statistics.mean(amounts)
        if any(abs(a - mean_amt) > tolerance * mean_amt for a in amounts):
            continue
        gaps = [
            (datetime.strptime(b["date"], "%Y-%m-%d") - datetime.strptime(a["date"], "%Y-%m-%d")).days
            for a, b in zip(rows, rows[1:])
        ]
        if gaps and all(25 <= g <= 35 for g in gaps):
            findings.append({"kind": "recurring", "merchant": merchant,
                             "amount": round(mean_amt, 2), "occurrences": len(rows),
                             "last_date": rows[-1]["date"]})
    return findings


def detect_income_cadence(txns: list[dict]) -> dict | None:
    """Income deposits: infer cadence from gaps (biweekly/semimonthly/monthly)."""
    deposits = sorted(
        (t for t in txns if t["amount"] < 0 and t.get("category") == "INCOME"),
        key=lambda t: t["date"],
    )
    if len(deposits) < 3:
        return None
    gaps = [
        (datetime.strptime(b["date"], "%Y-%m-%d") - datetime.strptime(a["date"], "%Y-%m-%d")).days
        for a, b in zip(deposits, deposits[1:])
    ]
    avg = statistics.mean(gaps)
    cadence = "biweekly" if 12 <= avg <= 17 else "monthly" if 26 <= avg <= 35 else "irregular"
    return {"cadence": cadence, "avg_gap_days": round(avg, 1),
            "typical_amount": round(-statistics.median(t["amount"] for t in deposits), 2)}
