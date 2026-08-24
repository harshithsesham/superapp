"""Agent runtime (architecture §5). Every agent is a stateless function:

    (trigger, context_slice) -> (ui_blocks, fact_writes, event_writes, actions)

`run_agent` is the deterministic harness around that function: assemble context
via the Context API, call the agent, apply write-backs (how memory forms), and
log the run to `events`.
"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Protocol

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


@dataclass
class AgentResult:
    screen: Screen | None = None
    fact_writes: list[FactWrite] = field(default_factory=list)
    event_writes: list[EventWrite] = field(default_factory=list)
    actions_taken: list[str] = field(default_factory=list)


class AgentFn(Protocol):
    def __call__(self, db: Session, *, trigger: dict, context: ContextSlice, run_id: str) -> AgentResult: ...


_REGISTRY: dict[str, "Callable"] = {}


def register_agent(name: str):
    def deco(fn: AgentFn) -> AgentFn:
        _REGISTRY[name] = fn
        return fn

    return deco


def run_agent(db: Session, *, agent: str, user_id: str, trigger: dict) -> AgentResult:
    if agent not in _REGISTRY:
        raise ValueError(f"No agent registered under {agent!r}")
    run_id = str(uuid.uuid4())

    context = get_context(db, agent=agent, user_id=user_id)
    result = _REGISTRY[agent](db, trigger=trigger, context=context, run_id=run_id)

    # Write-back step: anything durable the agent learned becomes a fact/event
    # before the run ends. Between runs the agent forgets everything.
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
        append_event(db, user_id=user_id, type=ew.type, agent=agent, payload=ew.payload)

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
    return result
