"""Demo agent — exists to prove the Phase 0 exit criterion:

"A hardcoded agent returns UI blocks that render as native components on the
phone, and a fact it writes shows up in the next run's context slice."

It counts its own past runs by reading the fact it wrote last time, writes the
incremented count back, and renders a screen showing exactly what it knew.
Delete this once the nutrition vertical lands.
"""
from sqlalchemy.orm import Session

from ..sdui.blocks import InsightCard, Screen, Section, Stat, StatRow, TextBlock
from ..substrate import ContextSlice
from .base import AgentResult, FactWrite, register_agent


@register_agent("demo")
def demo_agent(db: Session, *, trigger: dict, context: ContextSlice, run_id: str) -> AgentResult:
    prior = next(
        (f for f in context.facts if f["domain"] == "demo" and f["key"] == "run_count"),
        None,
    )
    run_count = (prior["value"]["count"] if prior else 0) + 1

    screen = Screen(
        title="Super App",
        sections=[
            Section(
                title="Spine check",
                blocks=[
                    TextBlock(
                        text=(
                            "This screen was assembled by an agent from a context slice "
                            "it borrowed from the substrate."
                        ),
                        variant="body",
                    ),
                    StatRow(
                        stats=[
                            Stat(label="Facts in my slice", value=str(len(context.facts))),
                            Stat(label="Recent events", value=str(len(context.recent_events))),
                            Stat(label="My past runs", value=str(run_count - 1)),
                        ]
                    ),
                    InsightCard(
                        id=f"demo-{run_id}",
                        agent="demo",
                        title="Memory is forming" if prior else "First run — blank slate",
                        body=(
                            f"I remembered {run_count - 1} previous run(s) via user_facts."
                            if prior
                            else "I wrote my first fact; refresh and I should remember this run."
                        ),
                        emphasis="positive" if prior else "default",
                    ),
                ],
            )
        ],
    )

    return AgentResult(
        screen=screen,
        fact_writes=[
            FactWrite(domain="demo", key="run_count", value={"count": run_count}, confidence=1.0)
        ],
    )
