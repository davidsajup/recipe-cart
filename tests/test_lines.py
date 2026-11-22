import pytest

import app.main as main
from app.matcher import build_cart, fmt_amount, usage
from app.parser import SYSTEM_TEXT, SYSTEM_VIDEO
from app.schemas import Ingredient, ParsedDish, Product


def P(name, size, unit, price):
    return Product(name=name, pack_size=size, unit=unit, price=price)


CAT = [
    P("Garlic 250g", 250, "g", 55),
    P("Butter 100g", 100, "g", 58),
    P("Eggs 6 pack", 6, "count", 54),
    P("Salt 1kg", 1000, "g", 25),
    P("Milk 1l", 1000, "ml", 60),
    P("Onion 1kg", 1000, "g", 45),
]


def dish(*ings, base=2):
    return ParsedDish(dish="t", base_servings=base, ingredients=list(ings))


def ing(name, q, unit, **kw):
    return Ingredient(name=name, quantity=q, unit=unit, **kw)


# --- helpers ---------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [(500.0, "500"), (2.5, "2.5"), (0.04, "0"), (12.34, "12.3")])
def test_fmt_amount(value, expected):
    assert fmt_amount(value) == expected


@pytest.mark.parametrize(
    "amount,packs,size,unit,expected",
    [
        (40, 1, 250, "g", (16, "210 g")),
        (1000, 2, 500, "g", (100, None)),  # exactly fills the packs: nothing left over
        (4, 1, 6, "count", (67, "2")),  # counts have no unit word
        (10, 1, 1000, "g", (1, "990 g")),
        (0.2, 1, 1000, "g", (1, "999.8 g")),  # a sliver still shows as 1%, never 0
        (600, 1, 1000, "ml", (60, "400 ml")),
    ],
)
def test_usage(amount, packs, size, unit, expected):
    assert usage(amount, packs, size, unit) == expected


# --- lines -----------------------------------------------------------------

def test_every_ingredient_becomes_a_line_in_recipe_order():
    cart = build_cart(
        dish(ing("penne", 250, "g"), ing("garlic", 20, "g"), ing("butter", 30, "ml"), ing("cream", 60, "ml")),
        2, [], CAT,
    )
    assert [l.name for l in cart.lines] == ["penne", "garlic", "butter", "cream"]
    assert [l.status for l in cart.lines] == ["open", "priced", "priced", "open"]


def test_priced_line_carries_product_price_and_pack_usage():
    garlic = build_cart(dish(ing("garlic", 20, "g")), 4, [], CAT).lines[0]  # 2x -> 40 g
    assert (garlic.need, garlic.need_amount, garlic.need_unit) == ("40 g", 40, "g")
    assert (garlic.product, garlic.packs, garlic.price) == ("Garlic 250g", 1, 55)
    assert (garlic.used_pct, garlic.left_over) == (16, "210 g")


def test_open_line_has_prefill_for_adding_to_the_catalogue():
    penne = build_cart(dish(ing("penne", 250, "g")), 4, [], CAT).lines[0]  # 500 g
    assert penne.status == "open" and penne.product is None and penne.price == 0
    assert penne.suggested_pack_size == 500 and penne.need == "500 g"


def test_count_ingredient_keeps_its_own_need_but_uses_the_weight_for_usage():
    onion = build_cart(dish(ing("onion", 2, "count")), 2, [], CAT).lines[0]  # 2 onions ~ 300 g
    assert onion.need == "2 count" and onion.need_unit == "count"
    assert onion.product == "Onion 1kg" and onion.used_pct == 30 and onion.left_over == "700 g"


def test_estimated_flag_is_passed_through():
    cart = build_cart(dish(ing("garlic", 20, "g", estimated=True), ing("butter", 30, "ml")), 2, [], CAT)
    assert [l.estimated for l in cart.lines] == [True, False]


def test_pantry_ingredients_stay_in_lines_but_leave_the_legacy_total():
    cart = build_cart(dish(ing("garlic", 20, "g"), ing("salt", 5, "ml")), 2, ["salt"], CAT)
    salt = next(l for l in cart.lines if l.name == "salt")
    assert salt.in_pantry and salt.status == "priced" and salt.price == 25  # still priced, for the strike-through
    assert cart.skipped_pantry == ["salt"]
    assert cart.total == 55  # legacy total excludes it, as before
    assert [i.ingredient for i in cart.items] == ["garlic"]


def test_free_items_are_not_lines():
    cart = build_cart(dish(ing("water", 2, "cup"), ing("garlic", 20, "g")), 2, [], CAT)
    assert [l.name for l in cart.lines] == ["garlic"]


def test_swaps_ride_on_the_open_line_with_usage():
    cart = build_cart(dish(ing("cream", 120, "ml")), 2, [], CAT)
    line = cart.lines[0]
    assert line.status == "open"
    swap = line.swaps[0]
    assert swap.product == "Milk 1l" and swap.price == 60
    assert (swap.used_pct, swap.left_over) == (12, "880 ml")
    assert cart.suggestions == line.swaps  # legacy list is the same data


def test_priced_lines_have_no_swaps():
    assert build_cart(dish(ing("garlic", 20, "g")), 2, [], CAT).lines[0].swaps == []


def test_servings_are_reported_and_scale_the_lines():
    cart = build_cart(dish(ing("garlic", 20, "g")), 8, [], CAT)  # base 2 -> 4x
    assert cart.servings == 8 and cart.lines[0].need == "80 g"


def test_legacy_fields_are_unchanged_by_the_new_ones():
    cart = build_cart(dish(ing("garlic", 20, "g"), ing("penne", 250, "g")), 2, [], CAT)
    assert [i.ingredient for i in cart.items] == ["garlic"] and cart.unmatched == ["penne"]
    assert cart.unmatched_items[0].needed == "250.0 g" and cart.total == 55


# --- model prompts ---------------------------------------------------------

def test_prompts_ask_the_model_to_flag_estimates():
    for system in (SYSTEM_TEXT, SYSTEM_VIDEO):
        assert '"estimated": bool' in system and "ONLY when the source does not state" in system


def test_older_cached_recipes_without_the_flag_still_parse():
    old = '{"dish":"x","base_servings":2,"ingredients":[{"name":"a","quantity":1,"unit":"g"}]}'
    assert ParsedDish.model_validate_json(old).ingredients[0].estimated is False


# --- API: recipe_key -------------------------------------------------------

REEL = "https://www.instagram.com/reel/C9abcDEF123/"
RECIPE = {
    "dish": "dal tadka", "base_servings": 4,
    "ingredients": [
        {"name": "garlic", "quantity": 20, "unit": "g", "estimated": True},
        {"name": "onion", "quantity": 1, "unit": "count"},
        {"name": "butter", "quantity": 30, "unit": "ml"},
    ],
}


@pytest.fixture
def client(client_factory, seeded_engine):
    return client_factory(seeded_engine)


@pytest.fixture
def stubs(monkeypatch):
    calls = {"fetch": 0, "text": 0}

    def fetch(ref):
        calls["fetch"] += 1
        return "caption with a recipe"

    def parse_text(text):
        calls["text"] += 1
        return ParsedDish.model_validate(RECIPE)

    monkeypatch.setattr(main, "fetch_caption", fetch)
    monkeypatch.setattr(main, "parse_recipe_text", parse_text)
    return calls


def post(client, **body):
    return client.post("/cart", json={"dish": "x", "servings": 4, **body})


def test_builtin_dish_returns_a_key_that_rebuilds_it_at_other_servings(client):
    first = post(client, dish="Fish  Molee").json()
    assert first["recipe_key"] == "fish molee" and first["source"] == "dish"
    again = post(client, dish="ignored", recipe_key=first["recipe_key"], servings=8).json()
    assert again["dish"] == "fish molee" and again["servings"] == 8
    fish1 = next(l for l in first["lines"] if l["name"] == "fish")
    fish2 = next(l for l in again["lines"] if l["name"] == "fish")
    assert fish2["need_amount"] == fish1["need_amount"] * 2


def test_reel_key_rebuilds_from_the_cache_without_touching_instagram_or_the_model(client, stubs):
    first = post(client, dish=REEL).json()
    assert first["recipe_key"] == "ig:C9abcDEF123" and first["source"] == "instagram"
    assert first["lines"][0]["estimated"] is True
    again = post(client, dish="ignored", recipe_key=first["recipe_key"], servings=8).json()
    assert again["source"] == "instagram" and again["servings"] == 8
    assert again["lines"][0]["need_amount"] == first["lines"][0]["need_amount"] * 2
    assert stubs == {"fetch": 1, "text": 1}


def test_pasted_text_key_round_trips(client, stubs):
    first = post(client, dish="my dal", caption="200g dal").json()
    assert first["recipe_key"].startswith("text:") and first["source"] == "text"
    assert post(client, recipe_key=first["recipe_key"]).json()["source"] == "text"


@pytest.mark.parametrize("key", ["ig:NOPE000000", "vid:deadbeef", "text:abc"])
def test_unknown_saved_recipe_keys_are_a_clean_404(client, key):
    r = post(client, recipe_key=key)
    assert r.status_code == 404 and "start again" in r.json()["detail"]


def test_upload_response_carries_its_key(client, monkeypatch):
    monkeypatch.setattr(main, "parse_recipe_video", lambda path: ParsedDish.model_validate(RECIPE))
    data = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"0" * 200
    r = client.post("/cart/video", files={"file": ("a.mp4", data, "video/mp4")}, data={"servings": "4"})
    body = r.json()
    assert body["source"] == "video" and body["recipe_key"].startswith("vid:")
    again = post(client, recipe_key=body["recipe_key"], servings=2).json()
    assert again["source"] == "video" and again["servings"] == 2


def test_servings_default_to_the_recipes_own_count(client, monkeypatch):
    # a reel that says "serves 2" must come back as 2, not a global default of 4
    two = {**RECIPE, "base_servings": 2}
    monkeypatch.setattr(main, "fetch_caption", lambda ref: "caption")
    monkeypatch.setattr(main, "parse_recipe_text", lambda text: ParsedDish.model_validate(two))
    body = client.post("/cart", json={"dish": REEL}).json()
    assert body["servings"] == 2 and body["lines"][0]["need_amount"] == 20  # unscaled


def test_explicit_servings_still_win(client):
    assert client.post("/cart", json={"dish": "fish molee", "servings": 6}).json()["servings"] == 6
    assert client.post("/cart", json={"dish": "fish molee", "servings": 0}).status_code == 422


def test_upload_servings_are_optional(client, monkeypatch):
    two = {**RECIPE, "base_servings": 2}
    monkeypatch.setattr(main, "parse_recipe_video", lambda path: ParsedDish.model_validate(two))
    data = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"0" * 200
    r = client.post("/cart/video", files={"file": ("a.mp4", data, "video/mp4")})
    assert r.status_code == 200 and r.json()["servings"] == 2


@pytest.mark.parametrize("name", ["heavy cream", "cream", "double cream"])
def test_swaps_are_found_when_the_ingredient_has_extra_words(name):
    line = build_cart(dish(ing(name, 100, "ml")), 2, [], CAT).lines[0]
    assert line.status == "open"
    assert [s.product for s in line.swaps][:1] == ["Milk 1l"]


def test_unrelated_ingredients_still_get_no_swaps():
    assert build_cart(dish(ing("saffron", 1, "g")), 2, [], CAT).lines[0].swaps == []
