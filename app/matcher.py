"""Ingredient -> product matching with unit normalisation and pack rounding.

Matching is token-based with rapidfuzz: every word of the ingredient must
appear (allowing typos/plurals) in the product name; synonyms are expanded first.
Mass/volume mismatches (e.g. oil in g vs ml) assume density 1, which is
approximate - fine for a prototype.
"""
import math
import re

from rapidfuzz import fuzz

from .schemas import CartItem, CartResponse, Ingredient, Line, ParsedDish, Product, Suggestion, UnmatchedItem
from .substitutions import SUBSTITUTES

MAX_SUGGESTIONS = 3

TO_BASE = {
    "g": ("g", 1),
    "kg": ("g", 1000),
    "ml": ("ml", 1),
    "l": ("ml", 1000),
    "tsp": ("ml", 5),
    "tbsp": ("ml", 15),
    "cup": ("ml", 240),
    "count": ("count", 1),
}

MATCH_THRESHOLD = 0.8
TOKEN_FUZZ = 80  # min rapidfuzz ratio for two tokens to count as the same word

# Variant spellings / regional names -> catalogue wording. Applied per phrase.
SYNONYMS = {
    "chilly": "chilli", "chili": "chilli", "chiles": "chilli",
    "capsicum": "bell pepper", "jeera": "cumin", "haldi": "turmeric",
    "brinjal": "eggplant", "aubergine": "eggplant", "scallion": "spring onion",
    "cilantro": "coriander leaves", "shrimp": "prawns", "shrimps": "prawns",
    "dhal": "dal", "daal": "dal", "ground black pepper": "black pepper",
    "kosher salt": "salt", "sea salt": "salt",
    "yellow lentils": "toor dal", "yellow lentil": "toor dal", "arhar": "toor dal",
    "pigeon peas": "toor dal", "split pigeon peas": "toor dal",
    "yoghurt": "curd", "yogurt": "curd", "dahi": "curd",
    "atta": "wheat flour", "whole wheat flour": "wheat flour",
    "clarified butter": "ghee", "cottage cheese": "paneer",
    "coriander seeds": "coriander powder", "dhania": "coriander",
    "ground cumin": "cumin", "cumin powder": "cumin",
}

# Things you don't buy from a shop.
FREE_ITEMS = {"water", "ice", "hot water", "warm water", "cold water", "ice water"}

# Rough grams per piece, so "2 onions" can be bought from a product sold by weight.
PIECE_WEIGHT_G = {
    "onion": 150, "tomato": 120, "potato": 150, "carrot": 70,
    "green chilli": 5, "garlic": 5,  # garlic = one clove
    "lemon": 60, "ginger": 30, "chicken breast": 200,
}

_NOISE = re.compile(r"\b\d+(\.\d+)?\s*(g|kg|ml|l|pack|pcs|x)?\b")


def normalize(quantity: float, unit: str) -> tuple[float, str]:
    base_unit, factor = TO_BASE[unit]
    return quantity * factor, base_unit


def _singular(tok: str) -> str:
    if tok == "leaves":
        return "leaf"
    if tok.endswith("ies") and len(tok) > 4:
        return tok[:-3] + "y"
    if tok.endswith("oes"):
        return tok[:-2]
    if tok.endswith("s") and not tok.endswith("ss") and len(tok) > 3:
        return tok[:-1]
    return tok


def tokens(text: str) -> list[str]:
    """Lowercase, apply synonyms, drop sizes/pack counts and filler, singularise."""
    t = text.lower().strip()
    for src, dst in SYNONYMS.items():
        t = re.sub(rf"\b{re.escape(src)}\b", dst, t)
    t = _NOISE.sub(" ", t)
    words = re.findall(r"[a-z]+", t)
    return [_singular(w) for w in words if w not in {"pack", "bunch", "fresh", "of"}]


def similarity(a: str, b: str) -> float:
    """0..1. Every word of `a` must be found (fuzzily) in `b`; extra words in
    `b` cost a little, so 'oil' prefers 'Vegetable Oil' over 'Coconut Oil' ties
    only by catalogue order, while 'coconut milk' never matches plain 'milk'."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    credits = []
    for w in ta:
        best = max(fuzz.ratio(w, x) for x in tb)
        credits.append(best / 100 if best >= TOKEN_FUZZ else 0.0)
    coverage = sum(credits) / len(ta)
    extra_penalty = 0.7 + 0.3 * (len(ta) / max(len(tb), len(ta)))
    return coverage * extra_penalty


def candidates(name: str, catalogue: list[Product]) -> list[Product]:
    """Products scoring >= MATCH_THRESHOLD, best first (catalogue order breaks ties)."""
    scored = [(similarity(name, p.name), p) for p in catalogue]
    scored = [sp for sp in scored if sp[0] >= MATCH_THRESHOLD]
    scored.sort(key=lambda sp: -sp[0])  # stable sort keeps catalogue order on ties
    return [p for _, p in scored]


def find_product(name: str, catalogue: list[Product]) -> Product | None:
    found = candidates(name, catalogue)
    return found[0] if found else None


def piece_weight(name: str) -> float | None:
    """Approx grams for one piece of `name`, or None if unknown."""
    toks = set(tokens(name))
    for key, grams in PIECE_WEIGHT_G.items():
        if set(key.split()) <= toks:
            return grams
    return None


def packs_needed(amount: float, pack_size: float) -> int:
    return max(1, math.ceil(amount / pack_size - 1e-9))


def build_cart(
    parsed: ParsedDish,
    servings: int,
    pantry: list[str],
    catalogue: list[Product],
) -> CartResponse:
    scale = servings / parsed.base_servings
    pantry_l = [p.lower() for p in pantry]
    items: list[CartItem] = []
    skipped: list[str] = []
    unmatched: list[str] = []
    unmatched_items: list[UnmatchedItem] = []
    suggestions: list[Suggestion] = []

    lines: list[Line] = []

    for ing in parsed.ingredients:
        if ing.name.lower().strip() in FREE_ITEMS:
            continue  # nothing to buy
        in_pantry = ing.name.lower() in pantry_l
        item = _match_one(ing, scale, catalogue)
        swaps = [] if item is not None else suggest(ing, scale, pantry, catalogue)
        lines.append(make_line(ing, scale, item, swaps, in_pantry))
        if in_pantry:
            skipped.append(ing.name)
            continue
        if item is None:
            unmatched.append(ing.name)
            unmatched_items.append(describe_unmatched(ing, scale))
            suggestions.extend(swaps)
        else:
            items.append(item)

    return CartResponse(
        dish=parsed.dish,
        items=items,
        skipped_pantry=skipped,
        unmatched=unmatched,
        unmatched_items=unmatched_items,
        suggestions=suggestions,
        lines=lines,
        servings=servings,
        total=round(sum(i.price for i in items), 2),
    )


def fmt_amount(value: float) -> str:
    """500.0 -> '500', 2.5 -> '2.5'."""
    value = round(value, 1)
    return str(int(value)) if value == int(value) else str(value)


def usage(amount: float, packs: int, pack_size: float, unit: str) -> tuple[int, str | None]:
    """(percent of the bought packs the recipe uses, what's left over)."""
    total = packs * pack_size
    pct = min(100, max(1, round(amount / total * 100))) if amount > 0 else 0
    left = total - amount
    if left < 0.5:
        return pct, None
    return pct, (fmt_amount(left) if unit == "count" else f"{fmt_amount(left)} {unit}")


def make_line(ing: Ingredient, scale: float, item: CartItem | None, swaps: list[Suggestion], in_pantry: bool) -> Line:
    amount, unit = normalize(ing.quantity * scale, ing.unit)
    common = dict(
        name=ing.name,
        need=f"{fmt_amount(amount)} {unit}",
        need_amount=round(amount, 2),
        need_unit=unit,
        estimated=ing.estimated,
        in_pantry=in_pantry,
    )
    if item is None:
        return Line(**common, status="open", suggested_pack_size=suggest_pack_size(amount, unit), swaps=swaps)
    return Line(
        **common, status="priced", product=item.product, packs=item.packs, price=item.price,
        used_pct=item.used_pct, left_over=item.left_over,
    )


PACK_SIZES = [50, 100, 200, 250, 500, 1000, 2000, 5000]  # typical shelf sizes for g / ml
COUNT_PACKS = [1, 6, 12]


def suggest_pack_size(amount: float, unit: str) -> float:
    """A realistic pack size to offer when adding a product: the smallest usual size
    that covers what the recipe needs (the user can change it)."""
    sizes = COUNT_PACKS if unit == "count" else PACK_SIZES
    return next((s for s in sizes if s >= amount - 1e-9), sizes[-1])


def describe_unmatched(ing: Ingredient, scale: float) -> UnmatchedItem:
    amount, unit = normalize(ing.quantity * scale, ing.unit)
    return UnmatchedItem(
        name=ing.name,
        needed=f"{round(amount, 1)} {unit}",
        unit=unit,
        suggested_pack_size=suggest_pack_size(amount, unit),
    )


def suggest(
    ing: Ingredient, scale: float, pantry: list[str], catalogue: list[Product]
) -> list[Suggestion]:
    """Stand-ins for an unmatched ingredient: pantry items first, then priced
    catalogue products that fit the ingredient's unit."""
    # Match both ways, so "heavy cream" finds the "cream" rule and "cream" finds "coconut cream".
    def score(k: str) -> float:
        return max(similarity(ing.name, k), similarity(k, ing.name))

    key = max(SUBSTITUTES, key=score, default=None)
    if key is None or score(key) < MATCH_THRESHOLD:
        return []
    have, buy = [], []
    for sub_name, note in SUBSTITUTES[key]:
        if any(similarity(sub_name, p) >= MATCH_THRESHOLD for p in pantry):
            have.append(Suggestion(ingredient=ing.name, product=sub_name, note=note, in_pantry=True))
            continue
        options = candidates(sub_name, catalogue)
        for product in options:
            priced = _price(ing, scale, product)
            if priced is not None:
                buy.append(Suggestion(
                    ingredient=ing.name, product=priced.product, note=note,
                    packs=priced.packs, price=priced.price,
                    used_pct=priced.used_pct, left_over=priced.left_over,
                ))
                break
        else:
            if options:  # stocked, but units can't be converted: offer one pack
                buy.append(Suggestion(
                    ingredient=ing.name, product=options[0].name,
                    note=f"{note} (quantity is a guess)",
                    packs=1, price=options[0].price,
                ))
    return (have + buy)[:MAX_SUGGESTIONS]


def _match_one(ing: Ingredient, scale: float, catalogue: list[Product]) -> CartItem | None:
    # Best-scoring products first; take the first one whose unit is usable, so
    # "coriander" in tsp falls through from the bunch to the powder.
    for product in candidates(ing.name, catalogue):
        item = _price(ing, scale, product)
        if item is not None:
            return item
    return None


def _price(ing: Ingredient, scale: float, product: Product) -> CartItem | None:
    amount, base_unit = normalize(ing.quantity * scale, ing.unit)
    if base_unit == "count" and product.unit == "g":
        # "2 onions" bought by weight: use a rough per-piece weight if we have one
        grams = piece_weight(ing.name)
        if grams is None:
            return None
        amount, base_unit = amount * grams, "g"
    elif (base_unit == "count") != (product.unit == "count"):
        return None
    packs = packs_needed(amount, product.pack_size)
    used_pct, left_over = usage(amount, packs, product.pack_size, product.unit)
    return CartItem(
        ingredient=ing.name,
        needed=f"{round(amount, 1)} {base_unit}",
        product=product.name,
        packs=packs,
        price=round(packs * product.price, 2),
        used_pct=used_pct,
        left_over=left_over,
    )
