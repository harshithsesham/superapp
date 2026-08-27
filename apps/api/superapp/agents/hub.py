"""The Hub (Nano V4) — the app's home screen and the substrate's show-off:
a render-only agent with wildcard scope that projects every vertical's state
into one glance. The V4 grammar: a greeting, a brief that leads with what
already happened ("Three things done. One question."), a signal-fate timeline
where every input ends in a verdict, then the agent grid. No think tier — the
Hub owns no cognition, only the view.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ..config import get_settings
from ..sdui.blocks import (
    Action, ActionRow, AgentCard, AgentGrid, AgentGridItem, AgentStat,
    ListBlock, ListItem, Screen, Section, TextBlock, Timeline, TimelineItem,
)
from ..substrate import ContextSlice
from .base import register_agent
from .inbox import inbox_hero

_CAPABILITY_LABELS = {
    "inbox.archive_noise": "Files obvious noise",
    "inbox.flag_to_read": "Flags what's worth reading",
    "inbox.send_reply": "Sends replies",
    "nutrition.log_meal": "Logs meals from photos",
    "finance.categorize": "Categorizes spending",
    "stylist.suggest": "Suggests outfits",
}


def _autonomy_section(autonomy: dict) -> Section | None:
    caps = [c for c in autonomy.get("capabilities", [])
            if c["acted"] or c["total_user"]]
    if not caps:
        return None
    items = []
    for c in caps:
        label = _CAPABILITY_LABELS.get(c["action_key"],
                                       c["action_key"].replace(".", " · "))
        if c["promotable"]:
            sub = (f"{c['total_user']} decisions, {c['clean_rate']:.0%} clean — "
                   "earned a promotion. Your call.")
        elif c["level"] >= 3:
            sub = f"{c['acted']} times · every one in the ledger"
        else:
            sub = f"still asks first · {c['total_user']} decisions, {c['clean_rate']:.0%} clean"
            if c["last_demotion_reason"]:
                sub = f"demoted — {c['last_demotion_reason']} · " + sub
        items.append(ListItem(id=c["action_key"], title=label, subtitle=sub))
    return Section(title="Without asking · earned, not configured", blocks=[
        ListBlock(items=items),
        TextBlock(text="Nano earns each of these from your decisions and loses "
                       "it on one undo. There is no settings page.",
                  variant="caption"),
    ])


_WORDS = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"]


def _spell(n: int) -> str:
    return _WORDS[n] if 0 <= n < len(_WORDS) else str(n)


def _greeting(name: str) -> str:
    hour = datetime.now(ZoneInfo(get_settings().default_timezone)).hour
    part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
    return f"Good {part}, {name}."


def _brief_card(data: dict, activity: dict) -> AgentCard:
    inbox = data.get("inbox", {})
    asks = [a for a in inbox.get("needs_reply", [])
            if not (a.get("draft") or {}).get("deferred")]
    questions = len(asks)
    done = sum(1 for i in activity.get("items", []) if i["tone"] != "ask")
    signals = activity.get("signals_today", 0)

    headline = f"{_spell(done)} thing{'s' if done != 1 else ''} done. " + (
        "Nothing needs you." if questions == 0
        else "One question." if questions == 1
        else f"{_spell(questions)} questions.")

    parts = []
    cleared = inbox.get("cleared_count", 0)
    if cleared:
        parts.append(f"filed {cleared} emails you never had to see")
    drafted = sum(1 for a in inbox.get("needs_reply", []) if a.get("draft"))
    if drafted:
        parts.append(f"drafted {drafted} repl{'y' if drafted == 1 else 'ies'}")
    meals = data.get("nutrition", {}).get("today", {}).get("meals", [])
    if meals:
        parts.append(f"logged {len(meals)} meal{'s' if len(meals) != 1 else ''}")
    body = ("While you were away I " + ", ".join(parts) + "."
            if parts else "I'm watching. Nothing has needed a hand yet today.")
    if questions:
        body += (" One thing needs your yes — it's below."
                 if questions == 1 else f" {questions} things need your yes — they're below.")

    return AgentCard(
        id="morning-brief", agent="hub", name="Nano", sub="Your brief",
        live=True, headline=headline, body=body,
        screen="inbox" if questions else None,
        stats=[
            AgentStat(n=str(done), label="done without you", accent=True),
            AgentStat(n=str(questions), label="need your yes"),
            AgentStat(n=str(signals), label="signals read"),
        ],
    )


def hub_render(context: ContextSlice) -> Screen:
    data = context.domain_data
    activity = data.get("activity", {})
    stamp = datetime.now(timezone.utc).strftime("%a %d %b · %H:%M").upper()

    nutrition = data.get("nutrition", {}).get("today", {})
    kcal = nutrition.get("kcal", 0)
    n_meals = len(nutrition.get("meals", []))

    finance = data.get("finance", {})
    linked = bool(finance.get("accounts"))
    mtd = finance.get("month_to_date", {}).get("spend", 0)

    wardrobe = data.get("wardrobe", {})
    garments = len(wardrobe.get("garments", []))
    outfits = len(wardrobe.get("todays_outfits", []))

    grid = AgentGrid(items=[
        AgentGridItem(
            screen="home", name="Nutrition", tone="mint",
            sub=f"{kcal} kcal · {n_meals} meal{'s' if n_meals != 1 else ''} today"
            if n_meals else "Nothing logged today",
        ),
        AgentGridItem(
            screen="finance", name="FinTrack", tone="amber",
            sub=f"${mtd:,.0f} spent this month" if linked else "Link a bank to start",
        ),
        AgentGridItem(
            screen="stylist", name="Stylist", tone="rose",
            sub=(f"{garments} garments · {outfits} looks today" if outfits
                 else f"{garments} garments in the closet") if garments
            else "Photograph your first garment",
        ),
    ])

    has_identity = any(f["domain"] == "identity" for f in context.facts)
    inbox_connected = data.get("inbox", {}).get("connected", False)

    hero_blocks: list = [TextBlock(text=stamp, variant="caption")]
    if not has_identity:
        hero_blocks.append(ActionRow(actions=[
            Action(id="interview.start", label="Let Nano get to know you · ~30 min, pause anytime")
        ]))
    if inbox_connected:
        hero_blocks.append(_brief_card(data, activity))
        hero_blocks.append(inbox_hero(data.get("inbox", {}), screen="inbox"))
    else:
        hero_blocks.append(TextBlock(
            text="Connect your inbox to put Nano to work.", variant="body"))
        hero_blocks.append(ActionRow(actions=[Action(id="inbox.connect", label="Connect inbox")]))

    sections = [Section(title=None, blocks=hero_blocks)]

    items = activity.get("items", [])
    if items:
        signals = activity.get("signals_today", 0)
        became = sum(1 for i in items if i["tone"] == "ask")
        sources = sum([
            inbox_connected, bool(n_meals), linked, bool(outfits or garments)])
        footer = (f"{signals} signal{'s' if signals != 1 else ''} today. "
                  + ("None became a question." if became == 0
                     else "One became a question." if became == 1
                     else f"{became} became questions."))
        sections.append(Section(
            title=f"Today · {sources} source{'s' if sources != 1 else ''} live",
            blocks=[Timeline(
                items=[TimelineItem(
                    text=i["text"], verdict=i["verdict"], tone=i["tone"],
                    at=i["at"].astimezone(ZoneInfo(get_settings().default_timezone)).strftime("%H:%M")
                    if hasattr(i["at"], "astimezone") else str(i["at"]),
                ) for i in items],
                footer=footer,
            )],
        ))

    autonomy = _autonomy_section(data.get("autonomy", {}))
    if autonomy:
        sections.append(autonomy)

    sections.append(Section(title="Your agents", blocks=[grid]))

    return Screen(title=_greeting(context.user_name or context.user_id.capitalize()),
                  theme="dark", sections=sections)


register_agent("hub", render=hub_render)
