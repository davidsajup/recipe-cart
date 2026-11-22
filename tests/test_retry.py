import httpx
import pytest
from google.genai import errors

from app.parser import call_with_retry, is_transient


def server_error(code=503):
    return errors.ServerError(code, {"error": {"message": "overloaded", "status": "UNAVAILABLE"}})


def client_error(code=400):
    return errors.ClientError(code, {"error": {"message": "bad", "status": "INVALID_ARGUMENT"}})


def flaky(*outcomes):
    """Callable that yields each outcome in turn (exceptions are raised)."""
    it = iter(outcomes)
    calls = []

    def fn():
        calls.append(1)
        out = next(it)
        if isinstance(out, Exception):
            raise out
        return out

    fn.calls = calls
    return fn


def run(fn, delays=(1.0, 3.0)):
    slept = []
    result = call_with_retry(fn, delays=delays, sleep=slept.append)
    return result, slept


def test_success_first_try_no_sleep():
    fn = flaky("ok")
    assert run(fn) == ("ok", [])
    assert len(fn.calls) == 1


def test_retries_503_then_succeeds():
    fn = flaky(server_error(503), server_error(503), "ok")
    result, slept = run(fn)
    assert result == "ok" and len(fn.calls) == 3
    assert len(slept) == 2 and 1.0 <= slept[0] <= 1.25 and 3.0 <= slept[1] <= 3.75  # backoff + jitter


def test_gives_up_after_three_attempts_and_raises_last_error():
    fn = flaky(server_error(503), server_error(503), server_error(503), "never reached")
    with pytest.raises(errors.ServerError):
        run(fn)
    assert len(fn.calls) == 3


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_client_errors_are_not_retried(code):
    fn = flaky(client_error(code), "never reached")
    with pytest.raises(errors.ClientError):
        run(fn)
    assert len(fn.calls) == 1


def test_rate_limit_429_is_retried():
    fn = flaky(client_error(429), "ok")
    assert run(fn)[0] == "ok" and len(fn.calls) == 2


def test_timeouts_and_connection_errors_are_retried():
    fn = flaky(httpx.ReadTimeout("slow"), httpx.ConnectError("down"), "ok")
    assert run(fn)[0] == "ok" and len(fn.calls) == 3


def test_non_api_errors_propagate_immediately():
    fn = flaky(KeyError("bug"), "never reached")
    with pytest.raises(KeyError):
        run(fn)
    assert len(fn.calls) == 1


def test_is_transient():
    assert is_transient(server_error(500)) and is_transient(server_error(504))
    assert not is_transient(client_error(400)) and not is_transient(ValueError("x"))


def test_gemini_complete_retries_through_the_real_wrapper(monkeypatch):
    """gemini_complete must actually use the retry (fake SDK client, no network)."""
    import app.parser as parser
    from google import genai

    calls = []

    class FakeResp:
        text = '{"ok": true}'

    class FakeModels:
        def generate_content(self, **kwargs):
            calls.append(kwargs["model"])
            if len(calls) < 3:
                raise server_error(503)
            return FakeResp()

    class FakeClient:
        def __init__(self, **kwargs):
            self.models = FakeModels()

    monkeypatch.setattr(genai, "Client", FakeClient)
    monkeypatch.setattr(parser.time, "sleep", lambda s: None)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    assert parser.gemini_complete("sys", "Dish: dal") == '{"ok": true}'
    assert len(calls) == 3
