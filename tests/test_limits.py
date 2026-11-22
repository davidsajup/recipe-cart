from datetime import date, timedelta

import pytest

from app import limits
from app.limits import MAX_CONCURRENT, MediaLimit, media_slot


def hold(n):
    """Enter n slots without leaving them; returns the context managers."""
    cms = [media_slot() for _ in range(n)]
    for cm in cms:
        cm.__enter__()
    return cms


def release(cms):
    for cm in cms:
        cm.__exit__(None, None, None)


def test_concurrency_cap_then_release():
    cms = hold(MAX_CONCURRENT)
    with pytest.raises(MediaLimit) as e:
        with media_slot():
            pass
    assert e.value.status == 503
    release(cms[:1])
    with media_slot():  # a freed slot is usable again
        pass
    release(cms[1:])


def test_slot_released_when_the_body_raises():
    with pytest.raises(RuntimeError):
        with media_slot():
            raise RuntimeError("boom")
    release(hold(MAX_CONCURRENT))  # all slots still available


def test_daily_cap(monkeypatch):
    monkeypatch.setenv("VIDEO_MAX_PER_DAY", "2")
    for _ in range(2):
        with media_slot():
            pass
    with pytest.raises(MediaLimit) as e:
        with media_slot():
            pass
    assert e.value.status == 429


def test_daily_cap_resets_on_a_new_day(monkeypatch):
    monkeypatch.setenv("VIDEO_MAX_PER_DAY", "1")
    with media_slot():
        pass
    limits._usage["day"] = date.today() - timedelta(days=1)
    with media_slot():  # new day -> counter reset
        pass


def test_bad_env_value_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("VIDEO_MAX_PER_DAY", "lots")
    assert limits.daily_cap() == 30
