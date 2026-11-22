from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Location:
    """Where the order is going. Providers want a saved address id, or (guest mode)
    coordinates. Never guess one: it comes from the customer."""

    address_id: str | None = None
    latitude: float | None = None
    longitude: float | None = None

    @property
    def known(self) -> bool:
        return bool(self.address_id) or (self.latitude is not None and self.longitude is not None)


@dataclass(frozen=True)
class Offer:
    """One buyable variant of a product, in provider-neutral terms."""

    provider: str          # "instamart", "catalogue"
    product_id: str
    variant_id: str        # Instamart: spinId
    sku_id: str            # Instamart: skuId
    name: str
    brand: str
    size_text: str         # exactly as the provider printed it, e.g. "500 g"
    price: float           # what you pay per unit
    mrp: float | None
    in_stock: bool
    max_quantity: int | None = None
    image_url: str | None = None
    pack_size: float | None = None   # parsed from size_text; None if we couldn't read it
    pack_unit: str | None = None     # "g" | "ml" | "count"

    @property
    def label(self) -> str:
        brand = self.brand.strip()
        name = self.name.strip()
        head = name if (not brand or name.lower().startswith(brand.lower())) else f"{brand} {name}"
        return f"{head} {self.size_text}".strip()

    @property
    def sized(self) -> bool:
        return self.pack_size is not None and self.pack_unit is not None

    def ref(self) -> dict:
        """The opaque ids a cart call needs. Safe to send to the browser."""
        return {
            "provider": self.provider, "product_id": self.product_id,
            "variant_id": self.variant_id, "sku_id": self.sku_id,
            "size_text": self.size_text, "max_quantity": self.max_quantity,
        }


class Provider(Protocol):
    name: str    # machine name
    badge: str   # attribution text the UI must show with this provider's results

    def search(self, query: str, location: Location) -> list[Offer]: ...
