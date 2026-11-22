"""An in-memory stand-in for the Instamart MCP server, for tests and local development.

SHAPES come from Swiggy's reference docs (tool arguments, `structuredContent`, the error
envelope, 401 / 429 / 5xx behaviour). The docs print NO sample responses and NO example
`quantityDescription` values, so every product and size string below is INVENTED. When you
get staging access, capture real responses and replace CATALOGUE with them.

    fake = FakeInstamart()
    client = InstamartClient("test-token", url="http://fake/im", http=fake.client())
"""
import json
from dataclasses import dataclass, field

import httpx

TOKEN = "test-token"
MAX_QTY = 10

# name, brand, [(size text, mrp, offer price, in_stock)]
_PRODUCTS = [
    ("Onion", "Fresh", [("1 kg", 55, 49, True), ("500 g", 30, 28, True), ("2 kg", 105, 92, True)]),
    ("Tomato", "Fresh", [("500 g", 40, 36, True), ("1 kg", 75, 68, True)]),
    ("Garlic", "Fresh", [("250 g", 80, 70, True), ("100 g", 40, 35, True)]),
    ("Butter", "Amul", [("100 g", 60, 58, True), ("500 g", 300, 285, True)]),
    ("Toned Milk", "Amul", [("500 ml", 29, 28, True), ("1 L", 58, 56, True)]),
    ("Eggs", "Farm", [("Pack of 6", 60, 54, True), ("Pack of 12", 115, 105, True)]),
    ("Penne Pasta", "Del Monte", [("500 g", 135, 120, True), ("2 x 250 g", 125, 110, True)]),
    ("Chicken Breast Boneless", "Licious", [("500 g", 320, 280, True)]),
    ("Fresh Cream", "Amul", [("200 ml", 70, 65, True)]),
    ("Parmesan Cheese", "Imported", [("100 g", 260, 250, False), ("200 g", 480, 460, True)]),
    ("Parsley", "Fresh", [("100 g", 35, 30, True)]),
    ("Iodised Salt", "Tata", [("1 kg", 30, 28, True)]),
    ("Black Pepper", "Catch", [("50 g", 90, 85, True)]),
    ("Turmeric Powder", "Everest", [("100 g", 48, 45, True)]),
    ("Basmati Rice", "India Gate", [("1 kg", 160, 148, True), ("5 kg", 760, 720, True)]),
    ("Mystery Spice Box", "Unknown", [("assorted", 99, 89, True)]),  # a size we can't read
]


def _build() -> list[dict]:
    out, n = [], 0
    for i, (name, brand, variants) in enumerate(_PRODUCTS, start=1):
        vs = []
        for size, mrp, offer, stock in variants:
            n += 1
            vs.append({
                "spinId": f"spin_{n}", "skuId": f"sku_{n}", "quantityDescription": size,
                "displayName": name, "brandName": brand,
                "price": {"mrp": mrp, "offerPrice": offer},
                "isInStockAndAvailable": stock, "maxQuantity": MAX_QTY,
            })
        out.append({
            "displayName": name, "brand": brand, "inStock": any(v["isInStockAndAvailable"] for v in vs),
            "isAvail": True, "productId": f"prod_{i}", "parentProductId": f"parent_{i}", "variations": vs,
        })
    return out


CATALOGUE = _build()
_BY_SPIN = {v["spinId"]: (p, v) for p in CATALOGUE for v in p["variations"]}


@dataclass
class FakeInstamart:
    token: str = TOKEN
    addresses: list[dict] = field(default_factory=lambda: [
        {"id": "addr_1", "addressLine": "12 MG Road, Bengaluru", "phoneNumber": "9XXXXXXXX0", "addressCategory": "Home"},
    ])
    calls: list[tuple[str, dict]] = field(default_factory=list)   # (tool, arguments)
    cart: list[dict] = field(default_factory=list)                # [{"spinId","skuId","quantity"}]
    queue: list = field(default_factory=list)                     # injected failures, FIFO

    # ---- failure injection ----
    def fail_next(self, status: int = 503, *, headers: dict | None = None, jsonrpc_code: int | None = None, times: int = 1):
        for _ in range(times):
            self.queue.append((status, headers or {}, jsonrpc_code))

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self._handle))

    # ---- HTTP ----
    def _handle(self, request: httpx.Request) -> httpx.Response:
        if self.queue:
            status, headers, code = self.queue.pop(0)
            if code is not None:
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": code, "message": "injected"}}, headers=headers)
            return httpx.Response(status, json={"error": {"message": "injected"}}, headers=headers)
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"error": {"message": "unauthenticated"}})
        body = json.loads(request.content)
        params = body["params"]
        tool, args = params["name"], params.get("arguments", {})
        self.calls.append((tool, args))
        handler = getattr(self, f"_tool_{tool}", None)
        if handler is None:
            return httpx.Response(400, json={"error": {"message": f"Invalid tool {tool}"}})
        try:
            data = handler(args)
        except _Bad as e:
            return httpx.Response(400, json={"error": {"message": str(e)}})
        except _Domain as e:
            return self._result(body["id"], {"success": False, "error": {"message": str(e), "reportId": "rep_1"}}, is_error=True)
        return self._result(body["id"], data)

    @staticmethod
    def _result(rid, structured: dict, *, is_error: bool = False) -> httpx.Response:
        return httpx.Response(200, json={
            "jsonrpc": "2.0", "id": rid,
            "result": {"content": [{"type": "text", "text": "prose summary for the model"}], "structuredContent": structured, "isError": is_error},
        })

    # ---- tools ----
    def _tool_get_addresses(self, args):
        return {
            "addresses": self.addresses,
            "pagination": {"page": 1, "pageSize": 10, "total": len(self.addresses), "totalPages": 1, "hasMore": False},
            "resolution": {"defaultAddressId": self.addresses[0]["id"] if self.addresses else None,
                           "resolutionSource": "ranked_saved_address", "needsUserClarification": False},
        }

    def _tool_search_products(self, args):
        q = (args.get("query") or "").strip().lower()
        if not q:
            raise _Bad("Invalid query: cannot be empty")
        if not (args.get("addressId") or (args.get("latitude") is not None and args.get("longitude") is not None)):
            raise _Domain("No delivery location. Choose an address first.")
        words = q.split()
        hits = [p for p in CATALOGUE if all(w in p["displayName"].lower() or w in p["brand"].lower() for w in words)]
        return {"nextOffset": "", "products": hits}

    def _tool_update_cart(self, args):
        items = args.get("items")
        if not isinstance(items, list):
            raise _Bad("Missing items")
        kept, removed, reduced = [], [], []
        for it in items:
            found = _BY_SPIN.get(it.get("spinId"))
            if not found:
                raise _Bad("Invalid spinId")
            _, v = found
            if not v["isInStockAndAvailable"]:
                removed.append({"spinId": v["spinId"], "itemName": v["displayName"]})
                continue
            q = int(it["quantity"])
            if q > MAX_QTY:
                reduced.append({"spinId": v["spinId"], "itemName": v["displayName"], "requestedQuantity": q, "cappedQuantity": MAX_QTY, "reason": "max per order"})
                q = MAX_QTY
            kept.append({"spinId": v["spinId"], "skuId": v["skuId"], "quantity": q})
        self.cart = kept  # replaces the entire cart
        out = self._cart_view()
        if removed:
            out["removedOutOfStockItems"] = removed
        if reduced:
            out["reducedQuantityItems"] = reduced
        return out

    def _tool_get_cart(self, args):
        return self._cart_view()

    def _tool_clear_cart(self, args):
        self.cart = []
        return {"verified": True}

    def _cart_view(self) -> dict:
        items, total = [], 0.0
        for c in self.cart:
            p, v = _BY_SPIN[c["spinId"]]
            price = v["price"]["offerPrice"]
            total += price * c["quantity"]
            items.append({
                "spinId": v["spinId"], "skuId": v["skuId"], "productId": p["productId"], "itemName": v["displayName"],
                "itemVariant": v["quantityDescription"], "quantity": c["quantity"], "isInStockAndAvailable": True,
                "mrp": v["price"]["mrp"], "discountedFinalPrice": price, "maxQuantity": MAX_QTY,
            })
        if not items:
            return {"cartAbsent": True, "cartAbsentReason": "empty", "items": [], "cartTotalAmount": "0",
                    "billBreakdown": {"lineItems": [], "toPay": {"label": "To pay", "value": "0"}}}
        fee = 0 if total >= 199 else 15
        return {
            "cartId": "cart_1", "items": items, "cartTotalAmount": f"{total:g}",
            "billBreakdown": {
                "lineItems": [{"label": "Item total", "value": f"{total:g}"}, {"label": "Handling fee", "value": f"{fee:g}"}],
                "toPay": {"label": "To pay", "value": f"{total + fee:g}"},
            },
            "availablePaymentMethods": ["UPI", "Cash"],
        }


class _Bad(Exception):
    pass


class _Domain(Exception):
    pass
