import json
import logging

import httpx
import pytest

from app.providers.base import Location, Offer
from app.providers.fake_instamart import TOKEN, FakeInstamart
from app.providers.instamart import (
    AuthExpired, BadRequest, DomainError, Forbidden, InstamartClient, InstamartProvider,
    RateLimited, Selection, Unavailable, offers_from, cart_result,
)

LOC = Location(address_id="addr_1")


class Env:
    """A fake server plus a client whose sleeping and clock we control."""

    def __init__(self):
        self.fake = FakeInstamart()
        self.sleeps: list[float] = []
        self.now = 0.0
        self.client = InstamartClient(
            TOKEN, url="http://fake/im", http=self.fake.client(),
            sleep=self._sleep, clock=lambda: self.now,
        )
        self.provider = InstamartProvider(self.client)

    def _sleep(self, s):
        self.sleeps.append(s)
        self.now += s

    @property
    def tools(self):
        return [t for t, _ in self.fake.calls]


@pytest.fixture
def env():
    return Env()


# --- request shape ---------------------------------------------------------

def test_request_matches_the_documented_shape():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"structuredContent": {"ok": True}}})

    client = InstamartClient("tok-123", url="http://fake/im", http=httpx.Client(transport=httpx.MockTransport(handler)))
    client.call("search_products", {"query": "onion", "addressId": "a1"})
    client.call("get_cart")
    first, second = seen
    assert first.method == "POST" and str(first.url) == "http://fake/im"
    assert first.headers["authorization"] == "Bearer tok-123"
    assert first.headers["content-type"].startswith("application/json")
    assert first.headers["accept"] == "application/json, text/event-stream"
    body = json.loads(first.content)
    assert body == {"jsonrpc": "2.0", "method": "tools/call",
                    "params": {"name": "search_products", "arguments": {"query": "onion", "addressId": "a1"}}, "id": 1}
    assert json.loads(second.content)["id"] == 2  # ids advance


def test_an_empty_token_is_refused_up_front():
    with pytest.raises(AuthExpired):
        InstamartClient("", url="http://fake/im")


def test_default_base_url_is_staging_and_overridable(monkeypatch):
    from app.providers import instamart

    assert "staging" in instamart.DEFAULT_BASE_URL and instamart.DEFAULT_BASE_URL.endswith("/im")
    monkeypatch.setenv("SWIGGY_MCP_BASE_URL", "https://example.test/im")
    assert InstamartClient("t").url == "https://example.test/im"


# --- reading results -------------------------------------------------------

def test_data_comes_from_structured_content_not_the_prose(env):
    data = env.client.call("get_addresses", {})
    assert data["addresses"][0]["id"] == "addr_1" and data["pagination"]["total"] == 1


@pytest.mark.parametrize(
    "result,expected",
    [
        ({"content": [{"type": "text", "text": '{"a": 1}'}]}, {"a": 1}),                                   # no structuredContent
        ({"structuredContent": {"success": True, "data": {"b": 2}, "message": "ok"}}, {"b": 2}),          # envelope
        ({"structuredContent": {"c": 3}, "content": [{"type": "text", "text": "prose"}]}, {"c": 3}),      # plain data
        ({"structuredContent": {}, "content": [{"type": "text", "text": "not json"}]}, {}),               # nothing usable
    ],
)
def test_result_unwrapping(result, expected):
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})))
    assert InstamartClient("t", url="http://f/im", http=http).call("x") == expected


# --- errors ----------------------------------------------------------------

def test_a_domain_failure_carries_the_message_and_report_id(env):
    with pytest.raises(DomainError) as e:
        env.provider.search("onion", Location())  # no location -> documented success:false answer
    assert "No delivery location" in e.value.message and e.value.report_id == "rep_1"
    assert env.sleeps == []  # a terminal answer is never retried


@pytest.mark.parametrize("kind", ["http401", "rpc"])
def test_expired_sessions_raise_auth_expired_without_retrying(env, kind):
    if kind == "http401":
        env.fake.token = "someone-elses-token"
    else:
        env.fake.fail_next(200, jsonrpc_code=-32001)
    with pytest.raises(AuthExpired):
        env.client.call("get_cart")
    assert env.sleeps == [] and len(env.fake.calls) <= 1


def test_forbidden_and_bad_requests_are_not_retried(env):
    env.fake.fail_next(200, jsonrpc_code=-32003)
    with pytest.raises(Forbidden):
        env.client.call("get_cart")
    with pytest.raises(BadRequest) as e:
        env.client.call("search_products", {"query": " "})  # 400 "Invalid query"
    assert "Invalid query" in e.value.message and env.sleeps == []


# --- retries ---------------------------------------------------------------

def test_server_errors_back_off_500ms_doubling_then_succeed(env):
    env.fake.fail_next(503, times=3)
    assert env.client.call("get_cart")["cartAbsent"] is True
    assert env.sleeps == [0.5, 1.0, 2.0]


def test_backoff_is_capped_and_gives_up_after_five_attempts(env):
    env.fake.fail_next(502, times=10)
    with pytest.raises(Unavailable):
        env.client.call("get_cart")
    assert env.sleeps == [0.5, 1.0, 2.0, 4.0]  # 5 attempts = 4 waits
    assert len(env.fake.queue) == 5            # exactly 5 of the 10 failures were consumed


def test_retry_after_is_honoured(env):
    env.fake.fail_next(429, headers={"Retry-After": "3"})
    env.client.call("get_cart")
    assert env.sleeps == [3.0]


def test_json_rpc_rate_limit_code_is_also_retried(env):
    env.fake.fail_next(200, jsonrpc_code=-32000)
    env.client.call("get_cart")
    assert len(env.sleeps) == 1


def test_a_30_second_budget_stops_retries_early(env):
    env.fake.fail_next(429, headers={"Retry-After": "20"}, times=5)
    with pytest.raises(RateLimited):
        env.client.call("get_cart")
    assert env.sleeps == [20.0]  # the second 20 s wait would blow the 30 s budget


def test_dropped_connections_are_retried(env):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"structuredContent": {"ok": 1}}})

    sleeps = []
    client = InstamartClient("t", url="http://f/im", http=httpx.Client(transport=httpx.MockTransport(handler)), sleep=sleeps.append)
    assert client.call("get_cart") == {"ok": 1} and sleeps == [0.5, 1.0]


@pytest.mark.parametrize("tool", ["checkout", "confirm_order"])
def test_placing_an_order_is_never_retried(env, tool):
    env.fake.fail_next(503, times=3)
    with pytest.raises(Unavailable):
        env.client.call(tool, {"addressId": "addr_1"})
    assert env.sleeps == [] and len(env.fake.queue) == 2  # one attempt only


def test_the_retry_choice_can_be_overridden(env):
    env.fake.fail_next(503)
    with pytest.raises(Unavailable):
        env.client.call("get_cart", retry=False)  # reads are normally retried; here we say no
    assert env.sleeps == []

    # ...and a caller who has verified an order was NOT placed (via get_orders) may opt in
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, json={})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"structuredContent": {"orderId": "o1"}}})

    sleeps = []
    client = InstamartClient("t", url="http://f/im", http=httpx.Client(transport=httpx.MockTransport(handler)), sleep=sleeps.append)
    assert client.call("checkout", {"addressId": "a"}, retry=True) == {"orderId": "o1"} and sleeps == [0.5]


# --- privacy ---------------------------------------------------------------

def test_the_token_and_arguments_never_reach_the_logs(env, caplog):
    caplog.set_level(logging.DEBUG)
    env.provider.search("a very specific onion query", LOC)
    env.fake.fail_next(503)
    env.client.call("get_cart")
    text = caplog.text
    assert "tool=search_products" in text  # we do log which tool ran
    assert TOKEN not in text and "a very specific onion query" not in text and "addr_1" not in text


# --- provider: search ------------------------------------------------------

def test_search_sends_the_address_or_coordinates(env):
    env.provider.search("onion", LOC)
    env.provider.search("onion", Location(latitude=12.9, longitude=77.6))
    (_, a), (_, b) = env.fake.calls
    assert a == {"query": "onion", "addressId": "addr_1"}
    assert b == {"query": "onion", "latitude": 12.9, "longitude": 77.6}


def test_search_returns_offers_in_the_providers_order(env):
    offers = env.provider.search("onion", LOC)
    assert [o.size_text for o in offers] == ["1 kg", "500 g", "2 kg"]
    first = offers[0]
    assert (first.provider, first.name, first.brand, first.price, first.mrp) == ("instamart", "Onion", "Fresh", 49, 55)
    assert (first.pack_size, first.pack_unit) == (1000, "g") and first.in_stock and first.label == "Fresh Onion 1 kg"


def test_offers_from_is_tolerant():
    data = {"products": [{
        "displayName": "Cream", "brand": "Amul", "inStock": True, "isAvail": False, "productId": "p1",
        "variations": [
            {"spinId": "s1", "skuId": "k1", "quantityDescription": "200 ml", "price": {"mrp": "70", "offerPrice": "65.5"}},   # strings; stock from product flags
            {"spinId": "s2", "skuId": "k2", "quantityDescription": "1 L", "price": {"mrp": 300}},                              # no offer price: falls back to mrp
            {"skuId": "k3", "quantityDescription": "5 L", "price": {"offerPrice": 1}},                                          # no spinId: can't be added to a cart
            {"spinId": "s4", "skuId": "k4", "quantityDescription": "weird", "price": {}},                                       # no price at all
            {"spinId": "s5", "skuId": "k5", "quantityDescription": "assorted", "price": {"offerPrice": 9}, "isInStockAndAvailable": True, "maxQuantity": 4},
        ],
    }]}
    offers = offers_from(data)
    assert [o.variant_id for o in offers] == ["s1", "s2", "s5"]
    s1, s2, s5 = offers
    assert s1.price == 65.5 and s1.mrp == 70 and not s1.in_stock          # product-level isAvail=False
    assert s2.price == 300
    assert s5.in_stock and s5.max_quantity == 4 and not s5.sized           # size we couldn't read


def test_offers_carry_opaque_ids_for_the_cart(env):
    ref = env.provider.search("garlic", LOC)[0].ref()
    assert set(ref) == {"provider", "product_id", "variant_id", "sku_id", "size_text", "max_quantity"}
    assert ref["provider"] == "instamart" and ref["variant_id"].startswith("spin_")


# --- provider: cart --------------------------------------------------------

def _pick(env, name, size):
    return next(o for o in env.provider.search(name, LOC) if o.size_text == size)


def test_set_cart_sends_spin_sku_quantity_and_the_address(env):
    onion = _pick(env, "onion", "1 kg")
    env.provider.set_cart([Selection(onion, 2), Selection(_pick(env, "garlic", "250 g"), 1), Selection(_pick(env, "salt", "1 kg"), 0)], LOC)
    tool, args = env.fake.calls[-1]
    assert tool == "update_cart" and args["selectedAddressId"] == "addr_1"
    assert args["items"] == [
        {"spinId": onion.variant_id, "skuId": onion.sku_id, "quantity": 2},
        {"spinId": _pick(env, "garlic", "250 g").variant_id, "skuId": _pick(env, "garlic", "250 g").sku_id, "quantity": 1},
    ]  # zero-quantity selections are left out


def test_set_cart_replaces_the_whole_cart(env):
    env.provider.set_cart([Selection(_pick(env, "onion", "1 kg"), 1)], LOC)
    res = env.provider.set_cart([Selection(_pick(env, "garlic", "250 g"), 1)], LOC)
    assert res.total == "70" and len(env.fake.cart) == 1  # the onion is gone, not added to


def test_cart_result_reads_totals_and_the_bill(env):
    res = env.provider.set_cart([Selection(_pick(env, "onion", "1 kg"), 2)], LOC)  # 98
    assert res.total == "98" and res.to_pay == "113"  # + a 15 handling fee under 199
    assert res.bill_lines == [("Item total", "98"), ("Handling fee", "15")]
    assert env.provider.get_cart().total == "98"


def test_out_of_stock_and_capped_items_are_reported_not_hidden(env):
    out_of_stock = next(o for o in env.provider.search("parmesan", LOC) if o.size_text == "100 g")
    res = env.provider.set_cart([Selection(out_of_stock, 1), Selection(_pick(env, "onion", "1 kg"), 25)], LOC)
    assert res.removed_out_of_stock == ["Parmesan Cheese"]
    assert res.reduced[0]["requestedQuantity"] == 25 and res.reduced[0]["cappedQuantity"] == 10
    assert res.total == "490"  # 10 onions, not 25


def test_clear_cart(env):
    env.provider.set_cart([Selection(_pick(env, "onion", "1 kg"), 1)], LOC)
    env.provider.clear_cart()
    assert env.fake.cart == [] and env.provider.get_cart().raw["cartAbsent"] is True


def test_guest_coordinates_are_used_when_there_is_no_address(env):
    env.provider.set_cart([Selection(_pick(env, "onion", "1 kg"), 1)], Location(latitude=12.9, longitude=77.6))
    _, args = env.fake.calls[-1]
    assert args["latitude"] == 12.9 and args["longitude"] == 77.6 and "selectedAddressId" not in args


def test_addresses(env):
    assert env.provider.addresses()["addresses"][0]["id"] == "addr_1"
    assert env.fake.calls[-1] == ("get_addresses", {"page": 1, "pageSize": 10})


def test_cart_result_handles_a_bare_response():
    res = cart_result({})
    assert res.total == "" and res.bill_lines == [] and res.to_pay is None and res.warnings == []
    res = cart_result({"cartWarning": {"statusCode": 1, "message": "Add Rs 50 more"}, "addressWarning": "Far away", "unserviceableItems": [{"itemName": "Ice"}]})
    assert res.warnings == ["Far away", "Add Rs 50 more"] and res.unserviceable == ["Ice"]
