"""User flows missing from the earlier release checks."""
import json
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from test_spine import SessionLocal, AUTH, client
from superapp import context_notes, memory
from superapp.models import GroceryItem, GroceryPurchase, GroceryReceipt, InboxMessage, InboxDraft, SavedContext, utcnow
from superapp.agents import grocery, inbox
from superapp.agents.base import ThinkResult
from superapp.routers import voice
from superapp.substrate import get_context
from superapp.substrate.history import record_message
from superapp.substrate.grocery import grocery_context, upsert_item


def receipt_response():
    return {"is_grocery_receipt": True, "merchant": "Instacart", "suspicious": False,
            "purchased_at": "", "items": [{"name": "Whole milk", "category": "Dairy & Protein",
            "brand": "", "size": "1 gal", "quantity": 1, "unit_price_cents": 400}]}


def old_receipt(db, uid, mid="historical-receipt", days=8):
    return record_message(db, user_id=uid, account_email="me@example.com", msg={
        "gmail_msg_id": mid, "from_addr": "receipts@instacart.com", "subject": "Your grocery receipt",
        "body_text": "Whole milk, 1 gallon, $4", "received_at": utcnow() - timedelta(days=days)})


def test_history_receipt_and_live_copy_produce_one_purchase_across_restarts():
    uid = str(uuid.uuid4()); calls = []
    def complete(*a, **kw):
        calls.append(kw)
        return SimpleNamespace(text=json.dumps(receipt_response()), stubbed=False, refused=False)
    provider = SimpleNamespace(complete=complete)
    with SessionLocal() as db:
        old_receipt(db, uid)
        db.add(InboxMessage(user_id=uid, account_email="me@example.com", gmail_msg_id="historical-receipt",
            from_addr="receipts@instacart.com", subject="Your grocery receipt", body_text="Whole milk", received_at=utcnow()))
        db.commit()
        grocery._scan_receipts(db, get_context(db, agent="grocery", user_id=uid), provider, ThinkResult())
        db.commit()
    with SessionLocal() as db:
        grocery._scan_receipts(db, get_context(db, agent="grocery", user_id=uid), provider, ThinkResult())
        assert db.scalar(select(func.count()).select_from(GroceryPurchase).where(GroceryPurchase.user_id == uid)) == 1
        assert db.scalar(select(GroceryReceipt.status).where(GroceryReceipt.user_id == uid)) == "done"
    assert len(calls) == 1


def test_dispatcher_reads_historical_receipts_without_creating_email_work(monkeypatch):
    uid = str(uuid.uuid4())
    with SessionLocal() as db:
        old_receipt(db, uid, mid="G" * 180)
        for n in range(310):
            db.add(InboxMessage(user_id=uid, account_email="me@example.com", gmail_msg_id=str(n),
                from_addr="alex@example.com", subject="Hello", body_text="Unrelated newer mail"))
        db.commit()
    monkeypatch.setattr(grocery.LLMProvider, "complete", lambda *a, **kw:
        SimpleNamespace(text=json.dumps(receipt_response()), stubbed=False, refused=False))
    assert grocery.scan_pending_receipts(user_id=uid) == 1
    with SessionLocal() as db:
        assert db.scalar(select(GroceryPurchase.source_ref).where(GroceryPurchase.user_id == uid)) == "G" * 180
        assert db.scalar(select(func.count()).select_from(InboxMessage).where(InboxMessage.user_id == uid)) == 310
        assert db.scalar(select(InboxDraft.id).where(InboxDraft.user_id == uid)) is None
        assert grocery_context(db, uid)["item_count"] == 1
    assert grocery.scan_pending_receipts(user_id=uid) == 0


def test_ancient_receipts_teach_history_without_restocking_old_one_off_items():
    uid = str(uuid.uuid4())
    with SessionLocal() as db:
        old_receipt(db, uid, days=600)
        provider = SimpleNamespace(complete=lambda *a, **kw:
            SimpleNamespace(text=json.dumps(receipt_response()), stubbed=False, refused=False))
        grocery._scan_receipts(db, get_context(db, agent="grocery", user_id=uid), provider, ThinkResult())
        db.commit()
        assert db.scalar(select(GroceryPurchase.id).where(GroceryPurchase.user_id == uid))
        assert grocery_context(db, uid)["item_count"] == 0


@pytest.mark.parametrize("response", [[], {**receipt_response(), "items": [{"name": "Milk", "quantity": "bad"}]}])
def test_malformed_receipts_remain_retryable(response):
    uid = str(uuid.uuid4())
    with SessionLocal() as db:
        old_receipt(db, uid); db.commit()
        provider = SimpleNamespace(complete=lambda *a, **kw:
            SimpleNamespace(text=json.dumps(response), stubbed=False, refused=False))
        stats = grocery._scan_receipts(db, get_context(db, agent="grocery", user_id=uid), provider, ThinkResult())
        db.commit()
        assert stats["failed"] == 1
        assert db.scalar(select(GroceryReceipt.status).where(GroceryReceipt.user_id == uid)) == "failed"


def test_grocery_add_edit_handoff_preserves_items_and_never_places_order(monkeypatch):
    seen = []
    def handoff(lines):
        seen.append(lines)
        return {"url": "https://www.instacart.com/store/shopping_lists/test"}
    monkeypatch.setattr("superapp.routers.grocery.client_for", lambda *a: SimpleNamespace(handoff=handoff))
    first = client.post("/v1/grocery/items", headers=AUTH, json={"name": f"Coffee {uuid.uuid4()}"}).json()["item_id"]
    second = client.post("/v1/grocery/items", headers=AUTH, json={"name": f"Eggs {uuid.uuid4()}"}).json()["item_id"]
    order = client.post("/v1/grocery/basket", headers=AUTH, json={"item_ids": [first]}).json()["order"]
    order = client.post("/v1/grocery/basket", headers=AUTH, json={"item_ids": [second], "append": True}).json()["order"]
    assert {l["item_id"] for l in order["lines"]} == {first, second}
    path = f"/v1/grocery/orders/{order['id']}"
    old = order["fingerprint"]
    lines = [{"item_id": first, "quantity": 2}, {"item_id": second, "quantity": 1}]
    result = client.patch(path, headers=AUTH, json={"fingerprint": old, "lines": lines, "platform": "instacart"})
    assert result.status_code == 200, result.text
    order = result.json()["order"]
    assert client.patch(path, headers=AUTH, json={"fingerprint": old, "lines": lines}).status_code == 409
    assert client.post(path + "/handoff", headers=AUTH, json={"fingerprint": old}).status_code == 409
    result = client.post(path + "/handoff", headers=AUTH, json={"fingerprint": order["fingerprint"]})
    assert result.status_code == 200, result.text
    assert result.json()["order"]["status"] == "handed_off"
    assert result.json()["order"]["confirmed_by"] == ""
    assert seen[0][0].quantity == 2
    assert client.post(path + "/handoff", headers=AUTH).status_code == 200
    assert len(seen) == 1  # retry reuses the link
    with SessionLocal() as db:
        other = upsert_item(db, user_id="foreign", name=f"Private {uuid.uuid4()}")
        db.commit(); foreign_id = other.id
    assert client.patch(path, headers=AUTH, json={"fingerprint": order["fingerprint"], "lines": [{"item_id": foreign_id, "quantity": 1}]}).status_code == 422


def test_voice_adds_unknown_items_without_a_dead_end_question():
    uid = str(uuid.uuid4())
    with SessionLocal() as db:
        parsed = {"action_type": "grocery_basket", "grocery_items": ["Oat milk"]}
        result = voice._execute(db, uid, parsed, user_text="Add oat milk to my list")
        db.commit()
        assert result["acted"] and result["screen"] == "grocery"
        assert "confirm" not in result["say"].lower()
        assert grocery_context(db, uid)["pending_orders"][0]["lines"][0]["name"] == "Oat milk"


def test_forget_that_uses_conversation_and_stays_forgotten_next_session(monkeypatch):
    uid = str(uuid.uuid4()); words = "Remember I prefer Panera on trips"
    monkeypatch.setattr(voice.LLMProvider, "complete", lambda *a, **kw:
        SimpleNamespace(text="", stubbed=True, refused=False))
    turns = [voice.Turn(role="user", text=words)]
    with SessionLocal() as db:
        assert voice.converse(voice.ConverseBody(messages=turns), user_id=uid, db=db)["acted"]
        turns += [voice.Turn(role="nano", text="I'll remember that."), voice.Turn(role="user", text="Forget that")]
        result = voice.converse(voice.ConverseBody(messages=turns), user_id=uid, db=db)
        assert result["acted"] and "forgotten" in result["say"]
    with SessionLocal() as db:
        assert context_notes.recent_context(db, uid) == []
        assert db.scalar(select(SavedContext.id).where(SavedContext.user_id == uid)) is None


def test_forget_requires_explicit_intent_and_never_deletes_other_users_notes():
    uid = str(uuid.uuid4())
    with SessionLocal() as db:
        note = context_notes.save_context(db, user_id=uid, text="Panera is my favorite")
        db.commit()
        parsed = {"action_type": "forget_context", "memory_id": note.id}
        assert not voice._execute(db, uid, parsed, user_text="What is on my calendar?").get("acted")
        assert not voice._execute(db, uid, parsed, user_text="Don't forget that").get("acted")
        assert not voice._execute(db, "other", parsed, user_text="Forget that").get("acted")
        assert db.get(SavedContext, note.id)


def test_ambiguous_forget_requests_ask_without_deleting():
    uid = str(uuid.uuid4())
    with SessionLocal() as db:
        context_notes.save_context(db, user_id=uid, text="James manages Seattle")
        context_notes.save_context(db, user_id=uid, text="James likes coffee")
        assert context_notes.resolve_forget(db, uid, "Forget James", []) == ""
        assert context_notes.resolve_forget(db, uid, "Forget that", []) == ""
        assert len(context_notes.recent_context(db, uid)) == 2


def test_pending_history_is_information_not_an_archive_veto(monkeypatch):
    from test_inbox_release import message
    from superapp.substrate.inbox import upsert_account
    uid = str(uuid.uuid4())
    monkeypatch.setattr(memory, "available", lambda db: True)
    monkeypatch.setattr(memory, "recall_for_agent", lambda *a, **kw: [])
    with SessionLocal() as db:
        upsert_account(db, user_id=uid, email="slow@example.com", provider="gmail")
        msg = message(db, uid)
        provider = SimpleNamespace(complete=lambda *a, **kw:
            SimpleNamespace(text='{"veto":false}', stubbed=False, refused=False))
        assert inbox._verify_clear(db, get_context(db, agent="inbox", user_id=uid), provider, msg)
        monkeypatch.setattr(memory, "recall_for_agent", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("offline")))
        assert not inbox._verify_clear(db, get_context(db, agent="inbox", user_id=uid), provider, msg)


def test_receipt_outage_retries_after_cooldown_instead_of_retiring_mail():
    uid = str(uuid.uuid4())
    with SessionLocal() as db:
        old_receipt(db, uid)
        checkpoint = GroceryReceipt(user_id=uid, source_ref="historical-receipt", status="failed", attempts=3)
        db.add(checkpoint); db.commit()
        provider = SimpleNamespace(complete=lambda *a, **kw:
            SimpleNamespace(text=json.dumps(receipt_response()), stubbed=False, refused=False))
        context = get_context(db, agent="grocery", user_id=uid)
        assert grocery._scan_receipts(db, context, provider, ThinkResult())["scanned"] == 0
        checkpoint.updated_at = utcnow() - timedelta(days=2)
        db.commit()
        assert grocery._scan_receipts(db, context, provider, ThinkResult())["purchases"] == 1


@pytest.mark.parametrize("handed_off", [False, True])
def test_background_scans_and_new_items_preserve_the_users_existing_list(handed_off):
    from superapp.substrate.grocery import add_to_basket, record_purchase
    uid = str(uuid.uuid4())
    with SessionLocal() as db:
        coffee = upsert_item(db, user_id=uid, name="Coffee")
        order = add_to_basket(db, user_id=uid, items=[coffee])
        order.lines = [{**order.lines[0], "quantity": 4}]
        if handed_off:
            order.status = "handed_off"; order.external_id = "https://www.instacart.com/test"
        milk = upsert_item(db, user_id=uid, name="Milk")
        record_purchase(db, user_id=uid, item=milk, purchased_at=utcnow() - timedelta(days=40), source_ref="old-milk")
        db.commit()
        assert grocery.propose_basket(db, uid).lines == order.lines
        assert order.lines[0]["quantity"] == 4 and len(order.lines) == 1
        updated = add_to_basket(db, user_id=uid, items=[milk])
        assert updated.id == order.id and updated.lines[0]["quantity"] == 4
        assert len(updated.lines) == 2 and updated.external_id == ""
        assert updated.status == "draft"


def test_ambiguous_grocery_request_does_not_partially_create_items():
    uid = str(uuid.uuid4())
    with SessionLocal() as db:
        upsert_item(db, user_id=uid, name="Whole milk")
        upsert_item(db, user_id=uid, name="Oat milk")
        result = voice._execute(db, uid, {"action_type": "grocery_basket", "grocery_items": ["Coffee", "Milk"]}, user_text="Add coffee and milk")
        db.commit()
        assert result["action"] == "none"
        assert list(db.scalars(select(GroceryItem.name).where(GroceryItem.user_id == uid).order_by(GroceryItem.name))) == ["Oat milk", "Whole milk"]
