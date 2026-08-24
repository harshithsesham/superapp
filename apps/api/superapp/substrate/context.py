"""The Context API — the personal Graph (architecture §6.2).

The ONLY door agents have to the substrate. Each run's context is assembled
fresh by deterministic code: scoped facts + recent events (+ domain data and
vector-retrieved episodes as verticals land). Agents never get raw DB access.
"""
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..config import get_settings
from .events import recent_events
from .facts import read_facts

# Entitlements: which fact domains each agent may read. "*" = all domains.
# Adding a vertical = adding a line here; it inherits everything on day one.
AGENT_SCOPES: dict[str, list[str] | None] = {
    "demo": None,  # None = wildcard
    "orchestrator": None,
    "nutrition": ["nutrition", "goals", "health"],
    "finance": ["finance", "goals"],
    "inbox": ["inbox", "goals"],
    "stylist": ["wardrobe", "goals", "nutrition", "finance"],
}


@dataclass
class ContextSlice:
    user_id: str
    agent: str
    facts: list[dict] = field(default_factory=list)
    recent_events: list[dict] = field(default_factory=list)

    def to_prompt_dict(self) -> dict:
        return {"facts": self.facts, "recent_events": self.recent_events}


def get_context(db: Session, *, agent: str, user_id: str) -> ContextSlice:
    settings = get_settings()
    if agent not in AGENT_SCOPES:
        raise ValueError(f"Unknown agent {agent!r}; register it in AGENT_SCOPES")
    domains = AGENT_SCOPES[agent]

    facts = read_facts(db, user_id=user_id, domains=domains, limit=settings.context_max_facts)
    events = recent_events(db, user_id=user_id, limit=settings.context_max_events)

    return ContextSlice(
        user_id=user_id,
        agent=agent,
        facts=[
            {
                "domain": f.domain,
                "key": f.key,
                "value": f.value,
                "confidence": f.confidence,
                "learned_at": f.learned_at.isoformat(),
            }
            for f in facts
        ],
        recent_events=[
            {"type": e.type, "agent": e.agent, "payload": e.payload, "at": e.created_at.isoformat()}
            for e in events
        ],
    )
