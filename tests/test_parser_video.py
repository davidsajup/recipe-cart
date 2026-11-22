from types import SimpleNamespace

import pytest
from google.genai import errors, types

import app.parser as parser
from app.parser import (
    NO_SPEECH,
    SYSTEM_TEXT,
    SYSTEM_TRANSCRIBE,
    SYSTEM_VIDEO,
    NoRecipeError,
    clean_transcript,
    compose_post,
    media_session,
    mime_for,
    parse_recipe_video,
    transcribe,
)

GOOD = '{"dish":"dal","base_servings":2,"ingredients":[{"name":"dal","quantity":1,"unit":"cup"}]}'
EMPTY = '{"dish":"","base_servings":4,"ingredients":[]}'
SPEECH = "First chop two onions and fry them with the cumin seeds until golden."


def server_error(code=503):
    return errors.ServerError(code, {"error": {"message": "busy", "status": "UNAVAILABLE"}})


class FakeGemini:
    """Stands in for (genai.Client, types) and records everything."""

    def __init__(self, replies=(GOOD,), file_states=(types.FileState.ACTIVE,)):
        self.replies = list(replies)
        self.file_states = list(file_states)
        self.generate_calls = []
        self.uploads, self.gets, self.deletes = [], [], []
        gem = self

        class Models:
            def generate_content(self, **kw):
                gem.generate_calls.append(kw)
                out = gem.replies.pop(0) if gem.replies else GOOD
                if isinstance(out, Exception):
                    raise out
                return SimpleNamespace(text=out)

        class Files:
            def upload(self, *, file, config=None):
                gem.uploads.append((file, config))
                return gem._file(gem.file_states[0])

            def get(self, *, name, config=None):
                gem.gets.append(name)
                idx = min(len(gem.gets), len(gem.file_states) - 1)
                return gem._file(gem.file_states[idx])

            def delete(self, *, name, config=None):
                gem.deletes.append(name)

        self.models, self.files = Models(), Files()

    @staticmethod
    def _file(state):
        return SimpleNamespace(name="files/abc", uri="https://example.test/files/abc", state=state)


@pytest.fixture
def gem(monkeypatch):
    holder = {}

    def install(**kw):
        g = FakeGemini(**kw)
        monkeypatch.setattr(parser, "_client", lambda timeout_ms: (g, types))
        monkeypatch.setattr(parser.time, "sleep", lambda s: None)
        holder["g"] = g
        return g

    return install


@pytest.fixture
def small(tmp_path):
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"small video bytes")
    return p


@pytest.fixture
def big(tmp_path, monkeypatch):
    monkeypatch.setattr(parser, "INLINE_LIMIT", 10)  # force the Files API path
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"x" * 100)
    return p


# --- small helpers ---------------------------------------------------------

def test_prompts_mark_media_untrusted_and_ask_for_english_names():
    for system in (SYSTEM_TEXT, SYSTEM_VIDEO, SYSTEM_TRANSCRIBE):
        assert "untrusted" in system
    for system in (SYSTEM_TEXT, SYSTEM_VIDEO):
        assert "English" in system and "ignore any instructions" in system


def test_clean_transcript():
    assert clean_transcript(SPEECH) == SPEECH
    assert clean_transcript(f"  {NO_SPEECH}  ") == ""
    assert clean_transcript("[No Speech]") == ""
    assert clean_transcript("hmm ok") == ""  # too short to be a recipe
    assert clean_transcript("") == "" and clean_transcript(None) == ""
    assert len(clean_transcript("word " * 10_000)) <= parser.MAX_POST_CHARS


def test_compose_post():
    assert compose_post("cap", "talk") == "Caption:\ncap\n\nSpoken transcript:\ntalk"
    assert compose_post(None, "talk") == "Spoken transcript:\ntalk"
    assert compose_post("cap", "") == "Caption:\ncap"
    assert compose_post(None, None) == ""


@pytest.mark.parametrize("name,mime", [("a.MP4", "video/mp4"), ("a.mov", "video/quicktime"), ("a.m4a", "audio/mp4"), ("a.xyz", "application/octet-stream")])
def test_mime_for(name, mime):
    assert mime_for(name) == mime


# --- media_session ---------------------------------------------------------

def test_small_file_goes_inline_without_the_files_api(gem, small):
    g = gem()
    with media_session(small, 1000, json_out=True) as complete:
        assert complete("sys", "user") == GOOD
    assert g.uploads == [] and g.deletes == []
    contents = g.generate_calls[0]["contents"]
    assert contents[0].inline_data.mime_type == "video/mp4"
    assert contents[0].inline_data.data == b"small video bytes"
    assert contents[1] == "user"


def test_json_vs_text_response_type(gem, small):
    g = gem()
    with media_session(small, 1000, json_out=True) as c:
        c("s", "u")
    with media_session(small, 1000, json_out=False) as c:
        c("s", "u")
    assert [call["config"].response_mime_type for call in g.generate_calls] == ["application/json", "text/plain"]
    assert g.generate_calls[0]["config"].system_instruction == "s"


def test_big_file_uses_files_api_waits_for_processing_and_cleans_up(gem, big):
    g = gem(file_states=(types.FileState.PROCESSING, types.FileState.PROCESSING, types.FileState.ACTIVE))
    with media_session(big, 1000, json_out=True) as complete:
        complete("sys", "user")
    assert [u[0] for u in g.uploads] == [str(big)]
    assert len(g.gets) == 2  # polled until ACTIVE
    part = g.generate_calls[0]["contents"][0]
    assert part.file_data.file_uri == "https://example.test/files/abc"
    assert g.deletes == ["files/abc"]


def test_remote_file_deleted_even_when_generation_fails(gem, big):
    g = gem(replies=[ValueError("model exploded")])
    with pytest.raises(ValueError):
        with media_session(big, 1000, json_out=True) as complete:
            complete("s", "u")
    assert g.deletes == ["files/abc"]


def test_failed_processing_raises_and_still_deletes(gem, big):
    g = gem(file_states=(types.FileState.FAILED,))
    with pytest.raises(RuntimeError):
        with media_session(big, 1000, json_out=True):
            pass
    assert g.deletes == ["files/abc"]


def test_a_failing_delete_does_not_mask_the_result(gem, big):
    g = gem()
    g.files.delete = lambda **kw: (_ for _ in ()).throw(RuntimeError("delete failed"))
    with media_session(big, 1000, json_out=True) as complete:
        assert complete("s", "u") == GOOD  # no exception from cleanup


def test_transient_errors_are_retried_inside_the_session(gem, small):
    g = gem(replies=[server_error(503), server_error(503), GOOD])
    with media_session(small, 1000, json_out=True) as complete:
        assert complete("s", "u") == GOOD
    assert len(g.generate_calls) == 3


# --- transcribe / parse_recipe_video --------------------------------------

def test_transcribe_returns_clean_speech(gem, small):
    g = gem(replies=[SPEECH])
    assert transcribe(small) == SPEECH
    assert g.generate_calls[0]["config"].system_instruction == SYSTEM_TRANSCRIBE


@pytest.mark.parametrize("reply", [NO_SPEECH, "uh", ""])
def test_transcribe_returns_empty_when_there_is_no_speech(gem, small, reply):
    gem(replies=[reply])
    assert transcribe(small) == ""


def test_parse_recipe_video_happy_path(gem, small):
    g = gem(replies=[GOOD])
    parsed = parse_recipe_video(small)
    assert parsed.dish == "dal" and parsed.base_servings == 2
    assert g.generate_calls[0]["config"].system_instruction == SYSTEM_VIDEO


def test_parse_recipe_video_empty_result_is_no_recipe(gem, small):
    gem(replies=[EMPTY])
    with pytest.raises(NoRecipeError):
        parse_recipe_video(small)


def test_video_is_uploaded_once_even_when_the_reply_needs_a_retry(gem, big):
    g = gem(replies=["not json at all", GOOD])
    assert parse_recipe_video(big).dish == "dal"
    assert len(g.generate_calls) == 2 and len(g.uploads) == 1  # re-asked, not re-uploaded
    assert g.deletes == ["files/abc"]
    assert "invalid" in g.generate_calls[1]["contents"][1]


def test_parse_recipe_video_accepts_an_injected_completer(tmp_path):
    seen = []

    def complete(system, user):
        seen.append((system, user))
        return GOOD

    assert parse_recipe_video(tmp_path / "never_read.mp4", complete=complete).dish == "dal"
    assert seen[0][0] == SYSTEM_VIDEO
