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
    "hub": None,  # the home screen: render-only, sees everything
    "orchestrator": None,
    "nutrition": ["nutrition", "goals", "health", "identity"],
    "finance": ["finance", "goals", "identity"],
    "inbox": ["inbox", "goals", "identity"],
    "stylist": ["wardrobe", "goals", "nutrition", "finance", "identity"],
}

# Domain twin loaders: extra per-domain data included in the slice when the
# agent's scope covers that domain. Twins hold records; facts hold beliefs.
def _twin_loaders() -> dict:
    from . import finance, inbox, nutrition, wardrobe

    return {
        "nutrition": nutrition.meals_context,
        "finance": finance.finance_context,
        "wardrobe": wardrobe.wardrobe_context,
        "inbox": inbox.inbox_context,
    }


# Telemetry that would only waste prompt budget: screen views and cost logs are
# queryable with SQL but never belong in an agent's context slice.
CONTEXT_EXCLUDED_EVENT_TYPES = ["screen_view", "llm_call"]


@dataclass
class ContextSlice:
    user_id: str
    agent: str
    facts: list[dict] = field(default_factory=list)
    recent_events: list[dict] = field(default_factory=list)
    domain_data: dict = field(default_factory=dict)  # {domain: twin payload}

    def to_prompt_dict(self) -> dict:
        return {"facts": self.facts, "recent_events": self.recent_events, "domain_data": self.domain_data}


def get_context(db: Session, *, agent: str, user_id: str) -> ContextSlice:
    settings = get_settings()
    if agent not in AGENT_SCOPES:
        raise ValueError(f"Unknown agent {agent!r}; register it in AGENT_SCOPES")
    domains = AGENT_SCOPES[agent]

    facts = read_facts(db, user_id=user_id, domains=domains, limit=settings.context_max_facts)
    # Events are entitlement-scoped exactly like facts: agents see their domains
    # plus domain-less system events, never another vertical's payloads.
    events = recent_events(
        db,
        user_id=user_id,
        limit=settings.context_max_events,
        domains=domains,
        exclude_types=CONTEXT_EXCLUDED_EVENT_TYPES,
    )

    loaders = _twin_loaders()
    twin_domains = loaders.keys() if domains is None else [d for d in domains if d in loaders]
    domain_data = {d: loaders[d](db, user_id) for d in twin_domains}

    return ContextSlice(
        user_id=user_id,
        agent=agent,
        domain_data=domain_data,
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
