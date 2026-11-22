import pytest

from app.providers.base import Location
from app.providers.cart import build_provider_cart, selections_from_cart
from app.providers.fake_instamart import TOKEN, FakeInstamart
from app.providers.instamart import (
    BadRequest, CartChanged, Confirmation, ConfirmationRequired, DomainError, InstamartClient,
    InstamartProvider, Unavailable, _same_amount, outcome_from,
)
from app.schemas import Ingredient, ParsedDish

LOC = Location(address_id="addr_1")


@pytest.fixture
def fake():
    return FakeInstamart()


@pytest.fixture
def prov(fake):
    sleeps = []
    p = InstamartProvider(InstamartClient(TOKEN, url="http://fake/im", http=fake.client(), sleep=sleeps.append))
    p.sleeps = sleeps
    return p


def fill_cart(prov, qty_onions=3):
    """A cart over the 99 minimum: onions + garlic."""
    d = ParsedDish(dish="t", base_servings=2, ingredients=[Ingredient(name="onion", quantity=1000 * qty_onions, unit="g"), Ingredient(name="garlic", quantity=20, unit="g")])
    cart = build_provider_cart(d, 2, prov, LOC)
    return prov.set_cart(selections_from_cart(cart), LOC)


def confirm(prov, method="Cash", confirmed=True, to_pay=None):
    to_pay = to_pay or prov.get_cart().to_pay
    return Confirmation(to_pay=to_pay, address_id="addr_1", payment_method=method, confirmed=confirmed)


def tools(fake):
    return [t for t, _ in fake.calls]


# --- the confirmation guard ------------------------------------------------

def test_nothing_is_placed_without_an_explicit_yes(prov, fake):
    fill_cart(prov)
    before = len(fake.calls)
    with pytest.raises(ConfirmationRequired):
        prov.checkout(confirm(prov, confirmed=False))
    assert len(fake.calls) == before + 1 and "checkout" not in tools(fake)[before:]  # only the get_cart for to_pay
    assert fake.orders == {}


def test_a_changed_price_stops_the_order(prov, fake):
    fill_cart(prov)
    with pytest.raises(CartChanged) as e:
        prov.checkout(confirm(prov, to_pay="1"))  # the customer saw 1, the cart is something else
    assert "price changed" in e.value.message and "checkout" not in tools(fake) and fake.orders == {}


def test_an_empty_cart_cannot_be_ordered(prov, fake):
    with pytest.raises(CartChanged):
        prov.checkout(confirm(prov, to_pay="0"))
    assert "checkout" not in tools(fake)


def test_unknown_payment_methods_are_refused(prov, fake):
    fill_cart(prov)
    with pytest.raises(BadRequest):
        prov.checkout(Confirmation(to_pay=prov.get_cart().to_pay, address_id="addr_1", payment_method="Bitcoin", confirmed=True))
    assert "checkout" not in tools(fake)


def test_cash_on_delivery_places_the_order_and_empties_the_cart(prov, fake):
    fill_cart(prov)
    out = prov.checkout(confirm(prov, "Cash"))
    assert out.status == "placed" and out.order_ids == ["ord_1"] and out.payment_method == "Cash"
    assert out.message == "Instamart order placed successfully." and out.delivery_address == "12 MG Road, Bengaluru"
    assert fake.orders["ord_1"]["status"] == "PLACED" and fake.cart == []


def test_the_same_cart_cannot_be_ordered_twice_by_accident(prov, fake):
    fill_cart(prov)
    prov.checkout(confirm(prov))
    fill_cart(prov)  # the customer rebuilds an identical cart
    with pytest.raises(DomainError) as e:
        prov.checkout(confirm(prov))
    assert "duplicate" in e.value.message and len(fake.orders) == 1
    fill_cart(prov)
    assert prov.checkout(confirm(prov), confirm_duplicate=True).status == "placed"  # only with an explicit override


def test_the_minimum_order_is_enforced_by_the_provider(prov):
    prov.set_cart([], LOC)
    d = ParsedDish(dish="t", base_servings=2, ingredients=[Ingredient(name="onion", quantity=100, unit="g")])
    prov.set_cart(selections_from_cart(build_provider_cart(d, 2, prov, LOC)), LOC)  # one small pack: under 99
    with pytest.raises(DomainError) as e:
        prov.checkout(confirm(prov))
    assert "Minimum order" in e.value.message


def test_an_invalid_address_is_a_bad_request(prov):
    fill_cart(prov)
    with pytest.raises(BadRequest):
        prov.checkout(Confirmation(to_pay=prov.get_cart().to_pay, address_id="addr_999", payment_method="Cash", confirmed=True))


# --- order placement is never retried -------------------------------------

def test_a_failed_checkout_is_not_retried(prov, fake):
    fill_cart(prov)
    fake.tool_failures["checkout"] = [503, 503, 503]
    with pytest.raises(Unavailable):
        prov.checkout(confirm(prov))
    assert tools(fake).count("checkout") == 1 and prov.sleeps == []
    assert fake.orders == {}  # and nothing was placed behind our back


# --- UPI -------------------------------------------------------------------

def test_upi_starts_pending_then_succeeds_and_is_confirmed_exactly_once(prov, fake):
    fill_cart(prov)
    out = prov.checkout(confirm(prov, "UPI"))
    assert out.status == "pending_payment" and out.paas_id and out.bridge_url.startswith("https://")
    assert out.poll_interval_ms == 1500 and out.max_poll_ms == 20000 and fake.orders["ord_1"]["status"] == "PENDING_PAYMENT"

    first = prov.payment_status(out.paas_id, out.order_ids[0])
    assert first.status == "pending" and "confirm_order" not in tools(fake)
    second = prov.payment_status(out.paas_id, out.order_ids[0])
    assert second.status == "success" and second.message == "Instamart order placed successfully."
    assert fake.orders["ord_1"]["status"] == "PLACED" and tools(fake).count("confirm_order") == 1
    third = prov.payment_status(out.paas_id, out.order_ids[0])  # already confirmed: no second confirm
    assert third.status == "success" and tools(fake).count("confirm_order") == 1


def test_a_failed_payment_places_no_order(prov, fake):
    fill_cart(prov)
    out = prov.checkout(confirm(prov, "UPI"))
    fake.payments[out.paas_id]["polls"] = 0

    def failing(args):
        return {"paasId": args["paasId"], "status": "failed", "terminal": True, "isTerminalSuccess": False, "isTerminalFailure": True}

    fake._tool_check_payment_status = failing
    status = prov.payment_status(out.paas_id, out.order_ids[0])
    assert status.status == "failed" and "No order was placed" in status.message and "confirm_order" not in tools(fake)


def test_if_confirming_fails_to_reach_swiggy_the_customer_keeps_waiting_not_failing(prov, fake):
    fill_cart(prov)
    out = prov.checkout(confirm(prov, "UPI"))
    prov.payment_status(out.paas_id, out.order_ids[0])
    fake.tool_failures["confirm_order"] = [503]
    status = prov.payment_status(out.paas_id, out.order_ids[0])
    assert status.status == "pending" and tools(fake).count("confirm_order") == 1  # not retried automatically
    assert prov.payment_status(out.paas_id, out.order_ids[0]).status == "success"  # the next poll finishes it


def test_payment_options_only_offer_what_swiggy_returns(prov):
    assert prov.payment_options() == [{"id": "UPI", "label": "UPI"}, {"id": "Cash", "label": "Cash on delivery"}]


# --- helpers ---------------------------------------------------------------

@pytest.mark.parametrize(
    "a,b,same",
    [("113", "113", True), ("113", "113.00", True), ("Rs 113", "113", True), ("₹113.50", "113.5", True),
     ("113", "114", False), (None, "113", False), ("n/a", "n/a", True), ("", "", True)],
)
def test_same_amount(a, b, same):
    assert _same_amount(a, b) is same


def test_outcome_shapes():
    single = outcome_from({"orderId": "o1", "status": "PLACED", "paymentMethod": "Cash", "cartTotal": "113", "deliveryAddress": "Home"})
    assert (single.status, single.order_ids, single.total, single.delivery_address) == ("placed", ["o1"], "113", "Home")
    multi = outcome_from({"orders": [{"orderId": "a", "status": "PLACED"}, {"error": "out of stock"}], "allSucceeded": False, "paymentMethod": "Cash"})
    assert multi.status == "partial" and multi.order_ids == ["a"] and multi.failed == 1 and "1 of 2" in multi.message
    allok = outcome_from({"orders": [{"orderId": "a"}, {"orderId": "b"}], "allSucceeded": True, "paymentMethod": "Cash"})
    assert allok.status == "placed" and allok.order_ids == ["a", "b"]
    upi = outcome_from({"orderId": "o2", "status": "PENDING_PAYMENT", "paymentMethod": "UPI", "paasId": "p", "bridgeUrl": "https://x", "pollingIntervalInMs": 1500, "maxTimeToPollForInMs": 9000})
    assert upi.status == "pending_payment" and upi.paas_id == "p" and upi.max_poll_ms == 9000
