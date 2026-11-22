from pathlib import Path

import pytest
from sqlmodel import Session, select

import app.main as main
from app import limits
from app.dishcache import ParsedDishRow
from app.parser import NoRecipeError
from app.schemas import ParsedDish
from app.video_fetch import Downloaded

REEL = "https://www.instagram.com/reel/C9abcDEF123/"
RECIPE = {
    "dish": "dal tadka",
    "base_servings": 4,
    "ingredients": [
        {"name": "toor dal", "quantity": 200, "unit": "g"},
        {"name": "onion", "quantity": 2, "unit": "count"},
    ],
}
SPEECH = "Chop two onions, fry them with cumin and then add the boiled toor dal."


@pytest.fixture
def client(client_factory, seeded_engine):
    return client_factory(seeded_engine)


@pytest.fixture
def media(monkeypatch, tmp_path):
    """Fake every network/LLM seam of the media pipeline and record the calls."""
    st = {
        "caption": None,            # what fetch_caption returns
        "audio": "audio",           # "audio" | "muxed" | None (download fails)
        "video": True,              # does the fallback video download succeed
        "transcript": SPEECH,
        "text_recipe": RECIPE,      # parse_recipe_text result (None -> NoRecipeError)
        "video_recipe": RECIPE,     # parse_recipe_video result (None -> NoRecipeError)
        "downloads": [], "texts": [], "videos": [], "transcribed": [], "workdirs": [],
    }

    def fetch(ref):
        return st["caption"]

    def download(ref, workdir, audio_only, **kw):
        st["downloads"].append("audio" if audio_only else "video")
        st["workdirs"].append(Path(workdir))
        if audio_only:
            if st["audio"] is None:
                return None
            name = "media.m4a" if st["audio"] == "audio" else "media.mp4"
            is_video = st["audio"] == "muxed"
        else:
            if not st["video"]:
                return None
            name, is_video = "media.mp4", True
        path = Path(workdir) / name
        path.write_bytes(b"media")
        return Downloaded(path=path, is_video=is_video)

    def transcribe(path):
        st["transcribed"].append(Path(path).name)
        return st["transcript"]

    def parse_text(text):
        st["texts"].append(text)
        if st["text_recipe"] is None:
            raise NoRecipeError("none")
        return ParsedDish.model_validate(st["text_recipe"])

    def parse_video(path):
        st["videos"].append(Path(path).name)
        if st["video_recipe"] is None:
            raise NoRecipeError("none")
        return ParsedDish.model_validate(st["video_recipe"])

    monkeypatch.setattr(main, "fetch_caption", fetch)
    monkeypatch.setattr(main, "download_reel", download)
    monkeypatch.setattr(main, "transcribe", transcribe)
    monkeypatch.setattr(main, "parse_recipe_text", parse_text)
    monkeypatch.setattr(main, "parse_recipe_video", parse_video)
    return st


def post(client, dish=REEL, **extra):
    return client.post("/cart", json={"dish": dish, "servings": 4, **extra})


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("VIDEO_DOWNLOAD", "1")


def no_text_recipe_unless_transcript(media):
    """Caption/text alone has no recipe; only text that includes the transcript does."""
    original = media["text_recipe"]

    def parse_text(text):
        media["texts"].append(text)
        if "Spoken transcript" not in text:
            raise NoRecipeError("none")
        return ParsedDish.model_validate(original)

    return parse_text


# --- feature flag ----------------------------------------------------------

def test_download_is_never_attempted_when_the_flag_is_off(client, media):
    r = post(client)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == "needs_caption" and detail["can_upload_video"] is True
    assert "upload the video" in detail["message"]
    assert media["downloads"] == []


# --- transcript-first path -------------------------------------------------

def test_transcript_path_builds_a_cart_and_is_cached(client, media, enabled, seeded_engine):
    r = post(client)
    assert r.status_code == 200 and r.json()["source"] == "instagram"
    assert media["downloads"] == ["audio"]  # no video fetched
    assert media["transcribed"] == ["media.m4a"]
    assert media["videos"] == []
    assert media["texts"] == [f"Spoken transcript:\n{SPEECH}"]
    # repeat (even a different URL shape) hits the cache: no new downloads
    assert post(client, "https://www.instagram.com/reels/C9abcDEF123/?igsh=1").status_code == 200
    assert media["downloads"] == ["audio"]
    with Session(seeded_engine) as s:
        assert [r.key for r in s.exec(select(ParsedDishRow)).all()] == ["ig:C9abcDEF123"]


def test_caption_and_transcript_are_combined(client, media, enabled, monkeypatch):
    media["caption"] = "Easy dal! #food"
    monkeypatch.setattr(main, "parse_recipe_text", no_text_recipe_unless_transcript(media))
    assert post(client).status_code == 200
    # first the caption alone (no recipe), then caption + transcript together
    assert media["texts"][0] == "Easy dal! #food"
    assert media["texts"][1] == f"Caption:\nEasy dal! #food\n\nSpoken transcript:\n{SPEECH}"


def test_a_caption_with_a_recipe_never_touches_media(client, media, enabled):
    media["caption"] = "200g toor dal, 2 onions"
    assert post(client).status_code == 200
    assert media["downloads"] == [] and media["transcribed"] == []


# --- video fallback --------------------------------------------------------

def test_silent_reel_falls_back_to_video(client, media, enabled):
    media["transcript"] = ""  # no speech
    r = post(client)
    assert r.status_code == 200
    assert media["downloads"] == ["audio", "video"]
    assert media["videos"] == ["media.mp4"]


def test_transcript_without_a_recipe_falls_back_to_video(client, media, enabled, monkeypatch):
    media["text_recipe"] = None  # chatter, no recipe
    assert post(client).status_code == 200
    assert media["downloads"] == ["audio", "video"] and media["videos"] == ["media.mp4"]


def test_fallback_can_be_disabled(client, media, enabled, monkeypatch):
    monkeypatch.setenv("VIDEO_FALLBACK", "0")
    media["transcript"] = ""
    r = post(client)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "needs_caption"
    assert media["downloads"] == ["audio"] and media["videos"] == []


def test_muxed_download_goes_straight_to_video_parsing(client, media, enabled):
    media["audio"] = "muxed"
    assert post(client).status_code == 200
    assert media["downloads"] == ["audio"]  # no second download
    assert media["transcribed"] == [] and media["videos"] == ["media.mp4"]


def test_video_without_a_recipe_asks_for_the_caption(client, media, enabled):
    media["transcript"] = ""
    media["video_recipe"] = None
    r = post(client)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "needs_caption"


@pytest.mark.parametrize("which", ["audio", "video"])
def test_failed_downloads_fall_back_to_asking_the_user(client, media, enabled, which):
    media["transcript"] = ""
    if which == "audio":
        media["audio"] = None
    else:
        media["video"] = False
    r = post(client)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "needs_caption"


def test_pasted_caption_skips_media_even_when_enabled(client, media, enabled):
    assert post(client, caption="1 cup dal, 2 onions").status_code == 200
    assert media["downloads"] == []


def test_temp_dir_is_removed_after_success_and_failure(client, media, enabled):
    media["transcript"] = ""
    post(client)  # success via fallback
    media["audio"] = None
    post(client, "https://www.instagram.com/reel/ZZZZZ99999/")  # failure
    assert media["workdirs"], "downloader was never called"
    assert all(not p.exists() for p in media["workdirs"])


def test_failed_media_attempts_are_not_cached(client, media, enabled):
    media["audio"] = None
    assert post(client).status_code == 422
    media["audio"] = "audio"
    assert post(client).status_code == 200


# --- limits ----------------------------------------------------------------

def test_daily_cap_blocks_further_media_jobs(client, media, enabled, monkeypatch):
    monkeypatch.setenv("VIDEO_MAX_PER_DAY", "1")
    assert post(client).status_code == 200
    r = post(client, "https://www.instagram.com/reel/NEWREEL1234/")
    assert r.status_code == 429
    assert post(client).status_code == 200  # the cached one still works


def test_concurrency_cap_returns_busy(client, media, enabled):
    held = [limits.media_slot() for _ in range(limits.MAX_CONCURRENT)]
    for h in held:
        h.__enter__()
    try:
        assert post(client).status_code == 503
    finally:
        for h in held:
            h.__exit__(None, None, None)
    assert post(client).status_code == 200


# --- upload endpoint -------------------------------------------------------

def mp4(size=300, brand=b"mp42"):
    return b"\x00\x00\x00\x18ftyp" + brand + b"\x00\x00\x00\x00mp42isom" + b"0" * size


def upload(client, data, name="clip.mp4", **form):
    return client.post("/cart/video", files={"file": (name, data, "video/mp4")}, data={"servings": "4", **form})


def test_upload_builds_a_cart(client, media):
    r = upload(client, mp4())
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "video" and body["dish"] == "dal tadka"
    assert {i["ingredient"] for i in body["items"]} >= {"toor dal", "onion"}
    assert media["videos"] == ["upload.mp4"]  # saved under a server-chosen name


def test_same_file_is_served_from_the_cache(client, media, seeded_engine):
    data = mp4()
    upload(client, data, name="a.mp4")
    r = upload(client, data, name="renamed.mp4")
    assert r.status_code == 200 and len(media["videos"]) == 1
    with Session(seeded_engine) as s:
        keys = [row.key for row in s.exec(select(ParsedDishRow)).all()]
    assert len(keys) == 1 and keys[0].startswith("vid:")
    assert len(upload(client, mp4(size=301)).json()["items"]) > 0 and len(media["videos"]) == 2


def test_refresh_reparses_an_uploaded_video(client, media):
    data = mp4()
    upload(client, data)
    assert upload(client, data, refresh="true").status_code == 200
    assert len(media["videos"]) == 2


def test_quicktime_and_webm_are_recognised(client, media):
    assert upload(client, mp4(brand=b"qt  "), name="x.mov").status_code == 200
    assert media["videos"][-1] == "upload.mov"
    webm = bytes.fromhex("1a45dfa3") + b"0" * 200
    assert upload(client, webm, name="x.webm").status_code == 200
    assert media["videos"][-1] == "upload.webm"


@pytest.mark.parametrize("data", [b"", b"just some text, not a video", b"%PDF-1.7 " + b"0" * 100, b"MZ" + b"0" * 100])
def test_non_video_files_are_rejected_whatever_the_filename(client, media, data):
    r = upload(client, data, name="definitely_a_video.mp4")
    assert r.status_code == 415 and media["videos"] == []


def test_oversized_upload_is_rejected_while_streaming(client, media, monkeypatch):
    monkeypatch.setenv("MAX_VIDEO_MB", "1")
    r = upload(client, mp4(size=1_500_000))  # under the header slack, over the limit
    assert r.status_code == 413 and media["videos"] == []


def test_hugely_oversized_upload_is_rejected_from_the_header(client, media, monkeypatch):
    monkeypatch.setenv("MAX_VIDEO_MB", "1")
    r = upload(client, mp4(size=3_000_000))
    assert r.status_code == 413 and media["videos"] == []


def test_video_without_a_recipe(client, media):
    media["video_recipe"] = None
    r = upload(client, mp4())
    assert r.status_code == 422 and r.json()["detail"]["code"] == "no_recipe"


def test_failed_parses_are_not_cached(client, media):
    media["video_recipe"] = None
    data = mp4()
    assert upload(client, data).status_code == 422
    media["video_recipe"] = RECIPE
    assert upload(client, data).status_code == 200


def test_pantry_and_servings_form_fields(client, media):
    base = upload(client, mp4(), servings="4").json()
    r = upload(client, mp4(), servings="8", pantry="onion, salt").json()
    assert "onion" in r["skipped_pantry"] and all(i["ingredient"] != "onion" for i in r["items"])
    dal4 = next(i for i in base["items"] if i["ingredient"] == "toor dal")
    dal8 = next(i for i in r["items"] if i["ingredient"] == "toor dal")
    assert dal8["needed"] != dal4["needed"]  # scaled for 8 servings


def test_bad_servings_is_rejected(client, media):
    assert upload(client, mp4(), servings="0").status_code == 422


def test_upload_respects_the_daily_cap(client, media, monkeypatch):
    monkeypatch.setenv("VIDEO_MAX_PER_DAY", "1")
    assert upload(client, mp4(size=10)).status_code == 200
    assert upload(client, mp4(size=11)).status_code == 429


def test_upload_works_even_when_download_is_disabled(client, media):
    assert upload(client, mp4()).status_code == 200  # the flag only gates link downloads


def test_upload_temp_files_are_removed(client, media, monkeypatch):
    seen = []
    original = main.parse_recipe_video

    def spy(path):
        seen.append(Path(path))
        return original(path)

    monkeypatch.setattr(main, "parse_recipe_video", spy)
    upload(client, mp4())
    assert seen and all(not p.exists() for p in seen)


def test_audio_only_m4a_is_labelled_as_audio(client, media):
    # an audio-only MP4 ("M4A " brand) is accepted but must not be mislabelled as video
    assert upload(client, mp4(brand=b"M4A "), name="x.m4a").status_code == 200
    assert media["videos"][-1] == "upload.m4a"


# --- friendly errors -------------------------------------------------------

def _gemini_400(msg):
    from google.genai import errors

    return errors.ClientError(400, {"error": {"message": msg, "status": "INVALID_ARGUMENT"}})


def test_corrupt_media_gives_a_friendly_422(client, media, monkeypatch):
    def boom(path):
        raise _gemini_400("Unable to process input: the file or media data is empty, corrupted, or malformed.")

    monkeypatch.setattr(main, "parse_recipe_video", boom)
    r = upload(client, mp4())
    assert r.status_code == 422 and "corrupt or in an unsupported format" in r.json()["detail"]


def test_a_bad_api_key_is_not_reported_as_a_corrupt_file(client, media, monkeypatch):
    def boom(path):
        raise _gemini_400("API key not valid. Please pass a valid API key.")

    monkeypatch.setattr(main, "parse_recipe_video", boom)
    r = upload(client, mp4())
    assert r.status_code == 503 and "API key not valid" in r.json()["detail"]
