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


# ---------------------------------------------------------------- Phase 2

def test_vault_roundtrip_encrypts_at_rest():
    from superapp.models import TokenVaultEntry
    from superapp.vault import get_token, store_token

    db = SessionLocal()
    store_token(db, user_id="u5", provider="plaid:item-x", token="access-sandbox-secret")
    db.commit()
    row = db.query(TokenVaultEntry).filter_by(user_id="u5").one()
    assert "access-sandbox-secret" not in row.ciphertext  # encrypted at rest
    assert get_token(db, user_id="u5", provider="plaid:item-x") == "access-sandbox-secret"
    assert get_token(db, user_id="u5", provider="plaid:nope") is None
    db.close()


def test_phase2_exit_criterion_link_sync_rules_insight():
    # Budget set BEFORE linking so the rules engine sees it on first sync.
    # Stub data always has >= $1800 rent MTD, so a $1000 cap always trips.
    r = client.post("/v1/finance/budget", headers=AUTH,
                    json={"category": "RENT_AND_UTILITIES", "monthly": 1000})
    assert r.status_code == 200

    # Link (stub bank): stores encrypted token, pulls accounts, first sync + rules.
    screen = client.post("/v1/finance/link/sandbox", headers=AUTH).json()
    assert screen["title"] == "Finance"
    blocks = screen["sections"][0]["blocks"]
    stats = next(b for b in blocks if b["type"] == "stat_row")
    assert next(s for s in stats["stats"] if s["label"] == "Accounts")["value"] == "2"
    assert any(b["type"] == "list" for b in blocks)  # transactions render

    db = SessionLocal()
    types = {e.type for e in recent_events(db, user_id="harshith", limit=60)}
    assert "transactions_synced" in types
    assert "budget_exceeded" in types            # deterministic: rent > cap
    assert "recurring_detected" in types         # Netflix / rent / ConEd cadence
    assert "anomaly" in types                    # the $342.99 B&H one-off

    from superapp.substrate import read_facts
    facts = {f.key: f.value for f in read_facts(db, user_id="harshith", domains=["finance"], limit=20)}
    assert facts["income_cadence"]["cadence"] == "biweekly"  # 1st + 15th payroll
    assert facts["recurring_bills"]["count"] >= 2
    db.close()

    # Re-sync is idempotent: no new rows, no duplicate alerts.
    summary = client.post("/v1/finance/sync", headers=AUTH).json()
    assert summary["agent"] == "finance"
    db = SessionLocal()
    synced = [e for e in recent_events(db, user_id="harshith", limit=10, types=["transactions_synced"])]
    assert synced[0].payload["new"] == 0
    db.close()

    # Weekly insight (think tier) writes the fact; the screen renders it.
    client.post("/v1/agents/finance/think", headers=AUTH)
    screen = client.get("/v1/screen/finance", headers=AUTH).json()
    card = next(b for b in screen["sections"][0]["blocks"] if b["type"] == "insight_card")
    assert card["id"] == "finance-insight" and "$" in card["body"]


def test_plaid_webhook_gated_by_token():
    assert client.post(
        "/v1/plaid/webhook/wrong-token", json={"webhook_type": "TRANSACTIONS"}
    ).status_code == 403
    r = client.post(
        "/v1/plaid/webhook/change-me-webhook-token",
        json={"webhook_type": "TRANSACTIONS", "webhook_code": "SYNC_UPDATES_AVAILABLE"},
    )
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_push_token_registration():
    r = client.post("/v1/devices/push-token", headers=AUTH,
                    json={"token": "ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx]"})
    assert r.status_code == 200
    from superapp.substrate import read_facts
    db = SessionLocal()
    facts = read_facts(db, user_id="harshith", domains=["system"], limit=5)
    assert any(f.key == "expo_push_token" for f in facts)
    db.close()


def test_finance_events_scoped_away_from_nutrition():
    db = SessionLocal()
    slice_ = get_context(db, agent="nutrition", user_id="harshith")
    assert not any(e["type"] == "transactions_synced" for e in slice_.recent_events)
    assert "finance" not in slice_.domain_data  # twin data scoped too
    db.close()


# ---------------------------------------------------------------- Phase 4

def test_phase4_closet_outfits_and_style_memory():
    # Upload three garments (stub extraction cycles top/bottom/shoes).
    for _ in range(3):
        r = client.post("/v1/wardrobe/photo", headers=AUTH,
                        files={"photo": ("garment.png", TINY_PNG, "image/png")})
        assert r.status_code == 200
    screen = r.json()
    assert screen["title"] == "Stylist"
    closet = next(s for s in screen["sections"] if s["title"] == "Closet")
    grid = next(b for b in closet["blocks"] if b["type"] == "image_grid")
    assert len(grid["items"]) == 3 and grid["items"][0]["image_url"].startswith("/v1/media/")

    # Refresh generates today's outfits from owned garments (+ weather fact).
    screen = client.post("/v1/screen/stylist/refresh", headers=AUTH).json()
    today_sec = next(s for s in screen["sections"] if s["title"] == "Today")
    cards = [b for b in today_sec["blocks"] if b["type"] == "outfit_card"]
    assert cards and all(c["items"] for c in cards)
    assert any("°C" in b["text"] for b in today_sec["blocks"] if b["type"] == "text")

    # Feedback x3 -> next think distills a style profile into wardrobe facts.
    for i in range(3):
        client.post("/v1/reactions", headers=AUTH, json={
            "kind": "outfit_liked" if i % 2 == 0 else "outfit_rejected",
            "target_id": cards[i % len(cards)]["id"], "agent": "stylist", "domain": "wardrobe",
        })
    client.post("/v1/agents/stylist/think", headers=AUTH)
    screen = client.get("/v1/screen/stylist", headers=AUTH).json()
    style = next((s for s in screen["sections"] if s["title"] == "Your style"), None)
    assert style is not None
    card = style["blocks"][0]
    assert card["type"] == "insight_card" and card["body"]

    db = SessionLocal()
    from superapp.substrate import read_facts
    facts = {f.key for f in read_facts(db, user_id="harshith", domains=["wardrobe"], limit=20)}
    assert {"style_profile", "last_distillation", "weather"} <= facts
    db.close()


def test_wardrobe_scoped_away_from_other_agents():
    db = SessionLocal()
    assert "wardrobe" not in get_context(db, agent="nutrition", user_id="harshith").domain_data
    stylist_slice = get_context(db, agent="stylist", user_id="harshith")
    assert "wardrobe" in stylist_slice.domain_data
    # Cross-domain payoff: stylist reads nutrition + finance facts too.
    assert {"nutrition", "finance"} <= {f["domain"] for f in stylist_slice.facts}
    db.close()


# ---------------------------------------------------------------- Phase 3

def test_phase3_connect_triage_tiers_and_receipts():
    screen = client.post("/v1/inbox/connect/stub", headers=AUTH).json()
    assert screen["title"] == "Inbox Zero" and screen["theme"] == "dark"
    hero = next(b for sec in screen["sections"] for b in sec["blocks"] if b["type"] == "agent_card")
    assert hero["name"] == "Inbox Zero" and "your yes" in hero["headline"]
    titles = [s["title"] or "" for s in screen["sections"]]

    # The three Nano tiers render with counts.
    needs = next(s for s in screen["sections"] if (s["title"] or "").startswith("Needs your words"))
    drafts = [b for b in needs["blocks"] if b["type"] == "draft_card"]
    assert len(drafts) >= 2  # Eureka deadline, Marcus lease, Amma
    assert all(d["draft"] for d in drafts)  # written and waiting
    assert any("deadline" in d["why"] or "waiting" in d["why"] for d in drafts)
    assert any(t.startswith("Read only") for t in titles)
    assert any(t.startswith("Cleared without asking") for t in titles)

    # Noise never made it into the visible tiers.
    all_text = str(screen)
    assert "UNIQLO" not in all_text and "LinkedIn" not in all_text

    # The receipt (Myntra order) was recognized — the Phase 4d hook.
    db = SessionLocal()
    from superapp.models import InboxMessage
    tiers = {m.tier for m in db.query(InboxMessage).filter_by(user_id="harshith")}
    assert "receipt" in tiers
    synced = recent_events(db, user_id="harshith", limit=5, types=["inbox_synced"])
    assert synced[0].payload["new"] == 12 and synced[0].payload["cleared"] >= 4
    db.close()

    # Re-sync is idempotent.
    client.post("/v1/inbox/sync", headers=AUTH)
    db = SessionLocal()
    synced = recent_events(db, user_id="harshith", limit=5, types=["inbox_synced"])
    assert synced[0].payload["new"] == 0
    db.close()


def test_phase3_trust_ladder_and_send_flow():
    screen = client.get("/v1/screen/inbox", headers=AUTH).json()
    needs = next(s for s in screen["sections"] if (s["title"] or "").startswith("Needs your words"))
    draft = next(b for b in needs["blocks"] if b["type"] == "draft_card")

    # Tier "read": sending is refused — the ladder holds.
    r = client.post(f"/v1/inbox/drafts/{draft['id']}/send", headers=AUTH)
    assert r.status_code == 403

    # Edit logs the voice-learning diff.
    r = client.put(f"/v1/inbox/drafts/{draft['id']}", headers=AUTH,
                   json={"body": "Yes — confirmed for 3pm. See you then."})
    assert r.status_code == 200
    db = SessionLocal()
    edits = recent_events(db, user_id="harshith", limit=5, types=["draft_edited"])
    assert edits and edits[0].payload["before"] != edits[0].payload["after"]
    db.close()

    # Climb to "send": the tap sends (stub) and the ask settles.
    import superapp.config as config_module
    settings = config_module.get_settings()
    old_tier = settings.gmail_scope_tier
    settings.gmail_scope_tier = "send"
    try:
        screen = client.post(f"/v1/inbox/drafts/{draft['id']}/send", headers=AUTH).json()
    finally:
        settings.gmail_scope_tier = old_tier
    needs = next(s for s in screen["sections"] if (s["title"] or "").startswith("Needs your words"))
    assert draft["id"] not in str(needs)  # settled, gone from the asks
    db = SessionLocal()
    assert recent_events(db, user_id="harshith", limit=5, types=["draft_sent"])
    db.close()

    # Defer: "ask me at 6pm" hides the card.
    screen = client.get("/v1/screen/inbox", headers=AUTH).json()
    needs = next(s for s in screen["sections"] if (s["title"] or "").startswith("Needs your words"))
    remaining = [b for b in needs["blocks"] if b["type"] == "draft_card"]
    before_count = len(remaining)
    if remaining:
        screen = client.post(f"/v1/inbox/drafts/{remaining[0]['id']}/defer", headers=AUTH).json()
        needs = next(s for s in screen["sections"] if (s["title"] or "").startswith("Needs your words"))
        assert len([b for b in needs["blocks"] if b["type"] == "draft_card"]) == before_count - 1


def test_phase3_morning_brief_and_style_learning():
    # Two more edits (3 total incl. the earlier one) -> scheduled think
    # distills reply style + writes the morning brief.
    screen = client.get("/v1/screen/inbox", headers=AUTH).json()
    card = next(b for sec in screen["sections"] for b in sec["blocks"] if b["type"] == "draft_card")
    for i in range(2):
        r = client.put(f"/v1/inbox/drafts/{card['id']}", headers=AUTH,
                       json={"body": f"Short answer - yes, works for me. (edit {i})"})
        assert r.status_code == 200
    summary = client.post("/v1/agents/inbox/think", headers=AUTH).json()
    assert summary["agent"] == "inbox"

    db = SessionLocal()
    from superapp.substrate import read_facts
    keys = {f.key for f in read_facts(db, user_id="harshith", domains=["inbox"], limit=20)}
    assert "morning_brief" in keys and "reply_style" in keys
    db.close()

    screen = client.get("/v1/screen/inbox", headers=AUTH).json()
    brief = next(b for s in screen["sections"] for b in s["blocks"]
                 if b["type"] == "insight_card" and b["id"] == "morning-brief")
    assert "need your words" in brief["body"] or "Inbox Zero" in brief["body"]


def test_gmail_webhook_gated():
    assert client.post("/v1/gmail/webhook/nope", json={}).status_code == 403
    r = client.post("/v1/gmail/webhook/change-me-gmail-webhook", json={"message": {"data": ""}})
    assert r.status_code == 200


def test_hub_screen_projects_all_verticals():
    screen = client.get("/v1/screen/hub", headers=AUTH).json()
    assert screen["title"] == "My Hub" and screen["theme"] == "dark"
    hero = next(b for sec in screen["sections"] for b in sec["blocks"] if b["type"] == "agent_card")
    assert hero["name"] == "Inbox Zero" and hero["screen"] == "inbox"
    grid = next(b for sec in screen["sections"] for b in sec["blocks"] if b["type"] == "agent_grid")
    assert {i["screen"] for i in grid["items"]} == {"home", "finance", "stylist"}
    assert any("kcal" in i["sub"] or "logged" in i["sub"] for i in grid["items"])


# ---------------------------------------------------------------- multi-user

def test_second_user_token_full_isolation():
    import superapp.config as config_module
    settings = config_module.get_settings()
    settings.user_tokens = "cofounder:cf-test-token-abc123"
    CF = {"Authorization": "Bearer cf-test-token-abc123"}
    try:
        # Co-founder authenticates and sees an EMPTY world, not harshith's.
        screen = client.get("/v1/screen/home", headers=CF).json()
        stats = next(b for sec in screen["sections"] for b in sec["blocks"] if b["type"] == "stat_row")
        assert next(st for st in stats["stats"] if st["label"] == "Today")["value"] == "0"

        hub = client.get("/v1/screen/hub", headers=CF).json()
        assert "Connect your inbox" in str(hub)  # no gmail account for this user

        # Their writes land under their id, invisible to harshith's slice.
        client.post("/v1/nutrition/target", headers=CF, json={"kcal": 1800})
        db = SessionLocal()
        from superapp.substrate import read_facts
        cf_facts = read_facts(db, user_id="cofounder", domains=["nutrition"], limit=5)
        assert any(f.key == "daily_target" and f.value["kcal"] == 1800 for f in cf_facts)
        h_facts = read_facts(db, user_id="harshith", domains=["nutrition"], limit=5)
        assert not any(f.value.get("kcal") == 1800 for f in h_facts)
        db.close()

        # Harshith's token still resolves to harshith.
        assert client.get("/v1/screen/home", headers=AUTH).status_code == 200
        # Garbage token still rejected.
        assert client.get("/v1/screen/home",
                          headers={"Authorization": "Bearer nope"}).status_code == 401
    finally:
        settings.user_tokens = ""


def test_oauth_state_is_signed():
    from superapp.routers.inbox import _sign_state, _verify_state
    state = _sign_state("cofounder")
    assert _verify_state(state) == "cofounder"
    r = client.get("/v1/gmail/callback", params={"code": "x", "state": "cofounder.forged"})
    assert r.status_code == 403  # tampered state dies before any Google call


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
