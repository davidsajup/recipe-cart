import pytest
from sqlmodel import Session, select

import app.main as main
from app.dishcache import ParsedDishRow, dish_key, get_cached
from app.schemas import ParsedDish

DAL = {
    "dish": "dal",
    "base_servings": 4,
    "ingredients": [{"name": "toor dal", "quantity": 200, "unit": "g"}],
}


@pytest.fixture
def llm(monkeypatch):
    """Replace the real parser; counts calls and lets a test queue outcomes."""
    calls = []
    outcomes = []

    def fake_parse(dish):
        calls.append(dish)
        out = outcomes.pop(0) if outcomes else DAL
        if isinstance(out, Exception):
            raise out
        return ParsedDish.model_validate(out)

    monkeypatch.setattr(main, "parse_dish", fake_parse)
    monkeypatch.setattr(main, "is_builtin", lambda d: d.lower().strip() == "fish molee")
    fake_parse.calls, fake_parse.outcomes = calls, outcomes
    return fake_parse


@pytest.fixture
def client(client_factory, seeded_engine):
    return client_factory(seeded_engine)


def cart(client, dish="dal", **extra):
    return client.post("/cart", json={"dish": dish, **extra})


def test_dish_key_normalises():
    assert dish_key("  Fish   MOLEE! ") == "fish molee"
    assert dish_key("Dal-Tadka") == "dal tadka"


def test_second_request_skips_the_llm(client, llm):
    assert cart(client).status_code == 200
    assert cart(client).status_code == 200
    assert len(llm.calls) == 1


def test_key_is_case_and_spacing_insensitive(client, llm):
    cart(client, "dal tadka")
    cart(client, "  Dal   TADKA ")
    assert len(llm.calls) == 1


def test_cached_result_gives_same_cart(client, llm):
    assert cart(client).json() == cart(client).json()


def test_failures_are_not_cached(client, llm):
    llm.outcomes.append(ValueError("bad json"))
    assert cart(client).status_code == 502
    assert cart(client).status_code == 200  # retried, not served from cache
    assert len(llm.calls) == 2


def test_refresh_bypasses_and_overwrites(client, llm, seeded_engine):
    cart(client)
    llm.outcomes.append({**DAL, "ingredients": [{"name": "toor dal", "quantity": 999, "unit": "g"}]})
    assert cart(client, refresh=True).status_code == 200
    assert len(llm.calls) == 2
    with Session(seeded_engine) as s:
        assert get_cached(s, "dal").ingredients[0].quantity == 999
        assert len(s.exec(select(ParsedDishRow)).all()) == 1  # overwritten, not duplicated
    cart(client)  # now served from the refreshed copy
    assert len(llm.calls) == 2


def test_builtin_dish_is_not_stored(client, llm, seeded_engine):
    cart(client, "fish molee")
    with Session(seeded_engine) as s:
        assert s.exec(select(ParsedDishRow)).all() == []


def test_corrupt_cache_row_is_treated_as_a_miss(client, llm, seeded_engine):
    with Session(seeded_engine) as s:
        s.add(ParsedDishRow(key="dal", data="{not valid"))
        s.commit()
    assert cart(client).status_code == 200
    assert len(llm.calls) == 1
