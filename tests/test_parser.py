import pytest

from app.parser import parse_dish

GOOD = (
    '{"dish": "dal", "base_servings": 2, "ingredients": '
    '[{"name": "lentils", "quantity": 200, "unit": "g"}]}'
)


def fake(*replies):
    calls = []
    it = iter(replies)

    def complete(system, user):
        calls.append(user)
        return next(it)

    complete.calls = calls
    return complete


def test_known_dish_skips_llm():
    def boom(*a):
        raise AssertionError("LLM should not be called")

    assert parse_dish("Fish Molee", complete=boom).dish == "fish molee"


def test_llm_valid_json_with_fences():
    c = fake("```json\n" + GOOD + "\n```")
    parsed = parse_dish("dal", complete=c)
    assert parsed.ingredients[0].name == "lentils"
    assert len(c.calls) == 1


def test_retry_once_then_succeed():
    c = fake("not json", GOOD)
    assert parse_dish("dal", complete=c).dish == "dal"
    assert "invalid" in c.calls[1]


def test_fails_after_retry():
    with pytest.raises(ValueError):
        parse_dish("dal", complete=fake("nope", '{"dish": "dal"}'))


def test_builtin_dish_matches_whatever_the_spacing_or_case():
    from app.parser import is_builtin

    def boom(*a):
        raise AssertionError("LLM should not be called")

    for name in ("fish molee", "  Fish   MOLEE ", "FISH\tMOLEE"):
        assert is_builtin(name)
        assert parse_dish(name, complete=boom).dish == "fish molee"
