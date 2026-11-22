from app.matcher import MAX_SUGGESTIONS, build_cart
from app.schemas import ParsedDish, Product


def P(name, size, unit, price):
    return Product(name=name, pack_size=size, unit=unit, price=price)


def dish(*ings):
    return ParsedDish(
        dish="x",
        ingredients=[{"name": n, "quantity": q, "unit": u} for n, q, u in ings],
    )


BUTTER = P("Butter 100g", 100, "g", 58)
OIL = P("Vegetable Oil 1l", 1000, "ml", 160)
LEMON = P("Lemon 6 pack", 6, "count", 30)
DALS = [P("Moong Dal 500g", 500, "g", 80), P("Masoor Dal 500g", 500, "g", 70), P("Chana Dal 500g", 500, "g", 75)]


def test_unmatched_gets_priced_substitute():
    cart = build_cart(dish(("ghee", 2, "tbsp")), 4, [], [BUTTER, OIL])
    assert cart.unmatched == ["ghee"]
    first = cart.suggestions[0]
    assert (first.ingredient, first.product) == ("ghee", "Butter 100g")
    assert first.packs == 1 and first.price == 58 and not first.in_pantry
    assert [s.product for s in cart.suggestions] == ["Butter 100g", "Vegetable Oil 1l"]


def test_suggestions_do_not_change_total():
    cart = build_cart(dish(("ghee", 2, "tbsp")), 4, [], [BUTTER, OIL])
    assert cart.total == 0 and cart.items == []


def test_pantry_substitute_comes_first_and_is_free():
    cart = build_cart(dish(("ghee", 2, "tbsp")), 4, ["butter"], [BUTTER, OIL])
    first = cart.suggestions[0]
    assert first.in_pantry and first.price == 0 and first.packs == 0
    assert first.product == "butter"
    assert "Vegetable Oil 1l" in [s.product for s in cart.suggestions]


def test_no_suggestions_when_everything_matches():
    cart = build_cart(dish(("butter", 50, "g")), 4, [], [BUTTER, OIL])
    assert cart.unmatched == [] and cart.suggestions == []


def test_unknown_ingredient_has_no_suggestions():
    cart = build_cart(dish(("saffron", 1, "g")), 4, [], [BUTTER, OIL])
    assert cart.unmatched == ["saffron"] and cart.suggestions == []


def test_count_ingredient_uses_unit_compatible_substitute():
    cart = build_cart(dish(("lime", 2, "count")), 4, [], [LEMON, BUTTER])
    assert [s.product for s in cart.suggestions] == ["Lemon 6 pack"]


def test_capped_and_only_stocked_products():
    cart = build_cart(dish(("toor dal", 200, "g")), 4, [], DALS + [BUTTER])
    assert len(cart.suggestions) == MAX_SUGGESTIONS
    assert {s.product for s in cart.suggestions} == {d.name for d in DALS}
    # a substitute missing from the catalogue is simply not offered
    cart = build_cart(dish(("toor dal", 200, "g")), 4, [], DALS[:1])
    assert [s.product for s in cart.suggestions] == ["Moong Dal 500g"]


def test_suggestions_scale_with_servings():
    cart = build_cart(dish(("toor dal", 200, "g")), 8, [], DALS)  # base 4 -> 400 g, then x2
    assert cart.suggestions[0].packs == 1
    big = build_cart(dish(("toor dal", 600, "g")), 8, [], DALS)  # 1200 g -> 3 packs
    assert big.suggestions[0].packs == 3


def test_unit_mismatch_still_offers_one_pack_with_caveat():
    # curry leaves are counted; bay leaf is sold by weight -> can't convert
    bay = P("Bay Leaf 25g", 25, "g", 30)
    cart = build_cart(dish(("curry leaves", 1, "count")), 4, [], [bay])
    s = cart.suggestions[0]
    assert s.product == "Bay Leaf 25g" and s.packs == 1 and s.price == 30
    assert "quantity is a guess" in s.note
