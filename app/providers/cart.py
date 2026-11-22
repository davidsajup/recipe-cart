"""Build a priced cart from ANY provider's live search, reusing the catalogue matcher.

For each ingredient we search once, keep in-stock offers whose pack size we can read, and
price them with the same pack / unit / leftover maths as the built-in catalogue. The
provider's own result order is respected (it ranks by relevance); name similarity is only
a sanity filter. The customer still chooses: every line carries alternatives.
"""
from typing import Any

from pydantic import ConfigDict

from ..matcher import (
    FREE_ITEMS, _price, describe_unmatched, make_line, similarity,
)
from ..schemas import Alternative, CartItem, CartResponse, Ingredient, Line, ParsedDish, Product, UnmatchedItem
from .base import Location, Offer, Provider
from .instamart import Selection

PROVIDER_THRESHOLD = 0.55  # the provider already searched; this just drops obvious misses
MAX_ALTERNATIVES = 3
MAX_UNSIZED = 2


class OfferProduct(Product):
    """A catalogue-style Product that remembers which Offer it came from."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    offer: Any


def _alt(offer: Offer, item: CartItem | None) -> Alternative:
    return Alternative(
        label=offer.label, size_text=offer.size_text, priced=item is not None,
        packs=item.packs if item else 0, price=item.price if item else offer.price,
        used_pct=item.used_pct if item else None, left_over=item.left_over if item else None,
        offer=offer.ref(),
    )


def _line_for(ing: Ingredient, scale: float, offers: list[Offer], in_pantry: bool) -> tuple[Line, CartItem | None]:
    wanted = [o for o in offers if o.in_stock and similarity(ing.name, o.label) >= PROVIDER_THRESHOLD]
    sized = [
        OfferProduct(name=o.label, pack_size=o.pack_size, unit=o.pack_unit, price=o.price, offer=o)
        for o in wanted if o.sized
    ]
    priced: list[tuple[OfferProduct, CartItem]] = []
    for p in sized:  # provider order = relevance order
        item = _price(ing, scale, p)
        if item is not None:
            priced.append((p, item))
    unsized = [o for o in wanted if not o.sized][:MAX_UNSIZED]
    # Sized, but we can't turn the recipe's amount into this pack's unit (e.g. "2 breasts" vs
    # grams): still worth showing, so the customer can choose how much to buy.
    unconvertible = [p.offer for p in sized if all(p is not q for q, _ in priced)][:MAX_UNSIZED]

    if not priced:
        line = make_line(ing, scale, None, [], in_pantry).model_copy(update={
            "provider": offers[0].provider if offers else None,
            "alternatives": [_alt(o, None) for o in (unconvertible + unsized)[:MAX_ALTERNATIVES]],
        })
        return line, None

    best, item = priced[0]
    alts = [_alt(p.offer, it) for p, it in priced[1 : 1 + MAX_ALTERNATIVES]] + [_alt(o, None) for o in unsized]
    line = make_line(ing, scale, item, [], in_pantry).model_copy(update={
        "provider": best.offer.provider, "offer": best.offer.ref(), "alternatives": alts,
    })
    return line, item


def build_provider_cart(
    parsed: ParsedDish,
    servings: int,
    provider: Provider,
    location: Location,
    pantry: list[str] | None = None,
) -> CartResponse:
    scale = servings / parsed.base_servings
    pantry_l = [p.lower() for p in (pantry or [])]
    searched: dict[str, list[Offer]] = {}  # one search per distinct query

    lines: list[Line] = []
    items: list[CartItem] = []
    skipped: list[str] = []
    unmatched: list[str] = []
    unmatched_items: list[UnmatchedItem] = []

    for ing in parsed.ingredients:
        if ing.name.lower().strip() in FREE_ITEMS:
            continue
        query = ing.name.strip().lower()
        if query not in searched:
            searched[query] = provider.search(query, location)
        in_pantry = ing.name.lower() in pantry_l
        line, item = _line_for(ing, scale, searched[query], in_pantry)
        lines.append(line)
        if in_pantry:
            skipped.append(ing.name)
        elif item is None:
            unmatched.append(ing.name)
            unmatched_items.append(describe_unmatched(ing, scale))
        else:
            items.append(item)

    return CartResponse(
        dish=parsed.dish, items=items, skipped_pantry=skipped, unmatched=unmatched,
        unmatched_items=unmatched_items, lines=lines, servings=servings,
        total=round(sum(i.price for i in items), 2),
        provider=provider.name, provider_badge=provider.badge,
    )


def selections_from_cart(cart: CartResponse, have: set[str] | None = None) -> list[Selection]:
    """The variants to put in the provider's cart: every priced line you're buying, at the
    number of packs the maths came to. (Not yet wired to a customer confirmation screen.)"""
    have = {h.lower() for h in (have or set())}
    out: list[Selection] = []
    for line in cart.lines:
        if line.status != "priced" or line.name.lower() in have or not line.offer:
            continue
        ref = line.offer
        offer = Offer(
            provider=ref["provider"], product_id=ref["product_id"], variant_id=ref["variant_id"],
            sku_id=ref["sku_id"], name=line.product or "", brand="", size_text=ref.get("size_text", ""),
            price=round(line.price / max(line.packs, 1), 2), mrp=None, in_stock=True,
            max_quantity=ref.get("max_quantity"),
        )
        out.append(Selection(offer=offer, quantity=line.packs))
    return out
