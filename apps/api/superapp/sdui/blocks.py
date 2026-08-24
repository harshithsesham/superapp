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


class Action(BaseModel):
    id: str  # posted back to /v1/actions when tapped; irreversible things require this tap
    label: str
    style: Literal["primary", "secondary", "destructive"] = "primary"


class ActionRow(BaseModel):
    type: Literal["action_row"] = "action_row"
    actions: list[Action] = Field(min_length=1, max_length=3)


LeafBlock = Annotated[
    Union[TextBlock, InsightCard, StatRow, ImageCard, ListBlock, ActionRow],
    Field(discriminator="type"),
]


class Section(BaseModel):
    type: Literal["section"] = "section"
    title: str | None = None
    blocks: list[LeafBlock]


class Screen(BaseModel):
    """Top-level payload for any screen the client renders."""

    type: Literal["screen"] = "screen"
    # Contract version, bumped on breaking changes only. Clients compare against
    # their supported version and prompt for an app update when behind; unknown
    # blocks within a version degrade silently.
    version: int = 1
    title: str
    sections: list[Section]


Block = Union[Screen, Section, LeafBlock]
