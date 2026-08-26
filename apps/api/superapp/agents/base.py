"""Agent runtime (architecture §5), two tiers:

- think(): background cognition. May call the LLM, may take time, and returns
  durable write-backs (facts/events) that the harness applies. Triggered by
  cron, webhooks, ingests, or an explicit pull-to-refresh — never by a plain
  screen view.
- render(): request-path projection. A pure function of the context slice that
  returns a Screen. No DB writes, no LLM calls, no side effects — which is why
  GET /v1/screen/* is fast and idempotent.

Memory forms only through think()'s write-backs; between runs agents forget
everything. Conclusions live in the substrate; screens just read them.
"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from sqlalchemy.orm import Session

from ..sdui.blocks import Screen
from ..substrate import ContextSlice, append_event, get_context, write_fact


@dataclass
class FactWrite:
    domain: str
    key: str
    value: dict
    confidence: float = 0.7
    expires_at: datetime | None = None


@dataclass
class EventWrite:
    type: str
    payload: dict = field(default_factory=dict)
    domain: str | None = None


@dataclass
class ThinkResult:
    fact_writes: list[FactWrite] = field(default_factory=list)
    event_writes: list[EventWrite] = field(default_factory=list)
    actions_taken: list[str] = field(default_factory=list)


class RenderFn(Protocol):
    def __call__(self, context: ContextSlice) -> Screen: ...


class ThinkFn(Protocol):
    def __call__(self, db: Session, *, trigger: dict, context: ContextSlice, run_id: str) -> ThinkResult: ...


@dataclass
class AgentSpec:
    name: str
    render: RenderFn
    think: ThinkFn | None = None  # None = purely reactive agent, renders substrate state
    slow_think: bool = False  # refresh returns immediately; think continues in background


_REGISTRY: dict[str, AgentSpec] = {}


def register_agent(name: str, *, render: RenderFn, think: ThinkFn | None = None,
                   slow_think: bool = False) -> None:
    _REGISTRY[name] = AgentSpec(name=name, render=render, think=think, slow_think=slow_think)


def get_agent(name: str) -> AgentSpec:
    if name not in _REGISTRY:
        raise ValueError(f"No agent registered under {name!r}")
    return _REGISTRY[name]


def render_screen(db: Session, *, agent: str, user_id: str) -> Screen:
    """Request path: assemble a context slice, project it to a Screen. Pure —
    performs no writes; callers that want telemetry log it themselves."""
    spec = get_agent(agent)
    context = get_context(db, agent=agent, user_id=user_id)
    return spec.render(context)


def run_think(db: Session, *, agent: str, user_id: str, trigger: dict) -> dict:
    """Background path: run cognition, then apply write-backs — how memory forms.
    Returns a run summary for the caller (cron endpoint, refresh handler)."""
    spec = get_agent(agent)
    if spec.think is None:
        raise ValueError(f"Agent {agent!r} has no think step")
    run_id = str(uuid.uuid4())

    context = get_context(db, agent=agent, user_id=user_id)
    result = spec.think(db, trigger=trigger, context=context, run_id=run_id)

    for fw in result.fact_writes:
        write_fact(
            db,
            user_id=user_id,
            domain=fw.domain,
            key=fw.key,
            value=fw.value,
            confidence=fw.confidence,
            source_agent=agent,
            source_run_id=run_id,
            expires_at=fw.expires_at,
        )
    for ew in result.event_writes:
        append_event(db, user_id=user_id, type=ew.type, agent=agent, domain=ew.domain, payload=ew.payload)

    append_event(
        db,
        user_id=user_id,
        type="agent_run",
        agent=agent,
        payload={
            "run_id": run_id,
            "trigger": trigger.get("kind", "unknown"),
            "facts_written": len(result.fact_writes),
        },
    )
    db.commit()
    return {
        "run_id": run_id,
        "agent": agent,
        "facts_written": len(result.fact_writes),
        "events_written": len(result.event_writes),
        "actions_taken": result.actions_taken,
    }
