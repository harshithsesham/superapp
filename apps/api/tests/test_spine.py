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
    assert "eggs" in meals["items"][0]["title"] and "kcal" in meals["items"][0]["subtitle"]
    # No plan yet: the screen asks to be set up instead of guessing numbers.
    setup = next(b for b in blocks if b["type"] == "action_row"
                 and any(a["id"] == "nutrition.setup" for a in b["actions"]))
    assert setup is not None

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
    assert "output_config" not in params  # Haiku rejects the effort parameter
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}

    heavy = provider._build_params(task="reply_draft", system="s", prompt="p", effort=None, max_tokens=None)
    assert heavy["output_config"] == {"effort": "high"}


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

    # Defer: the card STAYS visible, settled with a label; "now" brings it back.
    screen = client.get("/v1/screen/inbox", headers=AUTH).json()
    needs = next(s for s in screen["sections"] if (s["title"] or "").startswith("Needs your words"))
    remaining = [b for b in needs["blocks"] if b["type"] == "draft_card"]
    if remaining:
        screen = client.post(f"/v1/inbox/drafts/{remaining[0]['id']}/defer", headers=AUTH).json()
        needs = next(s for s in screen["sections"] if (s["title"] or "").startswith("Needs your words"))
        cards = [b for b in needs["blocks"] if b["type"] == "draft_card"]
        assert len(cards) == len(remaining)  # nothing vanished
        deferred = next(c for c in cards if c["id"] == remaining[0]["id"])
        assert deferred["deferred_label"] == "ASKING AGAIN AT 6PM"

        screen = client.post(f"/v1/inbox/drafts/{remaining[0]['id']}/now", headers=AUTH).json()
        needs = next(s for s in screen["sections"] if (s["title"] or "").startswith("Needs your words"))
        undeferred = next(b for b in needs["blocks"]
                          if b["type"] == "draft_card" and b["id"] == remaining[0]["id"])
        assert undeferred["deferred_label"] is None  # back to an active ask


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
    # V4: the title is a greeting, not a label.
    assert screen["title"].startswith("Good ") and screen["title"].endswith(".")
    assert screen["theme"] == "dark"
    cards = [b for sec in screen["sections"] for b in sec["blocks"] if b["type"] == "agent_card"]
    brief = next(c for c in cards if c["id"] == "morning-brief")
    assert "done" in brief["headline"]  # "Two things done. One question."
    assert {s["label"] for s in brief["stats"]} == {"done without you", "need your yes", "signals read"}
    hero = next(c for c in cards if c["name"] == "Inbox Zero")
    assert hero["screen"] == "inbox"
    grid = next(b for sec in screen["sections"] for b in sec["blocks"] if b["type"] == "agent_grid")
    assert {i["screen"] for i in grid["items"]} == {"home", "finance", "stylist"}
    assert any("kcal" in i["sub"] or "logged" in i["sub"] for i in grid["items"])


def test_hub_timeline_every_signal_ends_in_a_verdict():
    screen = client.get("/v1/screen/hub", headers=AUTH).json()
    timeline = next(b for sec in screen["sections"] for b in sec["blocks"] if b["type"] == "timeline")
    assert timeline["items"], "today's mail should appear as fate lines"
    assert all(i["verdict"] for i in timeline["items"])
    assert any(i["tone"] == "filed" for i in timeline["items"])
    assert "signal" in timeline["footer"]


def test_decision_ledger_and_autonomy_panel():
    # The send-flow tests above left typed verdicts behind: an edited send and
    # a defer from the user, plus nano's own archive/flag verdicts from triage.
    autonomy = client.get("/v1/kernel/autonomy", headers=AUTH).json()
    caps = {c["action_key"]: c for c in autonomy["capabilities"]}
    send = caps["inbox.send_reply"]
    assert send["edited"] == 1 and send["level"] == 2 and not send["promotable"]
    assert caps["inbox.archive_noise"]["acted"] >= 1  # nano's side is counted too

    # Promotion cannot be taken, only earned: 409 until the record qualifies.
    r = client.post("/v1/kernel/promote", headers=AUTH,
                    json={"action_key": "inbox.send_reply"})
    assert r.status_code == 409 and "earned" in r.json()["detail"]

    # The Hub shows the panel with honest counts.
    hub = client.get("/v1/screen/hub", headers=AUTH).json()
    panel = next(sec for sec in hub["sections"]
                 if (sec["title"] or "").startswith("Without asking"))
    assert "earned, not configured" in panel["title"]
    rows = next(b for b in panel["blocks"] if b["type"] == "list")["items"]
    assert any(r["id"] == "inbox.archive_noise" for r in rows)


def test_voice_orb_hello_and_conversation():
    # No identity facts yet: the orb's first words are the get-to-know-you ask.
    hello = client.post("/v1/voice/hello", headers=AUTH).json()
    assert hello["offer"] == "interview" and "get to know you" in hello["say"]

    # "What needs my attention" speaks the actual senders and offers next steps
    # (asks were settled by the send-flow tests, so accept either shape).
    r = client.post("/v1/voice/converse", headers=AUTH,
                    json={"messages": [{"role": "user", "text": "what needs my attention?"}]}).json()
    assert r["say"] and ("Want me to" in r["say"] or "clear" in r["say"])
    assert r["action"] == "none"

    # Navigation still works through conversation.
    r = client.post("/v1/voice/converse", headers=AUTH,
                    json={"messages": [{"role": "user", "text": "show me my emails"}]}).json()
    assert r["action"] == "open_screen" and r["screen"] == "inbox"

    # Old app builds' one-shot shape still answers.
    r = client.post("/v1/voice/command", headers=AUTH,
                    json={"transcript": "go to my hub"}).json()
    assert r["intent"] == "open_screen" and r["screen"] == "hub"

    # Every exchange lands in the event ledger.
    db = SessionLocal()
    events = recent_events(db, user_id="harshith", limit=8, types=["voice_command"])
    assert len(events) >= 3
    db.close()


def test_worth_knowing_emails_are_readable():
    screen = client.get("/v1/screen/inbox", headers=AUTH).json()
    reads = next((sec for sec in screen["sections"]
                  if (sec["title"] or "").startswith("Read only")), None)
    if reads is None:
        return  # stub mailbox produced no worth_knowing this run
    items = next(b for b in reads["blocks"] if b["type"] == "list")["items"]
    assert all(i.get("detail") for i in items)  # tap-to-read body present


def test_reflection_writes_brief_and_hub_speaks_it():
    # Nightly reflection (stub LLM -> deterministic fallback brief) writes the
    # hub/reflection_brief fact and logs its run.
    r = client.post("/v1/agents/orchestrator/think?kind=nightly", headers=AUTH).json()
    assert r["agent"] == "orchestrator" and r["facts_written"] >= 1

    db = SessionLocal()
    facts = read_facts(db, user_id="harshith", domains=["hub"], limit=5)
    brief = next(f for f in facts if f.key == "reflection_brief")
    assert brief.value["text"] and brief.value["date"]
    runs = recent_events(db, user_id="harshith", limit=5, types=["reflection_run"])
    assert runs and "remembered" in runs[0].payload
    db.close()

    # The Hub's brief card speaks the fresh reflection verbatim.
    hub = client.get("/v1/screen/hub", headers=AUTH).json()
    cards = [b for sec in hub["sections"] for b in sec["blocks"] if b["type"] == "agent_card"]
    assert next(c for c in cards if c["id"] == "morning-brief")["body"] == brief.value["text"]


def test_push_respects_the_attention_cap(monkeypatch):
    import superapp.push as push_module
    from superapp.push import send_push
    from superapp.substrate import write_fact

    class _FakeResp:
        def raise_for_status(self):
            return self

    monkeypatch.setattr(push_module.httpx, "post", lambda *a, **k: _FakeResp())

    db = SessionLocal()
    # A registered (fake) expo token makes sends observable; APNs is unconfigured.
    write_fact(db, user_id="cap-user", domain="system", key="expo_push_token",
               value={"token": "ExponentPushToken[test]"}, confidence=1.0, source_agent="user")
    db.commit()
    sent = sum(send_push(db, user_id="cap-user", title="t", body=str(i)) for i in range(6))
    db.commit()
    suppressed = recent_events(db, user_id="cap-user", limit=10, types=["push_suppressed"])
    logged = recent_events(db, user_id="cap-user", limit=10, types=["push_sent"])
    # Cap = 3: only three attempts became push_sent events, the rest suppressed.
    assert len(logged) == 3 and len(suppressed) == 3
    db.close()


def test_semantic_memory_degrades_on_sqlite():
    from superapp.memory import recall, remember

    db = SessionLocal()
    remember(db, user_id="harshith", domain="inbox", kind="email", ref_id="x",
             content="Lease renewal from Marcus")
    assert recall(db, user_id="harshith", query="lease") == []  # postgres-only, silently
    db.close()


def test_sent_by_nano_is_visible_and_in_voice_context():
    # The send-flow tests sent a draft; it must appear on the inbox screen...
    screen = client.get("/v1/screen/inbox", headers=AUTH).json()
    sent_sec = next(sec for sec in screen["sections"]
                    if (sec["title"] or "").startswith("Sent by Nano"))
    rows = next(b for b in sent_sec["blocks"] if b["type"] == "list")["items"]
    assert rows and all(r["detail"] for r in rows)  # readable, tap to expand
    assert rows[0]["title"].startswith("To ")

    # ...and in the orb's context, so "what did you send?" answers concretely.
    from superapp.routers.voice import _inbox_for_voice
    from superapp.substrate import get_context

    db = SessionLocal()
    voice = _inbox_for_voice(get_context(db, agent="hub", user_id="harshith"))
    assert voice["recently_sent"] and voice["recently_sent"][0]["to"]
    assert voice["recently_sent"][0]["body_excerpt"]
    db.close()


def test_nutrition_plan_from_voice_profile():
    from superapp.nutrition_plan import compute_plan, sanitize_profile
    from superapp.routers.voice import _execute

    # The math: a 75kg/175cm male, 2000-era, moderate, maintain.
    plan = compute_plan({"sex": "male", "born_year": 2000, "height_cm": 175,
                         "weight_kg": 75, "activity": "moderate", "goal": "maintain"})
    assert 2300 < plan["kcal"] < 2700 and plan["protein_g"] == 128

    # Junk from the model gets clamped away.
    assert sanitize_profile({"weight_kg": "not a number", "goal": "LOSE", "hack": 1}) == {"goal": "lose"}

    # The voice action writes profile + plan facts...
    db = SessionLocal()
    out = _execute(db, "harshith", {
        "action_type": "set_nutrition",
        "profile_json": '{"sex": "male", "born_year": 2000, "height_cm": 175, '
                        '"weight_kg": 75, "activity": "moderate", "goal": "maintain"}',
        "draft_id": "", "message_id": "", "reply_body": "", "to_addr": "", "subject": "",
    })
    db.commit()
    assert out == {}
    facts = {f.key: f.value for f in read_facts(db, user_id="harshith",
                                                domains=["nutrition"], limit=30)}
    assert facts["plan"]["kcal"] > 2000 and facts["profile"]["weight_kg"] == 75
    db.close()

    # ...and the nutrition screen becomes the Cal Neo layout: day strip,
    # ring hero with macro chips, water meter, snap CTA.
    screen = client.get("/v1/screen/home", headers=AUTH).json()
    blocks = [b for sec in screen["sections"] for b in sec["blocks"]]
    hero = next(b for b in blocks if b["type"] == "ring_hero")
    assert "KCAL LEFT OF" in hero["label"] and hero["pct_label"] == "EATEN"
    assert len(hero["chips"]) == 3 and hero["chips"][0].startswith("P ")
    strip = next(b for b in blocks if b["type"] == "day_strip")
    assert len(strip["days"]) == 14 and sum(1 for d in strip["days"] if d["today"]) == 1
    meters = next(b for b in blocks if b["type"] == "meter_row")
    assert {m["label"] for m in meters["meters"]} == {"WATER"}
    actions = next(b for b in blocks if b["type"] == "action_row")
    assert any("Snap the plate" in a["label"] for a in actions["actions"])


def test_cal_neo_onboarding_flow():
    # Suggest: live numbers for the targets step, nothing stored.
    r = client.post("/v1/nutrition/suggest", headers=AUTH,
                    json={"born_year": 1996, "height_cm": 172, "weight_kg": 70,
                          "steps_target": 10000}).json()
    assert 2000 < r["kcal"] < 3200 and r["bmi"] == 23.7 and r["bmi_band"] == "Healthy"

    # Onboard: partial body + overrides -> plan with quiet targets and a start date.
    state = client.post("/v1/nutrition/onboard", headers=AUTH,
                        json={"born_year": 1996, "height_cm": 172, "weight_kg": 70,
                              "steps_target": 10000, "kcal_override": 2200,
                              "water_override": 2500}).json()
    plan = state["plan"]
    assert state["onboarded"] and plan["kcal"] == 2200 and plan["water_ml"] == 2500
    assert plan["fiber_g"] == round(2200 / 1000 * 14) and plan["sugar_g_max"] == 55
    assert plan["steps_target"] == 10000 and plan["started"]
    assert state["day_n"] >= 1

    # Settings-style nudge: override only, profile persists, started survives.
    state2 = client.post("/v1/nutrition/onboard", headers=AUTH,
                         json={"kcal_override": 2100}).json()
    assert state2["plan"]["kcal"] == 2100
    assert state2["plan"]["started"] == plan["started"]

    # Health score: no meals -> unscored; with meals -> 5..100 with a note.
    from superapp.nutrition_plan import health_score
    assert health_score({"meals": []}, plan)[0] == -1
    s100, note = health_score({"meals": [1], "kcal": 900, "protein_g": 60,
                               "fiber_g": 12, "sugar_g": 10, "sodium_mg": 800}, plan)
    assert 5 <= s100 <= 100 and "meal" in note


def test_scout_task_queue_roundtrip():
    import superapp.config as config_module
    settings = config_module.get_settings()
    settings.worker_token = "wk-test-token"
    W = {"Authorization": "Bearer wk-test-token"}
    try:
        # Queue by voice-style instruction; worker pulls, completes; push+event land.
        t = client.post("/v1/tasks", headers=AUTH,
                        json={"instruction": "find 3 used tennis rackets under $100 nearby"}).json()
        assert t["status"] == "queued"

        assert client.get("/v1/tasks/next").status_code == 401  # worker auth required
        nxt = client.get("/v1/tasks/next", headers=W).json()["task"]
        assert nxt["id"] == t["id"] and nxt["instruction"].startswith("find 3")

        r = client.post(f"/v1/tasks/{t['id']}/complete", headers=W, json={"result": {
            "summary": "Two solid rackets at $60 and $85.",
            "shortlist": [{"title": "Wilson Pro", "price": "$60", "location": "5 km",
                           "url": "https://x", "why": "barely used"}],
            "caveats": ""}})
        assert r.json()["ok"]

        mine = client.get("/v1/tasks", headers=AUTH).json()["tasks"][0]
        assert mine["status"] == "done" and mine["result"]["shortlist"]

        db = SessionLocal()
        events = recent_events(db, user_id="harshith", limit=5, types=["task_completed"])
        assert events and events[0].payload["found"] == 1
        db.close()
    finally:
        settings.worker_token = ""


def test_draft_card_explains_why_it_wrote_this():
    screen = client.get("/v1/screen/inbox", headers=AUTH).json()
    card = next(b for sec in screen["sections"] for b in sec["blocks"]
                if b["type"] == "draft_card")
    assert card["why_detail"] and "nothing sends until you say so" in card["why_detail"].lower()


# ---------------------------------------------------------------- multi-user

def test_second_user_token_full_isolation():
    import superapp.config as config_module
    settings = config_module.get_settings()
    settings.user_tokens = "cofounder:cf-test-token-abc123"
    CF = {"Authorization": "Bearer cf-test-token-abc123"}
    try:
        # Co-founder authenticates and sees an EMPTY world, not harshith's.
        screen = client.get("/v1/screen/home", headers=CF).json()
        assert "Let's build your plan" in str(screen)  # unset, not inherited
        assert "eggs" not in str(screen)  # none of harshith's meals leak

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


# ---------------------------------------------------------------- sign-in

def test_google_signin_sessions():
    from superapp.auth_sessions import complete_signin
    import superapp.config as config_module

    settings = config_module.get_settings()
    settings.user_email_links = "harshithsesham007@gmail.com:harshith"
    try:
        db = SessionLocal()
        # Pre-linked email -> binds to the existing harshith identity + data.
        user, token = complete_signin(db, google_sub="g-sub-h", 
                                      email="harshithsesham007@gmail.com", name="Harshith")
        db.commit()
        assert user.id == "harshith"
        r = client.get("/v1/screen/hub", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200 and "Inbox Zero" in str(r.json())  # HIS data

        # Unknown email -> auto-provisioned isolated user.
        user2, token2 = complete_signin(db, google_sub="g-sub-t",
                                        email="tester.person@gmail.com", name="Tester")
        db.commit()
        assert user2.id == "testerperson"
        hub = client.get("/v1/screen/hub", headers={"Authorization": f"Bearer {token2}"}).json()
        assert "Connect your inbox" in str(hub)  # empty world

        # Same sub signing in again -> same user, new session.
        user3, token3 = complete_signin(db, google_sub="g-sub-t",
                                        email="tester.person@gmail.com", name="Tester")
        db.commit()
        assert user3.id == user2.id and token3 != token2

        # Garbage session still rejected; token hash stored, not the token.
        assert client.get("/v1/screen/hub",
                          headers={"Authorization": "Bearer not-a-session"}).status_code == 401
        from superapp.models import AuthSession
        assert all(len(row.token_hash) == 64 and token2 not in row.token_hash
                   for row in db.query(AuthSession))
        db.close()
    finally:
        settings.user_email_links = ""


def test_signin_start_requires_config():
    # Stub mode (no google client id): sign-in start refuses cleanly.
    assert client.get("/v1/auth/google/start", follow_redirects=False).status_code == 400
    # Forged callback state dies before any Google call.
    assert client.get("/v1/auth/google/callback",
                      params={"code": "x", "state": "123.forged"}).status_code == 403


# ---------------------------------------------------------------- interview

def test_identity_interview_flow():
    r = client.post("/v1/interview/start", headers=AUTH).json()
    assert "I'm Nano" in r["question"] and r["session_id"]
    sid = r["session_id"]

    # Stub mode walks the sections deterministically; answer through them all.
    answers = ["I'm Harshith, I build things.", "Mornings are coffee and code.",
               "Mostly my cofounder Rohith and my mom.", "Frugal except tools.",
               "Short and direct, lowercase.", "Never compromise on shipping.",
               "Run my inbox."]
    done, hops = False, 0
    for text in answers * 2:
        r = client.post(f"/v1/interview/{sid}/answer", headers=AUTH, json={"text": text}).json()
        hops += 1
        if r["done"]:
            done = True
            break
    assert done and hops <= 10
    assert "thank you" in r["question"].lower() or "yours" in r["question"].lower()

    # Transcript stored verbatim; identity facts distilled and visible to agents.
    db = SessionLocal()
    from superapp.models import InterviewTurn
    texts = [t.text for t in db.query(InterviewTurn).all()]
    assert "I'm Harshith, I build things." in texts
    from superapp.substrate import read_facts
    keys = {f.key for f in read_facts(db, user_id="harshith", domains=["identity"], limit=20)}
    assert {"identity", "communication_style", "decision_rules"} <= keys
    inbox_slice = get_context(db, agent="inbox", user_id="harshith")
    assert any(f["domain"] == "identity" for f in inbox_slice.facts)
    db.close()

    # Completed session refuses more answers; audio endpoint stubs to 204.
    assert client.post(f"/v1/interview/{sid}/answer", headers=AUTH,
                       json={"text": "more"}).status_code == 409
    turn_audio_url = r["audio_url"]
    assert client.get(turn_audio_url, headers=AUTH).status_code == 204


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


def test_flight_watch_lifecycle():
    import superapp.config as config_module
    settings = config_module.get_settings()
    settings.worker_token = "wk-test-token"
    W = {"Authorization": "Bearer wk-test-token"}
    try:
        # Create a watch with a target: first check is queued immediately.
        w = client.post("/v1/tasks/watch", headers=AUTH, json={
            "instruction": "watch flights from Columbus to Hyderabad in December",
            "target_price": 900}).json()
        assert w["id"] and w["first_check"]

        # Tick doesn't double-queue while a check is pending.
        assert client.post("/v1/tasks/flight-watch-tick", headers=W).json()["queued"] == 0

        # First check completes at $1,150: baseline only, no deal push event.
        nxt = client.get("/v1/tasks/next", headers=W).json()["task"]
        client.post(f"/v1/tasks/{nxt['id']}/complete", headers=W, json={"result": {
            "summary": "22 options", "caveats": "",
            "shortlist": [{"title": "Qatar, 1 stop", "price": "$1,150 round trip",
                           "location": "CMH-HYD", "url": "https://g", "why": "cheapest"}]}})
        watches = client.get("/v1/tasks/watches", headers=AUTH).json()["watches"]
        assert watches[0]["best_price"] == 1150

        # Tick queues a fresh check; a drop to $890 (under target) updates best.
        assert client.post("/v1/tasks/flight-watch-tick", headers=W).json()["queued"] == 1
        nxt = client.get("/v1/tasks/next", headers=W).json()["task"]
        client.post(f"/v1/tasks/{nxt['id']}/complete", headers=W, json={"result": {
            "summary": "drop", "caveats": "",
            "shortlist": [{"title": "Qatar, 1 stop", "price": "$890 round trip",
                           "location": "CMH-HYD", "url": "https://g", "why": "cheapest"}]}})
        watches = client.get("/v1/tasks/watches", headers=AUTH).json()["watches"]
        assert watches[0]["best_price"] == 890

        # Stop the watch; tick then queues nothing.
        assert client.delete(f"/v1/tasks/watch/{w['id']}", headers=AUTH).json()["ok"]
        assert client.post("/v1/tasks/flight-watch-tick", headers=W).json()["queued"] == 0
    finally:
        settings.worker_token = ""
