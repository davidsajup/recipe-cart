import pytest

from app.providers.quantity import parse_quantity


@pytest.mark.parametrize(
    "text,expected",
    [
        # plain sizes
        ("500 g", (500, "g")),
        ("500g", (500, "g")),
        ("500 gm", (500, "g")),
        ("250 grams", (250, "g")),
        ("1 kg", (1000, "g")),
        ("1.5 kg", (1500, "g")),
        ("1,000 g", (1000, "g")),
        ("500 ml", (500, "ml")),
        ("1 L", (1000, "ml")),
        ("1 Ltr", (1000, "ml")),
        ("1 litre", (1000, "ml")),
        ("1.5 L", (1500, "ml")),
        ("750 millilitres", (750, "ml")),
        # counts
        ("6 pcs", (6, "count")),
        ("1 pc", (1, "count")),
        ("12 units", (12, "count")),
        ("Pack of 6", (6, "count")),
        ("pack of 12", (12, "count")),
        ("Set of 4", (4, "count")),
        ("6 pack", (6, "count")),
        ("1 dozen", (12, "count")),
        ("dozen", (12, "count")),
        # multipacks, either way round, plain x or the multiplication sign
        ("2 x 250 g", (500, "g")),
        ("2 × 250 g", (500, "g")),
        ("6 x 50 g", (300, "g")),
        ("250 g x 2", (500, "g")),
        ("4 x 200 ml", (800, "ml")),
        # ranges take the low end, so we never under-buy
        ("100-120 g", (100, "g")),
        ("100 to 120 g", (100, "g")),
        # surrounding words are ignored
        ("Net 1 kg", (1000, "g")),
        ("approx. 400 g", (400, "g")),
        ("Pack of 3 x 100 g", (300, "g")),
    ],
)
def test_parse_quantity(text, expected):
    assert parse_quantity(text) == expected


@pytest.mark.parametrize("text", [None, "", "   ", "assorted", "1 large", "per piece", "0 g", "Pack of 0", "g", "kg 1x"])
def test_unreadable_sizes_are_none_not_guesses(text):
    assert parse_quantity(text) is None


def test_the_unit_is_never_confused_with_a_longer_word():
    assert parse_quantity("1 large") is None      # 'l' must not match inside 'large'
    assert parse_quantity("2 grapes") is None     # 'g' must not match inside 'grapes'
    assert parse_quantity("5 pcs") == (5, "count")  # but 'pcs' is a unit


def test_results_are_in_base_units():
    size, unit = parse_quantity("2 kg")
    assert (size, unit) == (2000, "g")
    size, unit = parse_quantity("2 L")
    assert (size, unit) == (2000, "ml")
