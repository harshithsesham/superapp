"""The grocery vertical.

Two things get most of the attention here, because they are the two that would
actually hurt someone: the forecast that drives the red shelf, and the gate
between "Nano built a basket" and "money left your account".
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

# The app's engine is installed exactly once, by test_spine. Standing up a
# second in-memory SQLite engine here would split the app's session from this
# module's: `client` would write to one database and `SessionLocal` would read
# an empty other one, and the tests would pass alone and fail as a suite.
from test_spine import AUTH, SessionLocal, client

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)


# --- the forecast ----------------------------------------------------------

def test_repeat_purchases_beat_the_category_guess():
    """The whole point: a household's own rhythm outranks an average. Someone
    who rebuys milk every 6 days should not be told it lasts a week."""
    from superapp.grocery.predict import forecast

    buys = [(NOW - timedelta(days=d), 1) for d in (24, 18, 12, 6)]
    f = forecast(purchases=buys, category="Dairy & Protein", now=NOW)
    assert f.basis == "measured"
    assert 5.5 <= f.days_supply <= 6.5
    # Six days of supply bought six days ago: due now, not next week.
    assert f.status == "out"

    cold = forecast(purchases=[], category="Dairy & Protein", now=NOW)
    assert cold.basis == "assumed" and cold.status == "stocked"


def test_bulk_buying_is_counted_on_both_sides():
    """The bug the review caught, pinned.

    The first version divided each gap by how much was bought (right: two
    cartons last twice as long) and then predicted from the interval alone,
    ignoring how much the LAST purchase held. Two cartons of a seven-day milk
    bought eight days ago came out "out", when its own arithmetic said six days
    left. Quantity has to appear in the learning AND the prediction.
    """
    from superapp.grocery.predict import forecast

    weekly = [(NOW - timedelta(days=22), 1), (NOW - timedelta(days=15), 1)]

    two = forecast(purchases=weekly + [(NOW - timedelta(days=8), 2)],
                   category="Dairy & Protein", now=NOW)
    assert two.status == "stocked"
    assert 5.5 <= two.days_left <= 6.5, "two cartons at 7 days each, 8 days in"

    one = forecast(purchases=weekly + [(NOW - timedelta(days=8), 1)],
                   category="Dairy & Protein", now=NOW)
    assert one.status == "out", "one carton on the same history is genuinely overdue"


def test_a_smaller_package_runs_out_sooner():
    """Buying an 8 oz carton instead of the usual gallon is not the same
    purchase. Throwing the size away made the household look like it had
    slowed down."""
    from superapp.grocery.predict import forecast

    usual = [(NOW - timedelta(days=21), 1, "1 gal"), (NOW - timedelta(days=14), 1, "1 gal")]
    small = forecast(purchases=usual + [(NOW - timedelta(days=2), 1, "8 floz")],
                     category="Dairy & Protein", now=NOW)
    big = forecast(purchases=usual + [(NOW - timedelta(days=2), 1, "1 gal")],
                   category="Dairy & Protein", now=NOW)
    assert small.days_left < big.days_left
    assert small.status in ("out", "running_low") and big.status == "stocked"


def test_the_same_size_written_three_ways_is_one_size():
    """A gallon, 128 fl oz and 3.78 L are the same milk. A shelf that cannot
    see that concludes consumption changed when only the label did."""
    from superapp.grocery.units import parse_size

    gal = parse_size("1 Gal")[0]
    floz = parse_size("128 fl oz")[0]
    litres = parse_size("3.78 L")[0]
    assert abs(gal - floz) / gal < 0.01
    assert abs(gal - litres) / gal < 0.01
    assert parse_size("500g") == (500.0, "g")
    assert parse_size("12 ct") == (12.0, "ct")
    assert parse_size("Whole Milk") is None


def test_mixed_units_fall_back_to_counting_packages():
    """Millilitres and grams cannot be compared. Coarser and honest beats
    precise and wrong."""
    from superapp.grocery.predict import consumption_rate

    mixed = [(NOW - timedelta(days=14), 1, "1 gal"), (NOW - timedelta(days=7), 1, "500 g")]
    rate, basis, unit = consumption_rate(mixed)
    assert unit == "pack", "no comparison is meaningful, so packages are counted"
    assert basis == "estimated" and rate is not None


def test_the_person_outranks_the_prediction():
    """They can see their own cupboard. An assistant that argues is worthless."""
    from superapp.grocery.predict import forecast

    just_bought = [(NOW - timedelta(days=1), 1)]
    confident = forecast(purchases=just_bought, category="Grains", now=NOW)
    assert confident.status == "stocked"

    declared = forecast(purchases=just_bought, category="Grains", now=NOW,
                        declared_out_at=NOW)
    assert declared.status == "out" and declared.basis == "declared"
    assert "you marked this out" in declared.reason


def test_forecast_never_returns_an_absurd_shelf_life():
    """Two purchases minutes apart would otherwise imply a supply of hours,
    and every item would live permanently on the red shelf."""
    from superapp.grocery.predict import MAX_DAYS, MIN_DAYS, forecast

    frantic = forecast(purchases=[(NOW - timedelta(minutes=30), 1), (NOW, 1)],
                       category="Snacks", now=NOW)
    assert MIN_DAYS <= frantic.days_supply <= MAX_DAYS

    glacial = forecast(purchases=[(NOW - timedelta(days=4000), 1), (NOW, 1)],
                       category="Grains", now=NOW)
    assert MIN_DAYS <= glacial.days_supply <= MAX_DAYS


# --- the shelf -------------------------------------------------------------

def test_two_spellings_of_one_product_land_on_one_shelf_item():
    """Receipts spell things differently. If they do not collapse, the forecast
    sees two items bought once each and never learns an interval at all."""
    from superapp.substrate.grocery import upsert_item

    db = SessionLocal()
    a = upsert_item(db, user_id="g1", name="Milk, Whole 1 Gal", category="Dairy & Protein")
    b = upsert_item(db, user_id="g1", name="WHOLE MILK 1GAL")
    db.commit()
    assert a.id == b.id
    # ...but genuinely different things stay apart.
    oat = upsert_item(db, user_id="g1", name="Oat Milk")
    db.commit()
    assert oat.id != a.id
    db.close()


def test_reading_the_same_receipt_twice_is_not_two_shopping_trips():
    """A duplicated purchase halves the measured interval and puts a stocked
    item on the red shelf."""
    from superapp.models import GroceryPurchase
    from superapp.substrate.grocery import record_purchase, upsert_item

    db = SessionLocal()
    item = upsert_item(db, user_id="g2", name="Rice", category="Grains")
    first = record_purchase(db, user_id="g2", item=item, purchased_at=NOW,
                            source="email", source_ref="receipt-1")
    again = record_purchase(db, user_id="g2", item=item, purchased_at=NOW,
                            source="email", source_ref="receipt-1")
    db.commit()
    assert first is not None and again is None
    assert db.scalar(select(GroceryPurchase).where(
        GroceryPurchase.user_id == "g2")) is not None
    assert len(db.scalars(select(GroceryPurchase).where(
        GroceryPurchase.user_id == "g2")).all()) == 1
    db.close()


def test_shelf_state_splits_out_the_two_computed_shelves():
    """Running low and Out of stock are views of the same items, and an item
    on one of them must not also sit on its category shelf twice."""
    from superapp.substrate.grocery import grocery_context, record_purchase, upsert_item

    db = SessionLocal()
    uid = "g3"
    milk = upsert_item(db, user_id=uid, name="Milk", category="Dairy & Protein")
    for d in (21, 14, 7):
        record_purchase(db, user_id=uid, item=milk, purchased_at=NOW - timedelta(days=d),
                        source="email", source_ref=f"r{d}")
    rice = upsert_item(db, user_id=uid, name="Basmati Rice", category="Grains")
    record_purchase(db, user_id=uid, item=rice, purchased_at=NOW - timedelta(days=1),
                    source="email", source_ref="r-rice")
    db.commit()

    state = grocery_context(db, uid)
    names_out = {s["name"] for s in state["out_of_stock"]}
    assert "Milk" in names_out, "bought every 7 days, last bought 7 days ago"
    assert "Basmati Rice" not in names_out
    assert state["item_count"] == 2
    assert state["measured_count"] >= 1
    # Every item still belongs to exactly one category shelf.
    shelved = [i["id"] for s in state["shelves"] for i in s["items"]]
    assert len(shelved) == len(set(shelved)) == 2
    db.close()


# --- the money gate --------------------------------------------------------

AUTH_UID = "harshith"   # who the dev bearer token authenticates as


def _basket(label):
    """A draft basket belonging to the authenticated user, via the API.

    Seeded under AUTH_UID rather than a per-test id: the endpoint builds the
    basket for whoever the token says, so seeding anyone else produces an
    empty one and the test passes for the wrong reason.
    """
    from superapp.substrate.grocery import record_purchase, upsert_item

    db = SessionLocal()
    item = upsert_item(db, user_id=AUTH_UID, name=f"Coffee {label}", category="Beverages")
    record_purchase(db, user_id=AUTH_UID, item=item,
                    purchased_at=NOW - timedelta(days=80),
                    source="email", source_ref=f"old-{label}")
    db.commit(); item_id = item.id; db.close()
    r = client.post("/v1/grocery/basket", headers=AUTH, json={"platform": "list", "item_ids": [item_id]})
    assert r.status_code == 200, r.text
    order = r.json()["order"]
    assert order is not None, "seeded item should be long overdue"
    return order


def test_placing_an_order_requires_a_human_yes():
    """Nano may fill a basket. It may never buy it. This is the gate."""
    order = _basket("g-order")
    r = client.post(f"/v1/grocery/orders/{order['id']}/place", headers=AUTH)
    assert r.status_code == 403
    assert "never buys on its own" in r.json()["detail"]


def test_a_yes_covers_the_basket_it_was_given_for_and_nothing_else():
    """Approving 'milk and eggs' must not become authority to buy whatever the
    basket happens to contain later."""
    order = _basket("g-order2")
    ok = client.post(f"/v1/grocery/orders/{order['id']}/confirm", headers=AUTH,
                     json={"fingerprint": order["fingerprint"]})
    assert ok.status_code == 200 and ok.json()["order"]["confirmed_by"] == "user"

    # Something changes the basket after the yes.
    from superapp.models import GroceryOrder
    db = SessionLocal()
    o = db.get(GroceryOrder, order["id"])
    o.lines = (o.lines or []) + [{"item_id": "x", "name": "Champagne", "quantity": 12}]
    db.commit(); db.close()

    stale = client.post(f"/v1/grocery/orders/{order['id']}/confirm", headers=AUTH,
                        json={"fingerprint": order["fingerprint"]})
    assert stale.status_code == 409
    assert "changed since you looked" in stale.json()["detail"]


def test_a_store_that_cannot_order_says_so_instead_of_inventing_an_id():
    """The mail seam's worst bug, ported: a client with no way to act used to
    return a plausible id and report success. Here that costs real groceries."""
    order = _basket("g-order3")
    client.post(f"/v1/grocery/orders/{order['id']}/confirm", headers=AUTH,
                json={"fingerprint": order["fingerprint"]})
    r = client.post(f"/v1/grocery/orders/{order['id']}/place", headers=AUTH)
    assert r.status_code == 422, "a platform that cannot check out is not a server error"
    assert "shopping list, not a store" in r.json()["detail"]

    from superapp.models import GroceryOrder
    db = SessionLocal()
    o = db.get(GroceryOrder, order["id"])
    assert o.status == "failed" and not o.external_id, "no id was invented"
    db.close()


def test_money_is_tier_three_and_has_no_autonomous_path():
    """Not a route test: the policy table itself. Every provenance is refused,
    so no cron, rule or spoken command can reach a charge."""
    from superapp.policy import assess

    for provenance in ("user", "email", "system"):
        v = assess("grocery.place_order", provenance=provenance)
        assert v.allowed is False and v.tier == 3
    assert assess("grocery.build_basket", provenance="system").allowed is True


def test_connection_status_reports_capability_not_a_stored_boolean():
    """The first version let /link write status="linked" with no authentication
    behind it, so the screen claimed a connection that did not exist — the exact
    failure the seam was built to prevent, in the module that quotes the rule.

    Status is now the result of trying to build the client, and it says what
    each platform can actually do.
    """
    r = client.post("/v1/grocery/platforms/link", headers=AUTH,
                    json={"platform": "walmart", "account_label": "rohit@example.com"})
    assert r.status_code == 200
    assert r.json()["available"] is False, "a preference is not a connection"
    assert "not available" in r.json()["unavailable_reason"]
    assert "Impact Radius" not in r.json()["unavailable_reason"]

    body = client.get("/v1/grocery/platforms", headers=AUTH).json()
    rows = {p["platform"]: p for p in body["platforms"]}
    assert "linked" not in rows["walmart"], "the misleading flag is gone"
    assert rows["walmart"]["available"] is False
    assert rows["list"]["available"] is True

    # And the screen must say where the shelf really comes from, or someone
    # will believe Nano reads their Instacart account and never forward the
    # receipts it actually needs.
    assert all(p["can_read_history"] is False for p in body["platforms"])
    assert body["history_source"]["kind"] == "email_receipts"


def test_instacart_handoff_is_unavailable_without_a_key_and_says_so():
    """No key configured must read as "not set up", never as a broken link or
    a silently empty basket."""
    from superapp.grocery.base import StoreNotConnected
    from superapp.grocery.factory import client_for

    db = SessionLocal()
    with pytest.raises(StoreNotConnected) as exc:
        client_for(db, "harshith", "instacart")
    assert "unavailable" in str(exc.value)
    assert "API key" not in str(exc.value)
    db.close()


def test_instacart_handoff_pins_the_exact_product_and_returns_a_link(monkeypatch):
    """The reason receipts matter: a recipe app sends "milk" and hopes; we send
    the UPC off this household's own receipt."""
    import superapp.grocery.providers as providers
    from superapp.grocery.base import OrderLine
    from superapp.grocery.providers import InstacartStore

    sent = {}

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"products_link_url": "https://instacart.com/store/list/abc"}

    def fake_post(url, json=None, timeout=None, headers=None):
        sent["url"], sent["payload"] = url, json
        return _Resp()

    monkeypatch.setattr(providers.httpx, "post", fake_post)
    store = InstacartStore(api_key="test-key")
    out = store.handoff([
        OrderLine(item_id="1", name="Whole Milk", quantity=2,
                  product_ref="0001234500012", product_ref_kind="upc"),
        OrderLine(item_id="2", name="Bananas", quantity=1),
    ])
    assert out["url"].startswith("https://instacart.com/")
    items = sent["payload"]["line_items"]
    assert items[0]["upcs"] == ["0001234500012"], "pinned to the exact product"
    assert items[0]["quantity"] == 2
    assert "upcs" not in items[1], "no identifier invented for an unknown product"

    # Handing over a basket is not checking out, and must not claim to be.
    from superapp.grocery.base import StoreUnsupported
    with pytest.raises(StoreUnsupported):
        store.place_order([])


def test_a_handoff_failure_is_loud_rather_than_an_empty_basket(monkeypatch):
    """A silent failure would leave the person believing a basket is waiting."""
    import httpx

    import superapp.grocery.providers as providers
    from superapp.grocery.base import OrderLine, StoreNotConnected
    from superapp.grocery.providers import InstacartStore

    def down(*a, **k):
        raise httpx.ConnectError("instacart down")

    monkeypatch.setattr(providers.httpx, "post", down)
    with pytest.raises(StoreNotConnected):
        InstacartStore(api_key="k").handoff([OrderLine(item_id="1", name="Milk")])


def test_an_unlinked_platform_raises_rather_than_falling_back_to_the_list():
    """"I added it to your list" when someone asked to order is a different
    outcome, and they have to be told which one happened."""
    from superapp.grocery.base import StoreNotConnected
    from superapp.grocery.factory import client_for

    db = SessionLocal()
    with pytest.raises(StoreNotConnected):
        client_for(db, "nobody-linked-this", "instacart")
    db.close()


# --- the screen ------------------------------------------------------------

def test_shelf_screen_renders_and_explains_itself():
    from superapp.agents.base import render_screen
    from superapp.substrate.grocery import record_purchase, upsert_item

    db = SessionLocal()
    uid = "g-render"
    item = upsert_item(db, user_id=uid, name="Doritos", category="Snacks")
    for d in (40, 26, 12):
        record_purchase(db, user_id=uid, item=item, purchased_at=NOW - timedelta(days=d),
                        source="email", source_ref=f"d{d}")
    db.commit()
    screen = render_screen(db, agent="grocery", user_id=uid)
    blocks = [b for s in screen.sections for b in s.blocks]
    kinds = {b.type for b in blocks}
    assert "shelf" in kinds
    shelf = next(b for b in blocks if b.type == "shelf")
    assert any(s.label == "Snacks" for s in shelf.shelves)
    # Whatever the verdict, the person can see the reasoning behind it.
    if any(s.tone in ("amber", "rose") for s in shelf.shelves):
        assert "list" in kinds, "a red shelf must come with its reasons"
    db.close()


def test_empty_shelf_offers_the_receipt_path_not_a_catalogue():
    from superapp.agents.base import render_screen

    db = SessionLocal()
    screen = render_screen(db, agent="grocery", user_id="g-empty")
    text = " ".join(b.text for s in screen.sections for b in s.blocks
                    if b.type == "text")
    assert "receipts" in text.lower()
    db.close()


def test_variants_stay_apart_while_package_sizes_merge():
    """Two different rules, and getting either backwards corrupts estimates.

    "Milk 1%" and "Milk 2%" are different products; merging their purchases
    makes both predictions wrong. "Milk 8 oz" and "Milk 1 Gal" are the SAME
    product in two amounts, and the size belongs in the arithmetic, not in the
    identity.
    """
    from superapp.substrate.grocery import slugify

    assert slugify("Milk 1%") != slugify("Milk 2%")
    assert slugify("Whole Milk") != slugify("Oat Milk")
    assert slugify("Organic Carrots 2 lb") != slugify("Carrots")

    assert slugify("Milk 8 oz") == slugify("Milk 1 Gal")
    assert slugify("Milk, Whole 1 Gal") == slugify("WHOLE MILK 1GAL")
    assert slugify("Great Value 2% Milk 1 gal") == slugify("2% Milk")


def test_an_old_receipt_never_overrides_todays_correction():
    """Backfilling last quarter's receipts must not tell someone they have milk
    they threw out this morning. Only shopping done SINCE the correction
    clears it."""
    from superapp.substrate.grocery import (item_state, record_purchase,
                                            set_declared_out, upsert_item)

    db = SessionLocal()
    uid = "g-stale"
    milk = upsert_item(db, user_id=uid, name="Milk", category="Dairy & Protein")
    record_purchase(db, user_id=uid, item=milk, purchased_at=NOW - timedelta(days=2),
                    source="email", source_ref="recent")
    set_declared_out(db, user_id=uid, item_id=milk.id, out=True)
    db.commit()
    assert item_state(db, uid, milk)["status"] == "out"

    # An import turns up a receipt from three months ago.
    record_purchase(db, user_id=uid, item=milk, purchased_at=NOW - timedelta(days=90),
                    source="email", source_ref="ancient")
    db.commit()
    db.refresh(milk)
    assert milk.declared_out_at is not None, "old news cannot restock a cupboard"
    assert item_state(db, uid, milk)["status"] == "out"

    # Actually shopping since the correction does clear it.
    from superapp.models import utcnow
    record_purchase(db, user_id=uid, item=milk, purchased_at=utcnow(),
                    source="manual", source_ref="today")
    db.commit()
    db.refresh(milk)
    assert milk.declared_out_at is None
    db.close()


def test_a_receipt_that_failed_to_parse_is_retried_not_forgotten():
    """The first version marked every candidate read before it had an answer,
    so one refusal or outage skipped that purchase permanently and the shelf
    was quietly wrong forever."""
    import json as _json

    from superapp.agents.grocery import MAX_RECEIPT_ATTEMPTS, _scan_receipts
    from superapp.agents.base import ThinkResult
    from superapp.llm.provider import LLMProvider
    from superapp.models import InboxMessage, utcnow
    from superapp.substrate import append_event, get_context

    uid = "g-retry"
    db = SessionLocal()
    db.add(InboxMessage(user_id=uid, account_email="me@example.com",
                        gmail_msg_id="rcpt-1", thread_id="t1", from_name="Walmart",
                        from_addr="orders@walmart.com", subject="Your Walmart order",
                        body_text="1 Whole Milk 1 gal $3.99", tier="receipt",
                        received_at=utcnow()))
    db.commit()

    class _R:
        def __init__(self, text, stubbed=False, refused=False):
            self.text, self.stubbed, self.refused = text, stubbed, refused

    provider = LLMProvider()

    # First scan: the model returns something unparseable.
    provider.complete = lambda db_, **kw: _R("{not json at all")
    result = ThinkResult()
    context = get_context(db, agent="grocery", user_id=uid)
    stats = _scan_receipts(db, context, provider, result)
    assert stats["failed"] == 1 and stats["purchases"] == 0
    kinds = [e.type for e in result.event_writes]
    assert "grocery_receipt_failed" in kinds
    assert "grocery_receipt_read" not in kinds, "an unanswered message is not done"
    for e in result.event_writes:
        append_event(db, user_id=uid, type=e.type, agent="grocery",
                     domain=e.domain, payload=e.payload)
    db.commit()

    # Second scan: the provider is healthy. The receipt must be picked up.
    good = {"is_grocery_receipt": True, "merchant": "Walmart", "suspicious": False,
            "purchased_at": "2026-09-01",
            "items": [{"name": "Whole Milk", "category": "Dairy & Protein",
                       "brand": "", "size": "1 gal", "quantity": 1,
                       "unit_price_cents": 399}]}
    provider.complete = lambda db_, **kw: _R(_json.dumps(good))
    result2 = ThinkResult()
    stats2 = _scan_receipts(db, get_context(db, agent="grocery", user_id=uid),
                            provider, result2)
    assert stats2["purchases"] == 1, "the retry recovered the purchase"
    assert "grocery_receipt_read" in [e.type for e in result2.event_writes]
    assert MAX_RECEIPT_ATTEMPTS >= 2
    db.close()


def test_the_grocery_screen_is_reachable_and_voice_can_name_it():
    """Voice returned open_screen: grocery for a screen the API did not serve,
    and the model could not even emit that value — it was missing from the enum."""
    from superapp.routers.voice import CONVERSE_SCHEMA

    assert client.get("/v1/screen/grocery", headers=AUTH).status_code == 200
    screens = CONVERSE_SCHEMA["properties"]["screen"]["enum"]
    assert "grocery" in screens
    assert "grocery_basket" in CONVERSE_SCHEMA["properties"]["action_type"]["enum"]


# --- self-review fixes -----------------------------------------------------

def test_voice_basket_actually_takes_you_to_the_shelf():
    """Nano announced a basket it had just built and left the app on the same
    screen: `_execute` returned the destination and the endpoint read only
    "say", dropping it. The client navigates on action=="open_screen" plus a
    screen name, so both have to survive the trip."""
    from datetime import timedelta

    from superapp.models import utcnow
    from superapp.routers.voice import _execute
    from superapp.substrate.grocery import record_purchase, upsert_item

    db = SessionLocal()
    uid = "v-nav"
    item = upsert_item(db, user_id=uid, name="Coffee", category="Beverages")
    record_purchase(db, user_id=uid, item=item, purchased_at=utcnow() - timedelta(days=300),
                    source="email", source_ref="old")
    db.commit()

    parsed = {"action_type": "grocery_basket", "grocery_items": ["coffee"], "say": "",
              "screen": "", "draft_id": "", "message_id": "", "reply_body": "",
              "to_addr": "", "subject": "", "profile_json": "", "mute_kind": "",
              "mute_sender": "", "priority_kind": "", "priority_sender": "",
              "listen": False}
    override = _execute(db, uid, parsed)
    assert override["action"] == "open_screen" and override["screen"] == "grocery"
    assert override.get("acted") is True

    # Replay exactly what the endpoint does with the override.
    if override.get("say"):
        parsed["say"] = override["say"]
    if override.get("action"):
        parsed["action_type"] = override["action"]
    if override.get("screen"):
        parsed["screen"] = override["screen"]
    acted = bool(override.get("acted")) or parsed["action_type"] in ("send_draft",)

    assert parsed["action_type"] == "open_screen", "the client navigates on this"
    assert parsed["screen"] == "grocery"
    assert acted is True, "building a basket is something that happened"
    db.close()


def test_numbers_in_a_product_name_are_identity_not_noise():
    """Stripping every bare number merged different dosages into one shelf item
    with interleaved history — the same corruption as merging 1% and 2% milk."""
    from superapp.substrate.grocery import slugify

    assert slugify("Vitamin D3 2000 IU") != slugify("Vitamin D3 5000 IU")
    assert slugify("Advil 200mg") != slugify("Advil 500mg")
    assert slugify("7 Up") != slugify("5 Gum")

    # Sizes still merge, because a size is not identity.
    assert slugify("Milk 8 oz") == slugify("Milk 1 Gal")
    assert slugify("Doritos Cool Ranch 9.25oz") == slugify("DORITOS COOL RANCH")
    # A receipt lot code is not a name.
    assert slugify("Whole Milk 4829173") == slugify("Whole Milk")


def test_the_shelf_does_not_query_once_per_item():
    """grocery_context runs on every screen load. One query per item cost 63
    queries for a 60-item shelf."""
    import string

    from sqlalchemy import event

    from superapp.db import engine
    from superapp.models import utcnow
    from superapp.substrate.grocery import grocery_context, record_purchase, upsert_item

    db = SessionLocal()
    uid = "g-perf"
    names = [f"{a}{b} cereal" for a in string.ascii_lowercase[:6]
             for b in string.ascii_lowercase[:6]][:30]
    for i, nm in enumerate(names):
        it = upsert_item(db, user_id=uid, name=nm, category="Snacks")
        record_purchase(db, user_id=uid, item=it, purchased_at=utcnow(),
                        source="email", source_ref=f"r{i}")
    db.commit()

    count = []
    def _seen(conn, cur, stmt, params, ctx, many):
        count.append(1)
    event.listen(engine, "before_cursor_execute", _seen)
    try:
        state = grocery_context(db, uid)
    finally:
        event.remove(engine, "before_cursor_execute", _seen)

    assert state["item_count"] == 30, "distinct products must not collapse"
    assert len(count) <= 6, f"one query per item is back: {len(count)} queries for 30 items"
    db.close()


def test_a_handoff_link_is_stored_whole(monkeypatch):
    """Half a URL is a dead link the person would tap."""
    import superapp.grocery.providers as providers
    from superapp.models import GroceryOrder

    long_url = "https://www.instacart.com/store/partner_recipe?" + "t=" + ("x" * 200)

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"products_link_url": long_url}

    from superapp.config import get_settings

    monkeypatch.setattr(providers.httpx, "post", lambda *a, **k: _Resp())
    settings = get_settings()
    prev = settings.instacart_api_key
    settings.instacart_api_key = "test-key"

    db = SessionLocal()
    o = GroceryOrder(user_id="harshith", platform="instacart",
                     lines=[{"item_id": "x", "name": "Milk", "quantity": 1}])
    db.add(o); db.commit(); oid = o.id; db.close()

    try:
        r = client.post(f"/v1/grocery/orders/{oid}/handoff", headers=AUTH)
    finally:
        settings.instacart_api_key = prev
    assert r.status_code == 200, r.text
    assert r.json()["url"] == long_url

    db = SessionLocal()
    stored = db.get(GroceryOrder, oid).external_id
    db.close()
    assert stored == long_url, f"stored {len(stored)} of {len(long_url)} characters"


def test_placing_an_order_records_the_tier_three_decision():
    """The gate here used to compute a verdict and discard it — code that read
    like a check and enforced nothing. The authorisation is the confirmation;
    what belongs here is the ledger entry."""
    from superapp.models import Decision

    order = _basket("ledger")
    client.post(f"/v1/grocery/orders/{order['id']}/confirm", headers=AUTH,
                json={"fingerprint": order["fingerprint"]})
    client.post(f"/v1/grocery/orders/{order['id']}/place", headers=AUTH)

    db = SessionLocal()
    rows = db.scalars(select(Decision).where(
        Decision.user_id == "harshith",
        Decision.action_key == "grocery.place_order")).all()
    db.close()
    assert rows, "a tier-3 act must leave a ledger entry"
    assert any(r.payload.get("risk_tier") == 3 for r in rows)
    assert all(r.decided_by == "user" for r in rows)
