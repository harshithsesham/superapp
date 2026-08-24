"""Phase 0 exit-criterion tests.

Exit criterion: an agent returns UI blocks, and a fact it writes shows up in the
next run's context slice (memory forms through write-back, not model memory).
"""
import os

os.environ["SUPERAPP_DATABASE_URL"] = "sqlite://"  # in-memory, before app import

from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import sessionmaker

import superapp.db as db_module

# Rewire to a shared in-memory engine before the app builds sessions.
engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
db_module.engine = engine
db_module.SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

from superapp.db import Base, SessionLocal
from superapp.main import app
from superapp.substrate import get_context, read_facts, recent_events, write_fact

Base.metadata.create_all(bind=engine)
client = TestClient(app)
AUTH = {"Authorization": "Bearer dev-token-change-me"}


def test_fact_write_conflict_resolution_archives_to_events():
    db = SessionLocal()
    write_fact(db, user_id="u1", domain="goals", key="trip", value={"goal": "japan_2027"}, source_agent="finance")
    write_fact(db, user_id="u1", domain="goals", key="trip", value={"goal": "japan_2028"}, source_agent="finance")
    db.commit()

    facts = read_facts(db, user_id="u1", domains=["goals"], limit=10)
    assert len(facts) == 1 and facts[0].value == {"goal": "japan_2028"}  # newer wins

    archived = [e for e in recent_events(db, user_id="u1", limit=10) if e.type == "fact_superseded"]
    assert len(archived) == 1 and archived[0].payload["old_value"] == {"goal": "japan_2027"}
    db.close()


def test_context_scoping():
    db = SessionLocal()
    write_fact(db, user_id="u2", domain="nutrition", key="target", value={"kcal": 2200}, source_agent="nutrition")
    write_fact(db, user_id="u2", domain="inbox", key="vip", value={"who": "landlord"}, source_agent="inbox")
    db.commit()

    nutrition_slice = get_context(db, agent="nutrition", user_id="u2")
    domains = {f["domain"] for f in nutrition_slice.facts}
    assert "nutrition" in domains and "inbox" not in domains  # scoped slice, not the whole substrate
    db.close()


def test_screen_requires_auth():
    assert client.get("/v1/screen/home").status_code == 401


def test_exit_criterion_agent_remembers_across_runs():
    first = client.get("/v1/screen/home", headers=AUTH).json()
    assert first["type"] == "screen" and first["sections"]  # renders typed blocks

    second = client.get("/v1/screen/home", headers=AUTH).json()
    card = next(b for b in second["sections"][0]["blocks"] if b["type"] == "insight_card")
    assert card["title"] == "Memory is forming"  # run 2 saw the fact run 1 wrote

    stats = next(b for b in second["sections"][0]["blocks"] if b["type"] == "stat_row")
    past_runs = next(s for s in stats["stats"] if s["label"] == "My past runs")
    assert past_runs["value"] == "1"


def test_reactions_land_in_events():
    r = client.post(
        "/v1/reactions",
        headers=AUTH,
        json={"kind": "insight_dismissed", "target_id": "demo-x", "agent": "demo"},
    )
    assert r.status_code == 200
    db = SessionLocal()
    kinds = [e.type for e in recent_events(db, user_id="harshith", limit=20)]
    assert "insight_dismissed" in kinds
    db.close()
