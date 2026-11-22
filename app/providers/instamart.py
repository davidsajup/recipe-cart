"""Swiggy Instamart over MCP (JSON-RPC 2.0 on Streamable HTTP, stateless JSON mode).

Built from Swiggy's published builder docs (https://mcp.swiggy.com/builders/docs/):
  * one POST endpoint per server; Instamart is `/im`
  * every request carries its own `Authorization: Bearer <user token>`
  * results come back in `structuredContent` (not the prose in `content`)
  * errors: `{"success": false, "error": {"message", "reportId"?}}`, plus HTTP/JSON-RPC codes
  * retries: 500 ms doubling to 8 s, at most 5 attempts, 30 s budget; honour Retry-After;
    NEVER blindly retry placing an order (there is no idempotency key)

Scope today: addresses, search, and cart building. Checkout and payment are deliberately
not here yet: they need the customer's explicit confirmation screen first.

The token is the CUSTOMER's own (OAuth 2.1 + PKCE with their Swiggy phone/OTP). This module
never stores or logs it.
"""
import itertools
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx

from .base import Location, Offer
from .quantity import parse_quantity

log = logging.getLogger("recipe_cart.instamart")

# Staging first: production access is by approval. Override with SWIGGY_MCP_BASE_URL.
DEFAULT_BASE_URL = "https://mcp-staging.swiggy.com/im"
NAME = "instamart"
BADGE = "Powered by Instamart"

MAX_ATTEMPTS = 5
BACKOFF_START = 0.5
BACKOFF_CAP = 8.0
RETRY_BUDGET_SECONDS = 30.0
# Order placement can't be retried safely: after a failure, check get_orders first.
NO_AUTO_RETRY = frozenset({"checkout", "confirm_order"})


class SwiggyError(Exception):
    """Base class. `message` is safe to show to a customer."""

    def __init__(self, message: str, *, report_id: str | None = None):
        super().__init__(message)
        self.message = message
        self.report_id = report_id


class AuthExpired(SwiggyError):
    """401 / -32001: the customer's token is missing, expired or revoked. Re-run sign-in."""


class Forbidden(SwiggyError):
    """403 / -32003."""


class BadRequest(SwiggyError):
    """400 'Invalid ...' / 'Missing ...': our arguments are wrong. Do not retry."""


class DomainError(SwiggyError):
    """HTTP 200 with success:false: a terminal answer (out of stock, not serviceable, ...)."""


class RateLimited(SwiggyError):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class Unavailable(SwiggyError):
    """5xx, timeouts, dropped connections."""


class ConfirmationRequired(SwiggyError):
    """checkout was attempted without the customer's explicit yes. Never place an order silently."""


class CartChanged(SwiggyError):
    """The cart no longer matches what the customer reviewed (price, stock or address changed)."""


def base_url() -> str:
    return os.environ.get("SWIGGY_MCP_BASE_URL", DEFAULT_BASE_URL)


class InstamartClient:
    def __init__(
        self,
        token: str,
        *,
        url: str | None = None,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        if not token:
            raise AuthExpired("Not signed in to Swiggy.")
        self._token = token
        self.url = url or base_url()
        self._http = http or httpx.Client(timeout=15.0)
        self._sleep, self._clock = sleep, clock
        self._ids = itertools.count(1)

    # ---- transport -------------------------------------------------------
    def call(self, tool: str, arguments: dict | None = None, *, retry: bool | None = None) -> dict:
        """Call one tool and return its data. Raises a SwiggyError subclass on failure."""
        allow_retry = (tool not in NO_AUTO_RETRY) if retry is None else retry
        started, delay, attempt = self._clock(), BACKOFF_START, 0
        while True:
            attempt += 1
            try:
                return self._once(tool, arguments or {})
            except RateLimited as e:
                wait = e.retry_after if e.retry_after is not None else delay
                err: SwiggyError = e
            except Unavailable as e:
                wait, err = delay, e
            out_of_budget = (self._clock() - started) + wait > RETRY_BUDGET_SECONDS
            if not allow_retry or attempt >= MAX_ATTEMPTS or out_of_budget:
                raise err
            self._sleep(wait)
            delay = min(delay * 2, BACKOFF_CAP)

    def _once(self, tool: str, arguments: dict) -> dict:
        request_id = next(self._ids)
        body = {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": tool, "arguments": arguments}, "id": request_id}
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        t0 = self._clock()
        try:
            resp = self._http.post(self.url, json=body, headers=headers)
        except httpx.HTTPError as e:  # timeouts, DNS, dropped connections
            log.warning("swiggy tool=%s id=%s transport_error=%s", tool, request_id, e.__class__.__name__)
            raise Unavailable("Couldn't reach Instamart. Try again in a moment.") from e
        log.info("swiggy tool=%s id=%s status=%s ms=%d", tool, request_id, resp.status_code, (self._clock() - t0) * 1000)
        return self._parse(resp)

    # ---- response handling ----------------------------------------------
    def _parse(self, resp: httpx.Response) -> dict:
        status = resp.status_code
        payload = _json(resp)
        if status == 401:
            raise AuthExpired("Your Swiggy session has expired. Please sign in again.")
        if status == 403:
            raise Forbidden("Swiggy refused this request.")
        if status == 429:
            raise RateLimited("Instamart is busy. Trying again shortly.", _retry_after(resp))
        if status == 400:
            raise BadRequest(_error_message(payload) or "Instamart rejected the request.")
        if status >= 500:
            raise Unavailable("Instamart is having trouble. Try again in a moment.")
        if status >= 400:
            raise SwiggyError(_error_message(payload) or f"Unexpected response ({status}).")

        if isinstance(payload, dict) and "error" in payload and "result" not in payload:  # JSON-RPC error
            code = (payload["error"] or {}).get("code")
            msg = (payload["error"] or {}).get("message") or "Instamart error."
            if code == -32001:
                raise AuthExpired("Your Swiggy session has expired. Please sign in again.")
            if code == -32003:
                raise Forbidden(msg)
            if code == -32000:
                raise RateLimited("Instamart is busy. Trying again shortly.", _retry_after(resp))
            if code == -32603:
                raise Unavailable("Instamart is having trouble. Try again in a moment.")
            raise SwiggyError(msg)

        result = (payload or {}).get("result") or {}
        return _unwrap(result)


def _json(resp: httpx.Response):
    try:
        return resp.json()
    except ValueError:
        return None


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        return max(0.0, float(resp.headers.get("Retry-After", "")))
    except ValueError:
        return None


def _error_message(payload) -> str | None:
    if not isinstance(payload, dict):
        return None
    err = payload.get("error")
    if isinstance(err, dict):
        return err.get("message")
    sc = (payload.get("result") or {}).get("structuredContent") or {}
    return (sc.get("error") or {}).get("message") if isinstance(sc.get("error"), dict) else None


def _unwrap(result: dict) -> dict:
    """Read `structuredContent` (never the prose `content`), falling back to the JSON text block."""
    sc = result.get("structuredContent")
    if not sc:
        for block in result.get("content") or []:
            if block.get("type") == "text":
                try:
                    sc = json.loads(block.get("text", ""))
                except ValueError:
                    sc = None
                break
    if not isinstance(sc, dict):
        if result.get("isError"):
            raise DomainError("Instamart couldn't complete that.")
        return {}
    if sc.get("success") is False or (result.get("isError") and "error" in sc):
        err = sc.get("error") or {}
        raise DomainError(err.get("message") or sc.get("message") or "Instamart couldn't complete that.", report_id=err.get("reportId"))
    if "data" in sc and set(sc) <= {"success", "data", "message"}:  # {success, data, message} envelope
        return sc["data"] or {}
    return sc


# ---- typed helpers --------------------------------------------------------

def _where(location: Location) -> dict:
    if location.address_id:
        return {"addressId": location.address_id}
    if location.latitude is not None and location.longitude is not None:
        return {"latitude": location.latitude, "longitude": location.longitude}
    return {}


@dataclass(frozen=True)
class Selection:
    """A variant the customer chose, and how many of it."""

    offer: Offer
    quantity: int


@dataclass
class CartResult:
    total: str                      # as printed by Swiggy, e.g. "173"
    bill_lines: list[tuple[str, str]]
    to_pay: str | None
    removed_out_of_stock: list[str] = field(default_factory=list)
    reduced: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unserviceable: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Confirmation:
    """What the customer explicitly agreed to. Swiggy's rule: always get a clear yes to the
    items, the total, the delivery address and the payment method before `checkout`."""

    to_pay: str          # the amount they were shown
    address_id: str
    payment_method: str  # "UPI" | "Cash" | "SwiggyPay"
    confirmed: bool      # they said yes


@dataclass
class OrderOutcome:
    status: str                      # "placed" | "pending_payment" | "partial"
    order_ids: list[str]
    payment_method: str
    message: str
    total: str | None = None
    delivery_address: str | None = None
    paas_id: str | None = None       # UPI only: poll check_payment_status with this
    bridge_url: str | None = None    # UPI only: opaque HTTPS page with the scan / open-app UI
    upi_intent_url: str | None = None
    poll_interval_ms: int | None = None
    max_poll_ms: int | None = None
    failed: int = 0


@dataclass
class PaymentStatus:
    status: str                      # "pending" | "success" | "failed"
    order_id: str | None = None
    message: str = ""


PAYMENT_METHODS = ("UPI", "Cash", "SwiggyPay")


class InstamartProvider:
    name = NAME
    badge = BADGE

    def __init__(self, client: InstamartClient):
        self.client = client

    def addresses(self, *, page: int = 1) -> dict:
        return self.client.call("get_addresses", {"page": page, "pageSize": 10})

    def search(self, query: str, location: Location, *, offset: int = 0) -> list[Offer]:
        args = {"query": query.strip(), **_where(location)}
        if offset:
            args["offset"] = offset
        return offers_from(self.client.call("search_products", args))

    def set_cart(self, selections: list[Selection], location: Location) -> CartResult:
        """Replaces the WHOLE cart (that's how update_cart works). Idempotent, so retryable."""
        items = [
            {"spinId": s.offer.variant_id, "skuId": s.offer.sku_id, "quantity": int(s.quantity)}
            for s in selections if s.quantity > 0
        ]
        args: dict = {"items": items}
        if location.address_id:
            args["selectedAddressId"] = location.address_id
        elif location.known:
            args.update(latitude=location.latitude, longitude=location.longitude)
        return cart_result(self.client.call("update_cart", args))

    def get_cart(self) -> CartResult:
        return cart_result(self.client.call("get_cart", {}))

    def clear_cart(self) -> None:
        self.client.call("clear_cart", {})

    # ---- ordering (guarded) ----------------------------------------------
    def payment_options(self) -> list[dict]:
        """Only offer what Swiggy returns. Never ask for a UPI ID."""
        data = self.client.call("get_payment_options", {})
        seen, out = set(), []
        for m in data.get("allMethods") or []:
            group = m.get("groupName") or m.get("displayName")
            if group in PAYMENT_METHODS and group not in seen:
                seen.add(group)
                out.append({"id": group, "label": m.get("displayName") or group})
        return out

    def checkout(self, confirmation: Confirmation, *, confirm_duplicate: bool = False) -> OrderOutcome:
        """Place the order. Refuses unless the customer confirmed what is in the cart NOW.
        Never retried automatically: after a failure, look at the orders list first."""
        if not confirmation.confirmed:
            raise ConfirmationRequired("Please confirm your order before we place it.")
        if confirmation.payment_method not in PAYMENT_METHODS:
            raise BadRequest("Choose a payment method.")
        cart = self.get_cart()
        if cart.raw.get("cartAbsent"):
            raise CartChanged("Your cart is empty. Please review it again.")
        if cart.unserviceable:
            raise CartChanged("Some items can't be delivered to this address. Please review your cart again.")
        if not _same_amount(cart.to_pay, confirmation.to_pay):
            raise CartChanged("The price changed since you reviewed your cart. Please review it again.")
        args: dict = {"addressId": confirmation.address_id, "paymentMethod": confirmation.payment_method}
        if confirm_duplicate:
            args["confirmDuplicate"] = True
        return outcome_from(self.client.call("checkout", args, retry=False))

    def payment_status(self, paas_id: str, order_id: str) -> PaymentStatus:
        """One UPI poll. When Swiggy reports success but hasn't confirmed the order, confirm it
        exactly once (the docs: confirm_order is idempotent for the same ids)."""
        data = self.client.call("check_payment_status", {"paasId": paas_id, "orderId": order_id})
        if data.get("isTerminalFailure") or data.get("status") in ("failed", "cancelled", "refund-initiated", "cart_changed"):
            return PaymentStatus("failed", order_id, "The payment didn't go through. No order was placed.")
        if not data.get("isTerminalSuccess"):
            return PaymentStatus("pending", order_id, "Waiting for your payment...")
        if data.get("confirmed"):
            return PaymentStatus("success", order_id, "Instamart order placed successfully.")
        try:
            res = self.client.call("confirm_order", {"orderId": order_id, "paasId": paas_id}, retry=False)
        except Unavailable:
            return PaymentStatus("pending", order_id, "Confirming your order...")
        result = res.get("result")
        if result == "success":
            return PaymentStatus("success", order_id, "Instamart order placed successfully.")
        if result == "failed":
            return PaymentStatus("failed", order_id, "The order couldn't be completed.")
        return PaymentStatus("pending", order_id, "Confirming your order...")


# ---- shape conversion -----------------------------------------------------

def _same_amount(a: str | None, b: str | None) -> bool:
    """'113' == '113.00' == 'Rs 113'. Falls back to exact text if neither reads as a number."""
    def num(x):
        digits = "".join(ch for ch in str(x or "") if ch.isdigit() or ch == ".")
        try:
            return float(digits)
        except ValueError:
            return None

    na, nb = num(a), num(b)
    return (na is not None and nb is not None and abs(na - nb) < 0.005) or (na is None and nb is None and a == b)


def outcome_from(data: dict) -> OrderOutcome:
    """checkout data has three shapes: one order, several (one per store), or UPI pending."""
    if "orders" in data:  # multi-store
        ok = [o["orderId"] for o in data["orders"] if o.get("orderId") and not o.get("error")]
        failed = len(data["orders"]) - len(ok)
        return OrderOutcome(
            status="placed" if data.get("allSucceeded") else "partial", order_ids=ok,
            payment_method=data.get("paymentMethod", ""), total=data.get("cartTotal"), failed=failed,
            message="Instamart order placed successfully." if not failed else f"{len(ok)} of {len(data['orders'])} orders were placed.",
        )
    base = dict(
        order_ids=[data["orderId"]] if data.get("orderId") else [], payment_method=data.get("paymentMethod", ""),
        total=data.get("cartTotal"), delivery_address=data.get("deliveryAddress"),
    )
    if data.get("status") == "PENDING_PAYMENT":
        return OrderOutcome(
            status="pending_payment", message="Waiting for your payment...", paas_id=data.get("paasId"),
            bridge_url=data.get("bridgeUrl"), upi_intent_url=data.get("upiIntentUrl"),
            poll_interval_ms=data.get("pollingIntervalInMs"), max_poll_ms=data.get("maxTimeToPollForInMs"), **base,
        )
    return OrderOutcome(status="placed", message="Instamart order placed successfully.", **base)


def _num(value) -> float | None:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def offers_from(data: dict) -> list[Offer]:
    """search_products data -> Offers (one per variation), in the provider's own order."""
    out: list[Offer] = []
    for p in data.get("products") or []:
        for v in p.get("variations") or []:
            price = v.get("price") or {}
            selling = _num(price.get("offerPrice"))
            mrp = _num(price.get("mrp"))
            if selling is None:
                selling = mrp
            if selling is None or not v.get("spinId") or not v.get("skuId"):
                continue  # can't price it or add it to a cart
            stock = v.get("isInStockAndAvailable")
            if stock is None:
                stock = bool(p.get("inStock", True)) and bool(p.get("isAvail", True))
            size_text = (v.get("quantityDescription") or "").strip()
            parsed = parse_quantity(size_text)
            mq = v.get("maxQuantity")
            out.append(Offer(
                provider=NAME,
                product_id=str(p.get("productId") or ""),
                variant_id=str(v["spinId"]),
                sku_id=str(v["skuId"]),
                name=(v.get("displayName") or p.get("displayName") or "").strip(),
                brand=(v.get("brandName") or p.get("brand") or "").strip(),
                size_text=size_text,
                price=selling,
                mrp=mrp,
                in_stock=bool(stock),
                max_quantity=int(mq) if isinstance(mq, (int, float)) else None,
                image_url=v.get("imageUrl"),
                pack_size=parsed[0] if parsed else None,
                pack_unit=parsed[1] if parsed else None,
            ))
    return out


def cart_result(data: dict) -> CartResult:
    bill = data.get("billBreakdown") or {}
    lines = [(str(li.get("label", "")), str(li.get("value", ""))) for li in bill.get("lineItems") or []]
    to_pay = (bill.get("toPay") or {}).get("value")
    warnings = [data[k] for k in ("addressWarning",) if data.get(k)]
    if (data.get("cartWarning") or {}).get("message"):
        warnings.append(data["cartWarning"]["message"])
    return CartResult(
        total=str(data.get("cartTotalAmount", "")),
        bill_lines=lines,
        to_pay=str(to_pay) if to_pay is not None else None,
        removed_out_of_stock=[i.get("itemName", "") for i in data.get("removedOutOfStockItems") or []],
        reduced=list(data.get("reducedQuantityItems") or []),
        warnings=warnings,
        unserviceable=[str(i.get("itemName", i)) if isinstance(i, dict) else str(i) for i in data.get("unserviceableItems") or []],
        raw=data,
    )
