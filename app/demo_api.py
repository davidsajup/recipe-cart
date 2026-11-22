"""Customer ordering routes for demo mode: review the cart, place the order, poll UPI.

These routes follow Swiggy's rules even though the backend is simulated: the server re-reads
prices from the provider (never trusts the browser's), checkout needs an explicit
confirmation of the amount the customer saw, and order placement is never retried.
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import demo
from .providers.cart import offer_from_ref
from .providers.instamart import (
    AuthExpired, BadRequest, CartChanged, Confirmation, ConfirmationRequired, DomainError,
    Forbidden, InstamartProvider, RateLimited, Selection, SwiggyError, Unavailable,
)

router = APIRouter(prefix="/instamart", tags=["instamart (demo)"])

BADGE = "Powered by Instamart"


def require_demo() -> None:
    if not demo.enabled():
        raise HTTPException(404, "Not found")


class PickIn(BaseModel):
    offer: dict  # the opaque ids from a /cart line or alternative
    quantity: int = Field(ge=1, le=50)


class ReviewIn(BaseModel):
    items: list[PickIn] = Field(min_length=1, max_length=40)


class CheckoutIn(ReviewIn):
    payment_method: Literal["UPI", "Cash"]
    to_pay: str = Field(min_length=1, max_length=30)  # exactly what the customer was shown
    confirmed: bool
    address_id: str = Field(min_length=1, max_length=100)


class PaymentStatusIn(BaseModel):
    paas_id: str = Field(min_length=1, max_length=100)
    order_id: str = Field(min_length=1, max_length=100)


def to_http(e: Exception) -> HTTPException:
    if isinstance(e, HTTPException):
        return e
    table = [
        (ConfirmationRequired, 400), (BadRequest, 400), (CartChanged, 409), (DomainError, 409),
        (AuthExpired, 401), (Forbidden, 403), (RateLimited, 429), (Unavailable, 503),
    ]
    for cls, status in table:
        if isinstance(e, cls):
            return HTTPException(status, e.message)
    if isinstance(e, SwiggyError):
        return HTTPException(502, e.message)
    if isinstance(e, KeyError):
        return HTTPException(422, "That item reference isn't valid.")
    raise e


def _selections(items: list[PickIn]) -> list[Selection]:
    out = []
    for it in items:
        ref = it.offer
        if ref.get("provider") != "instamart" or not ref.get("variant_id") or not ref.get("sku_id"):
            raise HTTPException(422, "That item reference isn't valid.")
        out.append(Selection(offer=offer_from_ref(ref), quantity=it.quantity))
    return out


def _set_cart(prov: InstamartProvider, items: list[PickIn]):
    return prov.set_cart(_selections(items), demo.LOCATION)


@router.post("/review", dependencies=[Depends(require_demo)])
def review(body: ReviewIn) -> dict:
    """The cart exactly as Instamart sees it, for the customer to check before ordering."""
    try:
        with demo.lock:
            prov = demo.provider()
            result = _set_cart(prov, body.items)
            options = prov.payment_options()
    except Exception as e:
        raise to_http(e)
    raw = result.raw
    address = raw.get("selectedAddressDetails") or {}
    return {
        "demo": True,
        "badge": BADGE,
        "address": {"id": raw.get("selectedAddress") or address.get("id"), "line": address.get("address", ""),
                    "name": address.get("name", "")},
        "items": [
            {"name": i.get("itemName", ""), "variant": i.get("itemVariant", ""), "quantity": i.get("quantity", 0),
             "unit_price": i.get("discountedFinalPrice"), "mrp": i.get("mrp")}
            for i in raw.get("items") or []
        ],
        "bill_lines": [{"label": a, "value": b} for a, b in result.bill_lines],
        "to_pay": result.to_pay,
        "total": result.total,
        "payment_methods": options,
        "warnings": result.warnings,
        "removed_out_of_stock": result.removed_out_of_stock,
        "reduced": result.reduced,
    }


@router.post("/checkout", dependencies=[Depends(require_demo)])
def checkout(body: CheckoutIn) -> dict:
    """Place the order. Needs the customer's explicit yes and the amount they saw."""
    try:
        with demo.lock:
            prov = demo.provider()
            _set_cart(prov, body.items)  # same absolute state the customer reviewed (update_cart replaces the cart)
            outcome = prov.checkout(Confirmation(
                to_pay=body.to_pay, address_id=body.address_id, payment_method=body.payment_method, confirmed=body.confirmed,
            ))
    except Exception as e:
        raise to_http(e)
    return {
        "demo": True, "badge": BADGE, "status": outcome.status, "message": outcome.message,
        "order_ids": outcome.order_ids, "payment_method": outcome.payment_method, "total": outcome.total,
        "delivery_address": outcome.delivery_address, "paas_id": outcome.paas_id,
        "bridge_url": outcome.bridge_url, "poll_interval_ms": outcome.poll_interval_ms, "max_poll_ms": outcome.max_poll_ms,
    }


@router.post("/payment-status", dependencies=[Depends(require_demo)])
def payment_status(body: PaymentStatusIn) -> dict:
    try:
        with demo.lock:
            status = demo.provider().payment_status(body.paas_id, body.order_id)
    except Exception as e:
        raise to_http(e)
    return {"demo": True, "badge": BADGE, "status": status.status, "order_id": status.order_id, "message": status.message}
