"""Ingredient -> product matching with unit normalisation and pack rounding.

Matching is token-based with rapidfuzz: every word of the ingredient must
appear (allowing typos/plurals) in the product name; synonyms are expanded first.
Mass/volume mismatches (e.g. oil in g vs ml) assume density 1, which is
approximate - fine for a prototype.
"""
import math
import re

from rapidfuzz import fuzz

from .schemas import CartItem, CartResponse, Ingredient, ParsedDish, Product, Suggestion
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
    "lemon": 60, "ginger": 30,
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
    suggestions: list[Suggestion] = []

    for ing in parsed.ingredients:
        if ing.name.lower().strip() in FREE_ITEMS:
            continue  # nothing to buy
        if ing.name.lower() in pantry_l:
            skipped.append(ing.name)
            continue
        item = _match_one(ing, scale, catalogue)
        if item is None:
            unmatched.append(ing.name)
            suggestions.extend(suggest(ing, scale, pantry, catalogue))
        else:
            items.append(item)

    return CartResponse(
        dish=parsed.dish,
        items=items,
        skipped_pantry=skipped,
        unmatched=unmatched,
        suggestions=suggestions,
        total=round(sum(i.price for i in items), 2),
    )


def suggest(
    ing: Ingredient, scale: float, pantry: list[str], catalogue: list[Product]
) -> list[Suggestion]:
    """Stand-ins for an unmatched ingredient: pantry items first, then priced
    catalogue products that fit the ingredient's unit."""
    key = max(SUBSTITUTES, key=lambda k: similarity(ing.name, k), default=None)
    if key is None or similarity(ing.name, key) < MATCH_THRESHOLD:
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
    return CartItem(
        ingredient=ing.name,
        needed=f"{round(amount, 1)} {base_unit}",
        product=product.name,
        packs=packs,
        price=round(packs * product.price, 2),
    )
