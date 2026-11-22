import pytest

from app.db import read_seed_file
from app.matcher import build_cart
from app.parser import parse_dish
from app.providers.base import Location, Offer
from app.providers.cart import MAX_ALTERNATIVES, build_provider_cart, selections_from_cart
from app.providers.fake_instamart import TOKEN, FakeInstamart
from app.providers.instamart import AuthExpired, InstamartClient, InstamartProvider
from app.providers.own import OwnCatalogueProvider
from app.schemas import Ingredient, ParsedDish

LOC = Location(address_id="addr_1")


@pytest.fixture
def fake():
    return FakeInstamart()


@pytest.fixture
def provider(fake):
    return InstamartProvider(InstamartClient(TOKEN, url="http://fake/im", http=fake.client()))


def dish(*ings, base=2):
    return ParsedDish(dish="t", base_servings=base, ingredients=[Ingredient(name=n, quantity=q, unit=u) for n, q, u in ings])


def searches(fake):
    return [a["query"] for t, a in fake.calls if t == "search_products"]


# --- pricing from live offers ----------------------------------------------

def test_lines_are_priced_from_live_offers_with_pack_maths(provider):
    cart = build_provider_cart(dish(("garlic", 20, "g"), ("butter", 30, "ml")), 2, provider, LOC)
    garlic, butter = cart.lines
    assert (garlic.status, garlic.product, garlic.packs, garlic.price) == ("priced", "Fresh Garlic 250 g", 1, 70)
    assert (garlic.used_pct, garlic.left_over) == (8, "230 g")
    assert (butter.product, butter.price, butter.used_pct) == ("Amul Butter 100 g", 58, 30)
    assert cart.total == 128 and cart.provider == "instamart" and cart.provider_badge == "Powered by Instamart"


def test_servings_scale_the_packs(provider):
    cart = build_provider_cart(dish(("onion", 600, "g")), 8, provider, LOC)  # base 2 -> 4x -> 2400 g
    line = cart.lines[0]
    assert line.need == "2400 g"
    assert (line.product, line.packs, line.price) == ("Fresh Onion 1 kg", 3, 147)  # the provider's first (1 kg) offer, 3 packs


def test_the_providers_order_wins_over_name_similarity(provider):
    # "cream" also matches nothing odd here, but the principle: first relevant, sized, compatible offer
    line = build_provider_cart(dish(("cream", 100, "ml")), 2, provider, LOC).lines[0]
    assert line.product == "Amul Fresh Cream 200 ml" and line.packs == 1


def test_out_of_stock_variants_are_skipped(provider):
    line = build_provider_cart(dish(("parmesan cheese", 150, "g")), 2, provider, LOC).lines[0]
    assert line.product == "Imported Parmesan Cheese 200 g"  # the 100 g pack is out of stock


def test_every_line_carries_the_ids_the_cart_needs(provider):
    line = build_provider_cart(dish(("garlic", 20, "g")), 2, provider, LOC).lines[0]
    assert line.provider == "instamart"
    assert set(line.offer) == {"provider", "product_id", "variant_id", "sku_id", "size_text", "max_quantity"}


def test_other_variants_are_offered_as_priced_alternatives(provider):
    line = build_provider_cart(dish(("onion", 600, "g")), 2, provider, LOC).lines[0]
    alts = line.alternatives
    assert [a.size_text for a in alts] == ["500 g", "2 kg"] and len(alts) <= MAX_ALTERNATIVES
    assert all(a.priced and a.packs >= 1 and a.offer["variant_id"] for a in alts)
    a500 = alts[0]
    assert (a500.packs, a500.price) == (2, 56)  # 600 g needs two 500 g packs


def test_a_size_we_cannot_read_is_shown_but_never_priced(provider):
    line = build_provider_cart(dish(("mystery spice box", 1, "count")), 2, provider, LOC).lines[0]
    assert line.status == "open" and line.price == 0 and line.product is None
    (alt,) = line.alternatives
    assert not alt.priced and alt.size_text == "assorted" and alt.price == 89  # shown for the customer to choose


def test_unconvertible_units_still_give_the_customer_choices(provider):
    # "2 count" of milk can't be turned into millilitres, so we can't price it - but we can show the milks
    line = build_provider_cart(dish(("toned milk", 2, "count")), 2, provider, LOC).lines[0]
    assert line.status == "open"
    assert [a.size_text for a in line.alternatives] == ["500 ml", "1 L"] and not any(a.priced for a in line.alternatives)


def test_a_typical_chicken_breast_weight_lets_pieces_be_priced(provider):
    line = build_provider_cart(dish(("chicken breast", 2, "count")), 2, provider, LOC).lines[0]  # 2 x 200 g = 400 g
    assert line.status == "priced" and line.product == "Licious Chicken Breast Boneless 500 g"
    assert (line.used_pct, line.left_over) == (80, "100 g")


def test_nothing_found_is_an_open_line_with_a_prefill(provider):
    cart = build_provider_cart(dish(("saffron", 1, "g")), 2, provider, LOC)
    line = cart.lines[0]
    assert line.status == "open" and line.suggested_pack_size == 50 and line.alternatives == []
    assert cart.unmatched == ["saffron"] and cart.unmatched_items[0].name == "saffron" and cart.total == 0


def test_water_is_free(provider, fake):
    cart = build_provider_cart(dish(("water", 2, "cup"), ("garlic", 20, "g")), 2, provider, LOC)
    assert [l.name for l in cart.lines] == ["garlic"] and searches(fake) == ["garlic"]


# --- search economy --------------------------------------------------------

def test_each_distinct_ingredient_is_searched_once(provider, fake):
    build_provider_cart(dish(("garlic", 20, "g"), ("Garlic", 5, "g"), ("butter", 30, "ml"), ("garlic", 1, "g")), 2, provider, LOC)
    assert searches(fake) == ["garlic", "butter"]


def test_pantry_ingredients_are_still_priced_so_toggling_back_to_buy_works(provider):
    cart = build_provider_cart(dish(("garlic", 20, "g"), ("butter", 30, "ml")), 2, provider, LOC, pantry=["butter"])
    butter = next(l for l in cart.lines if l.name == "butter")
    assert butter.in_pantry and butter.status == "priced" and butter.price == 58
    assert cart.skipped_pantry == ["butter"] and cart.total == 70  # legacy total leaves it out, as before
    assert [i.ingredient for i in cart.items] == ["garlic"]


def test_provider_failures_propagate_to_the_caller(fake):
    fake.token = "other"  # the customer's token no longer matches
    bad = InstamartProvider(InstamartClient(TOKEN, url="http://fake/im", http=fake.client(), sleep=lambda s: None))
    with pytest.raises(AuthExpired):
        build_provider_cart(dish(("garlic", 20, "g")), 2, bad, LOC)


# --- from lines to the provider's cart -------------------------------------

def test_selections_cover_priced_lines_you_are_buying(provider):
    cart = build_provider_cart(dish(("onion", 600, "g"), ("garlic", 20, "g"), ("saffron", 1, "g")), 2, provider, LOC)
    sel = selections_from_cart(cart)
    assert [s.offer.name for s in sel] == ["Fresh Onion 1 kg", "Fresh Garlic 250 g"] and [s.quantity for s in sel] == [1, 1]
    assert [s.offer.name for s in selections_from_cart(cart, have={"Onion"})] == ["Fresh Garlic 250 g"]  # case-insensitive


def test_the_providers_own_cart_agrees_with_our_total(provider, fake):
    d = dish(("onion", 600, "g"), ("garlic", 20, "g"), ("butter", 30, "ml"), ("penne", 250, "g"), ("cream", 100, "ml"))
    cart = build_provider_cart(d, 4, provider, LOC)  # scaled 2x
    result = provider.set_cart(selections_from_cart(cart), LOC)
    assert float(result.total) == pytest.approx(cart.total)
    assert len(fake.cart) == len([l for l in cart.lines if l.status == "priced"])


def test_a_selection_round_trips_through_the_offer_ref(provider):
    cart = build_provider_cart(dish(("garlic", 20, "g")), 2, provider, LOC)
    (sel,) = selections_from_cart(cart)
    original = next(o for o in provider.search("garlic", LOC) if o.size_text == "250 g")
    assert (sel.offer.variant_id, sel.offer.sku_id, sel.offer.price) == (original.variant_id, original.sku_id, original.price)


# --- parity with the built-in catalogue ------------------------------------

def test_the_catalogue_as_a_provider_prices_like_the_original_builder():
    products = read_seed_file()
    parsed = parse_dish("fish molee")
    old = build_cart(parsed, 8, [], products)
    new = build_provider_cart(parsed, 8, OwnCatalogueProvider(products), Location(), [])
    assert new.provider == "catalogue" and new.provider_badge == ""
    assert new.total == old.total
    for a, b in zip(old.lines, new.lines):
        assert (a.name, a.status, a.product, a.packs, a.price, a.used_pct, a.left_over) == (
            b.name, b.status, b.product, b.packs, b.price, b.used_pct, b.left_over
        ), a.name
