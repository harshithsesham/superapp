"""When will they run out?

The two shelves at the bottom of the screen — Running low, Out of stock — are
this file's output, so it is worth being precise about what it does and does
not know.

It is deterministic arithmetic, not a model call. It runs on every item on
every render, so it has to be free. Its answer has to be explainable to the
person looking at a red shelf. And a model asked to guess a repurchase interval
will happily invent one, which is exactly the failure that makes a prediction
feature untrustworthy.

## What it measures

A rate of CONSUMPTION, in amount per day — not an interval between trips.

The first version measured intervals, and it had a bug worth remembering. It
divided each gap by how much was bought (correct: two cartons last twice as
long), then predicted from the interval alone and ignored how much the LAST
purchase contained. So buying two cartons of a seven-day milk eight days ago
came out "out", when its own arithmetic implied six days left. The lesson is
that quantity has to appear on both sides — in the learning and in the
prediction — or the model contradicts itself.

Amounts are normalised (`units.py`), so a gallon and 128 fl oz are the same
quantity of milk. When a product's own history mixes dimensions, or has no
sizes at all, the maths falls back to counting packages — coarser, still
consistent.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import median

from .units import parse_size

# How long ONE package of a category typically lasts one household, in days.
# Deliberately coarse: these exist to be replaced by the person's own data as
# soon as there is any, not to be accurate on their own.
CATEGORY_DAYS = {
    "fresh produce": 7,
    "dairy & protein": 7,
    "grains": 45,
    "snacks": 14,
    "beverages": 10,
    "household essentials": 45,
}
DEFAULT_DAYS = 21

MIN_DAYS, MAX_DAYS = 1, 365
LOW_FRACTION = 0.25       # inside the last quarter of its life, it is running low
LOW_FLOOR_DAYS = 2        # ...and always at least the last two days

STOCKED, LOW, OUT = "stocked", "running_low", "out"


@dataclass
class Bought:
    """One purchase, with enough detail to be comparable to the others."""
    at: datetime
    quantity: float = 1.0            # how many packages
    pack_amount: float | None = None  # size of ONE package, normalised
    pack_unit: str = ""              # ml | g | ct


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def as_bought(raw) -> Bought:
    """Accepts a Bought, a (date, qty) pair, or (date, qty, size_text)."""
    if isinstance(raw, Bought):
        return Bought(_aware(raw.at), max(float(raw.quantity or 1), 0.01),
                      raw.pack_amount, raw.pack_unit)
    at, qty, *rest = raw
    amount, unit = (None, "")
    if rest and rest[0]:
        parsed = parse_size(rest[0]) if isinstance(rest[0], str) else None
        if parsed:
            amount, unit = parsed
    return Bought(_aware(at), max(float(qty or 1), 0.01), amount, unit)


def _amounts(purchases: list) -> tuple[list[Bought], str]:
    """Every purchase measured on one scale.

    Returns the purchases and the unit in play. If the sizes disagree about
    what they are measuring — millilitres in one row, grams in another — no
    comparison is meaningful, so both are dropped and packages are counted
    instead. Coarser and honest beats precise and wrong.
    """
    rows = [as_bought(p) for p in purchases]
    units = {r.pack_unit for r in rows if r.pack_amount and r.pack_unit}
    if len(units) == 1 and all(r.pack_amount for r in rows):
        return rows, units.pop()
    for r in rows:
        r.pack_amount, r.pack_unit = None, ""
    return rows, "pack"


def amount_of(b: Bought) -> float:
    """How much stuff one purchase put in the cupboard."""
    return b.quantity * (b.pack_amount if b.pack_amount else 1.0)


def consumption_rate(purchases: list) -> tuple[float | None, str, str]:
    """Amount consumed per day, learned from the person's own repurchases.

    Between two trips, roughly what was bought on the first trip got used up.
    That is the only assumption in here, and it is the one a shopping habit
    actually encodes.
    """
    rows, unit = _amounts(purchases)
    rows.sort(key=lambda r: r.at)
    rates = []
    for earlier, later in zip(rows, rows[1:]):
        gap = (later.at - earlier.at).total_seconds() / 86400.0
        if gap <= 0:
            continue          # same-day top-up, not a repurchase cycle
        rates.append(amount_of(earlier) / gap)
    if len(rates) >= 2:
        return median(rates), "measured", unit
    if len(rates) == 1:
        return rates[0], "estimated", unit
    return None, "assumed", unit


@dataclass
class Forecast:
    """What we think, and how much of it we actually know."""
    status: str               # stocked | running_low | out
    days_supply: float        # how long the LAST purchase should last
    days_left: float          # negative means overdue
    out_on: datetime | None
    basis: str                # measured | estimated | assumed | declared
    reason: str               # one plain line, shown to the person

    def as_dict(self) -> dict:
        return {"status": self.status, "days_supply": round(self.days_supply, 1),
                "days_left": round(self.days_left, 1), "basis": self.basis,
                "reason": self.reason,
                "out_on": self.out_on.date().isoformat() if self.out_on else ""}


def _clamp(v: float) -> float:
    return max(MIN_DAYS, min(MAX_DAYS, float(v)))


def forecast(*, purchases: list, category: str = "",
             last_purchased_at: datetime | None = None,
             declared_out_at: datetime | None = None,
             now: datetime | None = None) -> Forecast:
    """The shelf's verdict for one item.

    `declared_out_at` is the person saying so themselves. It outranks every
    calculation and is never argued with: they can see their own cupboard.
    """
    now = _aware(now or datetime.now(timezone.utc))
    rows = sorted((as_bought(p) for p in purchases), key=lambda r: r.at)
    prior_days = float(CATEGORY_DAYS.get((category or "").strip().lower(), DEFAULT_DAYS))

    last = rows[-1] if rows else None
    last_at = _aware(last_purchased_at) if last_purchased_at else (last.at if last else None)

    # A person's own eyes beat any arithmetic — unless they have shopped since.
    if declared_out_at is not None:
        declared = _aware(declared_out_at)
        if last_at is None or declared >= last_at:
            return Forecast(status=OUT, days_supply=0.0, days_left=0.0, out_on=declared,
                            basis="declared", reason="you marked this out")

    if last is None or last_at is None:
        return Forecast(status=STOCKED, days_supply=prior_days, days_left=prior_days,
                        out_on=None, basis="assumed",
                        reason="No purchase history yet.")

    rate, basis, _unit = consumption_rate(rows)
    if rate and rate > 0:
        # How long the LAST purchase should last: how much it was, over how
        # fast this household gets through it. Two cartons last twice as long
        # as one — the half the first version forgot.
        #
        # Measured on the same scale the rate was learned on, which is why the
        # amount comes back through _amounts rather than off `last` directly:
        # if the sizes disagreed, both sides fall back to counting packages.
        scaled, _ = _amounts(rows)
        bought_last = amount_of(scaled[-1])
        supply = _clamp(bought_last / rate)
        per_pack = _clamp((bought_last / max(last.quantity, 0.01)) / rate)
    else:
        # No repeat yet: the category prior is per PACKAGE, so buying three
        # still means three packages' worth of time.
        supply = _clamp(prior_days * last.quantity)
        per_pack = _clamp(prior_days)

    elapsed = (now - last_at).total_seconds() / 86400.0
    days_left = supply - elapsed
    out_on = last_at + timedelta(days=supply)
    threshold = max(LOW_FLOOR_DAYS, supply * LOW_FRACTION)

    if days_left <= 0:
        status = OUT
    elif days_left <= threshold:
        status = LOW
    else:
        status = STOCKED

    bulk = (f" you bought {last.quantity:g}, so about {supply:.0f} days' worth"
            if last.quantity > 1 else "")
    how = {"measured": f"one lasts you about {per_pack:.0f} days;",
           "estimated": f"looks like one lasts about {per_pack:.0f} days, on one repeat so far;",
           "assumed": f"one typically lasts about {per_pack:.0f} days — no repeat from you yet;"}[basis]
    if days_left > 0:
        when = "about %.0f days left" % days_left
    elif days_left > -1:
        when = "due about now"
    else:
        when = "overdue by %.0f days" % abs(days_left)
    return Forecast(status=status, days_supply=supply, days_left=days_left, out_on=out_on,
                    basis=basis, reason=f"{how}{bulk + ',' if bulk else ''} {when}")
