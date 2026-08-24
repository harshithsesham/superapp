"""Spine + Phase 1 exit-criterion tests.

Exit criterion: an agent returns UI blocks, and a fact it writes shows up in the
next run's context slice (memory forms through write-back, not model memory).
Architecture invariants under test: GET screens are pure renders; cognition only
runs through the think tier; context slices are entitlement-scoped for facts AND
events; user_facts holds beliefs, not collections.
"""
import os

import pytest

import tempfile

os.environ["SUPERAPP_DATABASE_URL"] = "sqlite://"  # in-memory, before app import
os.environ["SUPERAPP_MEDIA_DIR"] = tempfile.mkdtemp(prefix="superapp-media-")

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
from superapp.substrate import append_event, get_context, read_facts, recent_events, write_fact

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
    assert archived[0].domain == "goals"  # archive events are scoped like the fact
    db.close()


def test_facts_hold_beliefs_not_collections():
    db = SessionLocal()
    with pytest.raises(ValueError, match="domain twin"):
        write_fact(
            db, user_id="u1", domain="wardrobe", key="items",
            value={"items": ["shirt", "jeans"]}, source_agent="stylist",
        )
    with pytest.raises(ValueError, match="domain twin"):
        write_fact(
            db, user_id="u1", domain="inbox", key="digest",
            value={"blob": "x" * 2000}, source_agent="inbox",
        )
    db.close()


def test_context_scoping_facts():
    db = SessionLocal()
    write_fact(db, user_id="u2", domain="nutrition", key="target", value={"kcal": 2200}, source_agent="nutrition")
    write_fact(db, user_id="u2", domain="inbox", key="vip", value={"who": "landlord"}, source_agent="inbox")
    db.commit()

    nutrition_slice = get_context(db, agent="nutrition", user_id="u2")
    domains = {f["domain"] for f in nutrition_slice.facts}
    assert "nutrition" in domains and "inbox" not in domains  # scoped slice, not the whole substrate
    db.close()


def test_context_scoping_events():
    db = SessionLocal()
    append_event(db, user_id="u3", type="email_ingested", agent="inbox", domain="inbox",
                 payload={"subject": "rent due"})
    append_event(db, user_id="u3", type="meal_logged", agent="nutrition", domain="nutrition")
    append_event(db, user_id="u3", type="agent_run", agent="inbox")  # domain-less system telemetry
    append_event(db, user_id="u3", type="screen_view", agent="demo", payload={"screen": "home"})
    db.commit()

    types = {e["type"] for e in get_context(db, agent="nutrition", user_id="u3").recent_events}
    assert "meal_logged" in types
    assert "email_ingested" not in types  # other verticals' events never leak into the slice
    assert "agent_run" in types  # system events stay visible
    assert "screen_view" not in types  # view noise never spends context budget
    db.close()


def test_screen_requires_auth():
    assert client.get("/v1/screen/home").status_code == 401


# 1x1 red pixel PNG — enough to exercise upload/storage/serving end to end.
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c626000010000050001a5f645400000000049454e44ae426082"
)


def test_phase1_exit_criterion_meal_flow():
    # Blank slate renders.
    first = client.get("/v1/screen/home", headers=AUTH).json()
    assert first["type"] == "screen" and first["version"] == 1 and first["title"] == "Nutrition"

    # Set a target (a user-stated belief -> user_facts).
    assert client.post("/v1/nutrition/target", headers=AUTH, json={"kcal": 2200}).status_code == 200

    # Log a meal by text: ingest -> think (stub estimate) -> fresh screen.
    screen = client.post(
        "/v1/nutrition/log", headers=AUTH, json={"description": "2 eggs and toast"}
    ).json()
    blocks = screen["sections"][0]["blocks"]
    meals = next(b for b in blocks if b["type"] == "list")
    assert "eggs" in meals["items"][0]["title"] and "kcal" in meals["items"][0]["trailing"]
    stats = next(b for b in blocks if b["type"] == "stat_row")
    today = next(s for s in stats["stats"] if s["label"] == "Today")
    assert int(today["value"]) > 0  # stub estimate landed in the twin
    assert next(s for s in stats["stats"] if s["label"] == "Target")["value"] == "2200"

    # GET is still pure: repeat views change nothing.
    again = client.get("/v1/screen/home", headers=AUTH).json()
    assert again == client.get("/v1/screen/home", headers=AUTH).json()

    # The estimate went through the provider and was cost-logged.
    db = SessionLocal()
    calls = [e for e in recent_events(db, user_id="harshith", limit=20) if e.type == "llm_call"]
    assert calls and calls[0].payload["task"] == "estimate"
    db.close()


def test_photo_upload_and_media_serving():
    screen = client.post(
        "/v1/nutrition/photo", headers=AUTH,
        files={"photo": ("lunch.png", TINY_PNG, "image/png")},
    ).json()
    blocks = screen["sections"][0]["blocks"]
    image = next(b for b in blocks if b["type"] == "image_card")
    assert image["image_url"].startswith("/v1/media/")

    photo_id = image["image_url"].removeprefix("/v1/media/")
    served = client.get(f"/v1/media/{photo_id}", headers=AUTH)
    assert served.status_code == 200 and served.content == TINY_PNG
    assert client.get(f"/v1/media/{photo_id}").status_code == 401  # auth required
    assert client.get("/v1/media/../etc/passwd", headers=AUTH).status_code in (404, 422)


def test_daily_summary_via_think_endpoint():
    # Cron trigger: summary insight forms as a fact, then renders on the screen.
    summary = client.post("/v1/agents/nutrition/think", headers=AUTH).json()
    assert summary["agent"] == "nutrition" and summary["facts_written"] == 1

    screen = client.get("/v1/screen/home", headers=AUTH).json()
    card = next(b for b in screen["sections"][0]["blocks"] if b["type"] == "insight_card")
    assert card["id"] == "nutrition-summary" and "kcal" in card["body"]
    assert client.post("/v1/agents/nope/think", headers=AUTH).status_code == 404


def test_provider_stub_logs_cost_event():
    from superapp.llm.provider import LLMProvider

    db = SessionLocal()
    provider = LLMProvider()
    resp = provider.complete(
        db, user_id="u4", agent="nutrition", task="estimate",
        system="You are the nutrition agent.", prompt="2 eggs and toast",
    )
    assert resp.stubbed and not resp.refused
    db.commit()

    calls = [e for e in recent_events(db, user_id="u4", limit=5) if e.type == "llm_call"]
    assert len(calls) == 1
    payload = calls[0].payload
    assert payload["model"] == "claude-opus-5" and payload["cost_usd"] == 0.0
    assert {"input_tokens", "output_tokens", "cache_read_tokens", "batched"} <= payload.keys()
    db.close()


def test_provider_stub_batch():
    from superapp.llm.provider import LLMProvider

    db = SessionLocal()
    provider = LLMProvider()
    results = provider.complete_batch(
        db, user_id="u4", agent="finance", task="weekly_insight",
        system="You are the finance agent.",
        prompts={"week-33": "spending summary", "week-34": "spending summary"},
    )
    assert set(results) == {"week-33", "week-34"}
    assert all(r.stubbed and r.batched for r in results.values())
    db.commit()

    batched = [
        e for e in recent_events(db, user_id="u4", limit=10)
        if e.type == "llm_call" and e.payload["batched"]
    ]
    assert len(batched) == 2
    db.close()


def test_routing_tasks_use_small_model_low_effort():
    from superapp.llm.provider import LLMProvider

    provider = LLMProvider()
    params = provider._build_params(task="triage", system="s", prompt="p", effort=None, max_tokens=None)
    assert params["model"] == "claude-haiku-4-5"
    assert params["output_config"] == {"effort": "low"}
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_reactions_land_in_events():
    r = client.post(
        "/v1/reactions",
        headers=AUTH,
        json={"kind": "insight_dismissed", "target_id": "nutrition-summary", "agent": "nutrition", "domain": "nutrition"},
    )
    assert r.status_code == 200
    db = SessionLocal()
    kinds = [e.type for e in recent_events(db, user_id="harshith", limit=20)]
    assert "insight_dismissed" in kinds
    db.close()
