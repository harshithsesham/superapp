"""The Hub (Nano V1 "My Hub") — the app's home screen and the substrate's
show-off: a render-only agent with wildcard scope that projects every
vertical's state into one glance. Hero card for the inbox; a live agent grid
for the rest. No think tier — the Hub owns no cognition, only the view.
"""
from datetime import datetime, timezone

from ..sdui.blocks import Action, ActionRow, AgentGrid, AgentGridItem, Screen, Section, TextBlock
from ..substrate import ContextSlice
from .base import register_agent
from .inbox import inbox_hero


def hub_render(context: ContextSlice) -> Screen:
    data = context.domain_data
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
    hero_blocks = [TextBlock(text=stamp, variant="caption")]
    if not has_identity:
        hero_blocks.append(ActionRow(actions=[
            Action(id="interview.start", label="Meet Nano — 20 minutes that change everything")
        ]))
    if inbox_connected:
        hero_blocks.append(inbox_hero(data.get("inbox", {}), screen="inbox"))
    else:
        hero_blocks.append(TextBlock(
            text="Connect your inbox to put Nano to work.", variant="body"))
        hero_blocks.append(ActionRow(actions=[Action(id="inbox.connect", label="Connect inbox")]))

    return Screen(title="My Hub", theme="dark", sections=[
        Section(title=None, blocks=hero_blocks),
        Section(title="Your agents", blocks=[grid]),
    ])


register_agent("hub", render=hub_render)
