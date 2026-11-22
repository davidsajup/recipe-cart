import json
from pathlib import Path

import pytest

from app.matcher import build_cart, find_product, normalize, packs_needed
from app.parser import parse_dish
from app.schemas import ParsedDish, Product

SEED = Path(__file__).resolve().parent.parent / "seed" / "products.json"
CATALOGUE = [Product(**p) for p in json.loads(SEED.read_text())]


def test_normalize_units():
    assert normalize(2, "kg") == (2000, "g")
    assert normalize(3, "tbsp") == (45, "ml")


def test_pack_rounding():
    assert packs_needed(401, 400) == 2
    assert packs_needed(400, 400) == 1


def test_servings_scale_up():
    cart = build_cart(parse_dish("fish molee"), 8, [], CATALOGUE)
    milk = next(i for i in cart.items if i.ingredient == "coconut milk")
    assert milk.packs == 2  # 800 ml needed


def test_pantry_items_skipped():
    cart = build_cart(parse_dish("fish molee"), 4, ["salt", "coconut oil"], CATALOGUE)
    assert set(cart.skipped_pantry) == {"salt", "coconut oil"}
    assert all(i.ingredient not in ("salt", "coconut oil") for i in cart.items)


def test_unmatched_reported_not_fatal():
    dish = ParsedDish(
        dish="x",
        ingredients=[
            {"name": "saffron", "quantity": 1, "unit": "g"},
            {"name": "onion", "quantity": 1, "unit": "count"},
        ],
    )
    cart = build_cart(dish, 4, [], CATALOGUE)
    assert cart.unmatched == ["saffron"]
    assert [i.ingredient for i in cart.items] == ["onion"]


def test_fish_molee_fully_matched():
    cart = build_cart(parse_dish("fish molee"), 4, [], CATALOGUE)
    assert cart.unmatched == []


def test_count_converted_to_weight():
    # 2 onions ~ 300 g -> one 1 kg pack
    cart = build_cart(parse_dish("fish molee"), 4, [], CATALOGUE)
    onion = next(i for i in cart.items if i.ingredient == "onion")
    assert onion.needed == "300.0 g" and onion.packs == 1


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Onions", "Onion 1kg"),  # plural
        ("tomatoe", "Tomato 500g"),  # typo
        ("capsicum", "Bell Pepper 1 piece"),
        ("yellow lentils", "Toor Dal 500g"),
        ("ghee", "Ghee 500ml"),
        ("yogurt", "Curd 400g"),
        ("jeera", "Cumin Seeds 100g"),  # regional name
        ("shrimp", "Prawns 500g"),  # synonym
        ("coconut milk", "Coconut Milk 400ml"),
        ("milk", "Milk 1l"),  # plain milk must not pick coconut milk
        ("coriander leaves", "Coriander Leaves bunch"),
        ("coriander powder", "Coriander Powder 100g"),
        ("saffron", None),
    ],
)
def test_find_product(name, expected):
    product = find_product(name, CATALOGUE)
    assert (product.name if product else None) == expected


def test_water_is_free_not_unmatched():
    dish = ParsedDish(
        dish="x", ingredients=[{"name": "water", "quantity": 2, "unit": "cup"}]
    )
    cart = build_cart(dish, 4, [], CATALOGUE)
    assert cart.items == [] and cart.unmatched == []


def test_falls_back_to_unit_compatible_product():
    # "coriander" in tsp can't use the bunch (count); it should take the powder.
    dish = ParsedDish(
        dish="x", ingredients=[{"name": "coriander", "quantity": 1, "unit": "tsp"}]
    )
    cart = build_cart(dish, 4, [], CATALOGUE)
    assert [i.product for i in cart.items] == ["Coriander Powder 100g"]
    # ...but "2 coriander" (count) still picks the fresh bunch.
    dish2 = ParsedDish(
        dish="x", ingredients=[{"name": "coriander", "quantity": 2, "unit": "count"}]
    )
    assert build_cart(dish2, 4, [], CATALOGUE).items[0].product == "Coriander Leaves bunch"


@pytest.mark.parametrize(
    "amount,unit,expected",
    [
        (5, "ml", 50),  # tiny spice amounts still come in a real pack
        (50, "g", 50),
        (51, "g", 100),
        (240, "ml", 250),
        (600, "g", 1000),
        (99999, "g", 5000),  # bigger than any listed size: largest shelf size
        (1, "count", 1),
        (2, "count", 6),
        (8, "count", 12),
        (40, "count", 12),
    ],
)
def test_suggest_pack_size(amount, unit, expected):
    from app.matcher import suggest_pack_size

    assert suggest_pack_size(amount, unit) == expected


def test_unmatched_items_say_what_is_needed_and_scale_with_servings():
    dish = ParsedDish(
        dish="x",
        base_servings=2,
        ingredients=[
            {"name": "saffron", "quantity": 1, "unit": "tsp"},
            {"name": "truffle", "quantity": 300, "unit": "g"},
            {"name": "starfruit", "quantity": 2, "unit": "count"},
        ],
    )
    cart = build_cart(dish, 4, [], CATALOGUE)  # 2x
    got = {u.name: u for u in cart.unmatched_items}
    assert cart.unmatched == ["saffron", "truffle", "starfruit"]
    assert (got["saffron"].needed, got["saffron"].unit, got["saffron"].suggested_pack_size) == ("10.0 ml", "ml", 50)
    assert (got["truffle"].needed, got["truffle"].suggested_pack_size) == ("600.0 g", 1000)
    assert (got["starfruit"].unit, got["starfruit"].suggested_pack_size) == ("count", 6)


def test_matched_ingredients_have_no_unmatched_item():
    cart = build_cart(parse_dish("fish molee"), 4, [], CATALOGUE)
    assert cart.unmatched == [] and cart.unmatched_items == []
