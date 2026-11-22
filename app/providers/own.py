from ..schemas import Product
from .base import Location, Offer


class OwnCatalogueProvider:
    """The built-in catalogue as a provider. Returns every product and lets the matcher
    rank them, which keeps this path identical to the original `build_cart`."""

    name = "catalogue"
    badge = ""

    def __init__(self, products: list[Product]):
        self.products = products

    def search(self, query: str, location: Location) -> list[Offer]:
        return [
            Offer(
                provider=self.name, product_id=str(i), variant_id=str(i), sku_id=str(i),
                name=p.name, brand="", size_text="", price=p.price, mrp=None, in_stock=True,
                pack_size=p.pack_size, pack_unit=p.unit,
            )
            for i, p in enumerate(self.products)
        ]
