"""SDUI v1 — the typed contract between agents and the client (architecture §3).

Agents may ONLY emit these blocks. The client owns how they look; agents only
choose which blocks and what content. This file is the single source of truth:
`scripts/export_sdui_schema.py` exports it as JSON Schema for the mobile app.
"""
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str
    variant: Literal["body", "title", "subtitle", "caption"] = "body"


class InsightCard(BaseModel):
    """The workhorse: one insight from one agent, dismissible, optionally actionable."""

    type: Literal["insight_card"] = "insight_card"
    id: str  # stable id so dismissals can be logged to events
    agent: str
    title: str
    body: str
    emphasis: Literal["default", "positive", "warning"] = "default"
    action_label: str | None = None
    action_id: str | None = None


class Stat(BaseModel):
    label: str
    value: str
    delta: str | None = None  # e.g. "+12%" — client colors it by sign
    unit: str | None = None


class StatRow(BaseModel):
    type: Literal["stat_row"] = "stat_row"
    stats: list[Stat] = Field(min_length=1, max_length=4)


class ImageCard(BaseModel):
    type: Literal["image_card"] = "image_card"
    image_url: str
    title: str | None = None
    subtitle: str | None = None


class ListItem(BaseModel):
    id: str
    title: str
    subtitle: str | None = None
    trailing: str | None = None  # e.g. an amount or a time


class ListBlock(BaseModel):
    type: Literal["list"] = "list"
    items: list[ListItem]


class GridImage(BaseModel):
    id: str
    image_url: str
    label: str | None = None


class ImageGrid(BaseModel):
    """Thumbnail grid (the closet). Taps log a reaction with the item id."""

    type: Literal["image_grid"] = "image_grid"
    items: list[GridImage]
    columns: Literal[2, 3, 4] = 3


class OutfitItem(BaseModel):
    garment_id: str
    image_url: str | None = None
    label: str


class OutfitCard(BaseModel):
    """One suggested outfit: garment strip + rationale + like/dislike.
    The client posts outfit_liked / outfit_rejected reactions with this id."""

    type: Literal["outfit_card"] = "outfit_card"
    id: str
    agent: str
    title: str
    occasion: str | None = None
    rationale: str
    items: list[OutfitItem]


class AgentStat(BaseModel):
    n: str
    label: str
    accent: bool = False  # mint highlight (the "37 handled" number)


class AgentCard(BaseModel):
    """The hero card (Nano V1 "My Hub"): one agent, its live status, a serif
    headline, and inline stat cells."""

    type: Literal["agent_card"] = "agent_card"
    id: str
    agent: str
    name: str
    sub: str
    live: bool = False
    headline: str
    body: str
    stats: list[AgentStat] = Field(default_factory=list)
    screen: str | None = None  # tapping the card navigates here (client-side)


class AgentGridItem(BaseModel):
    screen: str  # navigation target
    name: str
    sub: str  # live one-liner ("370 kcal today")
    tone: Literal["indigo", "mint", "amber", "rose"] = "indigo"


class AgentGrid(BaseModel):
    """The hub's agent roster (Nano V1 "Coming to your Hub", except ours are live)."""

    type: Literal["agent_grid"] = "agent_grid"
    items: list[AgentGridItem]


class DraftCard(BaseModel):
    """A reply written and waiting (the Nano inbox). The client owns the feel:
    inline editing, optimistic send, defer. id is the draft id; the client
    calls the draft endpoints and logs reactions."""

    type: Literal["draft_card"] = "draft_card"
    id: str
    agent: str
    from_name: str
    subject: str
    why: str  # urgency chip, e.g. "deadline today EOD"
    draft: str
    status: Literal["waiting", "edited", "sent", "dismissed"] = "waiting"
    deferred_label: str | None = None  # e.g. "ASKING AGAIN AT 6PM" — card stays visible, settled


class Action(BaseModel):
    id: str  # posted back to /v1/actions when tapped; irreversible things require this tap
    label: str
    style: Literal["primary", "secondary", "destructive"] = "primary"


class ActionRow(BaseModel):
    type: Literal["action_row"] = "action_row"
    actions: list[Action] = Field(min_length=1, max_length=3)


LeafBlock = Annotated[
    Union[TextBlock, InsightCard, StatRow, ImageCard, ListBlock, ImageGrid, OutfitCard, AgentCard, AgentGrid, DraftCard, ActionRow],
    Field(discriminator="type"),
]


class Section(BaseModel):
    type: Literal["section"] = "section"
    title: str | None = None
    blocks: list[LeafBlock]


class Screen(BaseModel):
    """Top-level payload for any screen the client renders."""

    type: Literal["screen"] = "screen"
    # Surface mood — the client renders its light or dark palette. Additive,
    # so no version bump; old clients ignore it.
    theme: Literal["light", "dark"] = "light"
    # Contract version, bumped on breaking changes only. Clients compare against
    # their supported version and prompt for an app update when behind; unknown
    # blocks within a version degrade silently.
    version: int = 1
    title: str
    sections: list[Section]


Block = Union[Screen, Section, LeafBlock]
