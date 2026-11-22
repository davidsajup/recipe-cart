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
    tool_failures: dict = field(default_factory=dict)             # tool name -> [http status, ...]
    orders: dict = field(default_factory=dict)                    # orderId -> order
    payments: dict = field(default_factory=dict)                  # paasId -> {"orderId", "polls", "confirmed"}
    polls_to_success: int = 2                                     # UPI "succeeds" on this many polls
    _order_n: int = 0
    _last_snapshot: str | None = None                             # for the duplicate-order guard

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
        if self.tool_failures.get(tool):
            return httpx.Response(self.tool_failures[tool].pop(0), json={"error": {"message": "injected"}})
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

    # ---- payment and orders (shapes from the checkout / pay-with-upi docs) ----
    def _tool_get_payment_options(self, args):
        view = self._cart_view()
        return {
            "platforms": {"mobile": {"methods": [{"id": "upi_intent", "displayName": "UPI app"}]}, "desktop": {"methods": [{"id": "upi_qr", "displayName": "Scan QR"}]}},
            "cod": {"available": True, "id": "cod", "displayName": "Cash"},
            "swiggyMoney": {"available": False, "id": "swiggy_money", "displayName": "Swiggy Money"},
            "allMethods": [
                {"id": "upi_intent", "groupName": "UPI", "displayName": "UPI", "kind": "upi"},
                {"id": "cod", "groupName": "Cash", "displayName": "Cash on delivery", "kind": "cod"},
            ],
            "paymentAmount": view["billBreakdown"]["toPay"]["value"],
            "placeOrderToolName": "checkout",
        }

    def _tool_checkout(self, args):
        if not self.cart:
            raise _Domain("Your cart is empty.")
        if args.get("addressId") not in {a["id"] for a in self.addresses}:
            raise _Bad("Invalid addressId")
        method = args.get("paymentMethod") or "Cash"
        if method not in ("UPI", "Cash", "COD", "SwiggyPay"):
            raise _Bad("Invalid paymentMethod")
        if method == "SwiggyPay":
            raise _Domain("Swiggy Money isn't available for this account.")
        snapshot = json.dumps(self.cart, sort_keys=True)
        if snapshot == self._last_snapshot and not args.get("confirmDuplicate"):
            raise _Domain("This looks like a duplicate of your last order. Confirm to place it again.")
        view = self._cart_view()
        to_pay = view["billBreakdown"]["toPay"]["value"]
        if float(to_pay) < 99:
            raise _Domain("Minimum order not met (cart under 99).")
        self._order_n += 1
        order_id = f"ord_{self._order_n}"
        address = next(a for a in self.addresses if a["id"] == args["addressId"])
        base = {"orderId": order_id, "paymentMethod": method if method != "COD" else "Cash", "cartTotal": to_pay,
                "addressId": address["id"], "deliveryAddress": address["addressLine"], "deliveryLabel": address.get("addressCategory", "")}
        if method == "UPI":
            paas = f"paas_{self._order_n}"
            self.payments[paas] = {"orderId": order_id, "polls": 0, "confirmed": False}
            self.orders[order_id] = {**base, "status": "PENDING_PAYMENT"}
            return {**base, "paymentMethod": "UPI", "status": "PENDING_PAYMENT", "paasId": paas, "transactionId": f"txn_{self._order_n}",
                    "bridgeUrl": f"https://pay.example/demo/{paas}", "upiIntentUrl": f"upi://pay?pa=demo&am={to_pay}",
                    "isQrFlow": bool(args.get("generateUPIQR")), "pollingIntervalInMs": 1500, "maxTimeToPollForInMs": 20000}
        self.orders[order_id] = {**base, "status": "PLACED"}
        self._finish_order()
        return {**base, "status": "PLACED"}

    def _tool_check_payment_status(self, args):
        pay = self.payments.get(args.get("paasId"))
        if not pay:
            raise _Bad("Invalid paasId")
        pay["polls"] += 1
        done = pay["polls"] >= self.polls_to_success
        return {"paasId": args["paasId"], "status": "success" if done else "pending", "terminal": done,
                "isTerminalSuccess": done, "isTerminalFailure": False, "confirmed": pay["confirmed"], "orderId": pay["orderId"]}

    def _tool_confirm_order(self, args):
        pay = self.payments.get(args.get("paasId"))
        if not pay or pay["orderId"] != args.get("orderId"):
            raise _Bad("Invalid orderId or paasId")
        if pay["polls"] < self.polls_to_success:
            raise _Domain("Payment hasn't completed yet.")
        pay["confirmed"] = True
        self.orders[pay["orderId"]]["status"] = "PLACED"
        self._finish_order()
        return {"orderId": pay["orderId"], "paasId": args["paasId"], "orderStatus": "PLACED", "result": "success"}

    def _tool_get_orders(self, args):
        return {"orders": [{"orderId": o["orderId"], "status": o["status"], "paymentMethod": o["paymentMethod"], "totalAmount": o["cartTotal"]}
                           for o in self.orders.values()], "hasMore": False}

    def _finish_order(self):
        self._last_snapshot = json.dumps(self.cart, sort_keys=True)
        self.cart = []

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
            "selectedAddress": self.addresses[0]["id"] if self.addresses else None,
            "selectedAddressDetails": ({"id": self.addresses[0]["id"], "address": self.addresses[0]["addressLine"], "area": "Bengaluru",
                                        "name": "Demo customer", "mobile": "9XXXXXXXX0"} if self.addresses else None),
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
