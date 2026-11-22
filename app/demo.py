"""Demo mode: a simulated Instamart so the whole customer flow can be shown and recorded
before Swiggy approves real access. Turn on with DEMO_INSTAMART=1.

Everything here is fake: products, prices, stock, addresses, payments and orders come from
`providers/fake_instamart.py`. It is ONE shared in-memory cart for the whole server, so it is
only for a single presenter, never for real customers. No real order is ever placed.
"""
import os
import threading

from .providers.base import Location
from .providers.fake_instamart import TOKEN, FakeInstamart
from .providers.instamart import InstamartClient, InstamartProvider

LOCATION = Location(address_id="addr_1")
lock = threading.Lock()  # serialise calls that change the shared cart
_fake: FakeInstamart | None = None


def enabled() -> bool:
    return os.environ.get("DEMO_INSTAMART", "0").strip().lower() in {"1", "true", "yes", "on"}


def fake() -> FakeInstamart:
    global _fake
    if _fake is None:
        _fake = FakeInstamart()
    return _fake


def provider() -> InstamartProvider:
    return InstamartProvider(InstamartClient(TOKEN, url="http://demo.invalid/im", http=fake().client()))


def reset() -> None:
    """Forget the shared cart and orders (tests, or between recordings)."""
    global _fake
    _fake = None
