import pytest

import app.main as main
from app import demo
from app.schemas import ParsedDish

RECIPE = {
    "dish": "garlic onion thing", "base_servings": 2,
    "ingredients": [
        {"name": "onion", "quantity": 3000, "unit": "g"},
        {"name": "garlic", "quantity": 20, "unit": "g"},
        {"name": "butter", "quantity": 30, "unit": "ml"},
        {"name": "saffron", "quantity": 1, "unit": "g"},
    ],
}


@pytest.fixture
def client(client_factory, seeded_engine, monkeypatch):
    monkeypatch.setattr(main, "parse_dish", lambda d: ParsedDish.model_validate(RECIPE))
    monkeypatch.setattr(main, "is_builtin", lambda d: False)
    return client_factory(seeded_engine)


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("DEMO_INSTAMART", "1")


def build(client, **extra):
    return client.post("/cart", json={"dish": "x", "servings": 2, "provider": "instamart", **extra})


def picks(cart):
    return [{"offer": l["offer"], "quantity": l["packs"]} for l in cart["lines"] if l["status"] == "priced"]


def review(client, cart=None, items=None):
    cart = cart or build(client).json()
    return client.post("/instamart/review", json={"items": items or picks(cart)})


# --- the flag --------------------------------------------------------------

def test_demo_mode_is_off_by_default(client):
    assert client.get("/api/config").json() == {"demo_instamart": False}
    assert client.post("/instamart/review", json={"items": [{"offer": {}, "quantity": 1}]}).status_code == 404
    r = build(client)
    assert r.status_code == 400 and "isn't connected" in r.json()["detail"]
    assert client.post("/cart", json={"dish": "x"}).status_code == 200  # the normal catalogue path is untouched


def test_config_reports_demo_mode(client, on):
    assert client.get("/api/config").json() == {"demo_instamart": True}


# --- provider carts --------------------------------------------------------

def test_a_cart_priced_from_instamart(client, on):
    cart = build(client).json()
    assert cart["provider"] == "instamart" and cart["provider_badge"] == "Powered by Instamart"
    by_name = {l["name"]: l for l in cart["lines"]}
    assert by_name["onion"]["product"] == "Fresh Onion 1 kg" and by_name["onion"]["packs"] == 3  # 3000 g wanted, 1 kg packs
    assert by_name["butter"]["status"] == "priced" and by_name["saffron"]["status"] == "open"
    assert all(l["provider"] == "instamart" and l["offer"] for l in cart["lines"] if l["status"] == "priced")
    assert by_name["onion"]["alternatives"]  # the customer can change it


def test_the_recipe_key_rebuilds_a_provider_cart_without_rereading_the_source(client, on):
    first = build(client).json()
    again = client.post("/cart", json={"dish": "x", "recipe_key": first["recipe_key"], "servings": 4, "provider": "instamart"}).json()
    assert again["provider"] == "instamart" and again["servings"] == 4
    assert {l["name"]: l["need"] for l in again["lines"]}["garlic"] == "40 g"


def test_the_catalogue_is_still_the_default_provider(client, on):
    cart = client.post("/cart", json={"dish": "x", "servings": 2}).json()
    assert cart["provider"] == "catalogue" and cart["provider_badge"] == "" and all(l["offer"] is None for l in cart["lines"])


# --- review ----------------------------------------------------------------

def test_review_shows_the_cart_as_instamart_sees_it(client, on):
    cart = build(client).json()
    r = review(client, cart)
    assert r.status_code == 200
    body = r.json()
    assert body["demo"] is True and body["badge"] == "Powered by Instamart"
    assert body["address"] == {"id": "addr_1", "line": "12 MG Road, Bengaluru", "name": "Demo customer"}
    assert {i["name"] for i in body["items"]} == {"Onion", "Garlic", "Butter"}
    onion = next(i for i in body["items"] if i["name"] == "Onion")
    assert onion["quantity"] == 3 and onion["variant"] == "1 kg" and onion["unit_price"] == 49
    assert body["payment_methods"] == [{"id": "UPI", "label": "UPI"}, {"id": "Cash", "label": "Cash on delivery"}]
    assert body["bill_lines"][0]["label"] == "Item total" and float(body["total"]) == pytest.approx(cart["total"])


def test_prices_come_from_the_provider_never_from_the_browser(client, on):
    cart = build(client).json()
    items = picks(cart)
    for it in items:
        it["price"] = 0.01          # a tampered price is ignored
        it["offer"]["price"] = 0.01
    honest = review(client, cart).json()
    tampered = client.post("/instamart/review", json={"items": items}).json()
    assert tampered["total"] == honest["total"] and float(tampered["total"]) > 100


def test_review_reports_what_the_provider_changed(client, on):
    cart = build(client).json()
    onion = next(l for l in cart["lines"] if l["name"] == "onion")
    r = client.post("/instamart/review", json={"items": [{"offer": onion["offer"], "quantity": 25}]}).json()
    assert r["reduced"][0]["requestedQuantity"] == 25 and r["reduced"][0]["cappedQuantity"] == 10
    assert r["items"][0]["quantity"] == 10


def test_review_validates_its_input(client, on):
    assert client.post("/instamart/review", json={"items": []}).status_code == 422
    assert client.post("/instamart/review", json={"items": [{"offer": {"provider": "blinkit", "variant_id": "x", "sku_id": "y"}, "quantity": 1}]}).status_code == 422
    assert client.post("/instamart/review", json={"items": [{"offer": {"provider": "instamart", "variant_id": "x", "sku_id": "y"}, "quantity": 0}]}).status_code == 422
    r = client.post("/instamart/review", json={"items": [{"offer": {"provider": "instamart", "variant_id": "spin_9999", "sku_id": "y"}, "quantity": 1}]})
    assert r.status_code == 400 and "Invalid" in r.json()["detail"]


# --- placing the order -----------------------------------------------------

def order(client, cart, to_pay, *, method="Cash", confirmed=True):
    return client.post("/instamart/checkout", json={
        "items": picks(cart), "payment_method": method, "to_pay": to_pay, "confirmed": confirmed, "address_id": "addr_1",
    })


def test_cash_on_delivery_end_to_end(client, on):
    cart = build(client).json()
    to_pay = review(client, cart).json()["to_pay"]
    r = order(client, cart, to_pay)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "placed" and body["order_ids"] == ["ord_1"] and body["demo"] is True
    assert body["message"] == "Instamart order placed successfully." and body["delivery_address"] == "12 MG Road, Bengaluru"
    assert demo.fake().orders["ord_1"]["status"] == "PLACED"


def test_an_unconfirmed_order_is_refused_and_nothing_is_placed(client, on):
    cart = build(client).json()
    r = order(client, cart, review(client, cart).json()["to_pay"], confirmed=False)
    assert r.status_code == 400 and "confirm" in r.json()["detail"].lower() and demo.fake().orders == {}


def test_a_stale_amount_is_refused_so_the_customer_reviews_again(client, on):
    cart = build(client).json()
    r = order(client, cart, "1")
    assert r.status_code == 409 and "price changed" in r.json()["detail"] and demo.fake().orders == {}


def test_a_double_click_does_not_place_two_orders(client, on):
    cart = build(client).json()
    to_pay = review(client, cart).json()["to_pay"]
    assert order(client, cart, to_pay).status_code == 200
    second = order(client, cart, to_pay)
    assert second.status_code == 409 and "duplicate" in second.json()["detail"] and len(demo.fake().orders) == 1


def test_the_minimum_order_is_explained(client, on, monkeypatch):
    monkeypatch.setattr(main, "parse_dish", lambda d: ParsedDish.model_validate({
        "dish": "tiny", "base_servings": 2, "ingredients": [{"name": "onion", "quantity": 100, "unit": "g"}]}))
    cart = build(client).json()
    to_pay = review(client, cart).json()["to_pay"]
    r = order(client, cart, to_pay)
    assert r.status_code == 409 and "Minimum order" in r.json()["detail"]


def test_payment_method_is_limited_to_what_the_screen_offers(client, on):
    cart = build(client).json()
    r = order(client, cart, "113", method="Bitcoin")
    assert r.status_code == 422 and demo.fake().orders == {}


def test_upi_end_to_end(client, on):
    cart = build(client).json()
    placed = order(client, cart, review(client, cart).json()["to_pay"], method="UPI").json()
    assert placed["status"] == "pending_payment" and placed["paas_id"] and placed["bridge_url"].startswith("https://")
    assert placed["poll_interval_ms"] == 1500
    poll = lambda: client.post("/instamart/payment-status", json={"paas_id": placed["paas_id"], "order_id": placed["order_ids"][0]}).json()
    assert poll()["status"] == "pending"
    done = poll()
    assert done["status"] == "success" and done["message"] == "Instamart order placed successfully."
    assert demo.fake().orders["ord_1"]["status"] == "PLACED"
    assert [t for t, _ in demo.fake().calls].count("confirm_order") == 1
    assert poll()["status"] == "success" and [t for t, _ in demo.fake().calls].count("confirm_order") == 1


def test_payment_status_rejects_an_unknown_payment(client, on):
    r = client.post("/instamart/payment-status", json={"paas_id": "nope", "order_id": "ord_9"})
    assert r.status_code == 400


def test_the_demo_routes_are_hidden_when_off(client):
    assert client.post("/instamart/checkout", json={"items": [], "payment_method": "Cash", "to_pay": "1", "confirmed": True, "address_id": "a"}).status_code == 404
    assert client.post("/instamart/payment-status", json={"paas_id": "p", "order_id": "o"}).status_code == 404
