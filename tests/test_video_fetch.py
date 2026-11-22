import time
from pathlib import Path

import pytest

from app import video_fetch
from app.reel import ReelRef
from app.video_fetch import build_options, download_reel

import threading

REF = ReelRef(kind="reel", code="C9abcDEF123")


def fake_ydl(filename="media.m4a", vcodec="none", info=True, raises=None, delay=0.0, write=True):
    """A stand-in for yt_dlp.YoutubeDL that records how it was used."""

    class FakeYDL:
        instances = []

        def __init__(self, opts):
            self.opts = opts
            self.urls = []
            FakeYDL.instances.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=True):
            self.urls.append(url)
            if delay:
                time.sleep(delay)
            if raises:
                raise raises
            if write:
                (Path(self.opts["outtmpl"]).parent / filename).write_bytes(b"x" * 100)
            return {"requested_downloads": [{"vcodec": vcodec}]} if info else None

    return FakeYDL


def test_only_the_canonical_instagram_url_is_passed(tmp_path):
    ydl = fake_ydl()
    download_reel(REF, tmp_path, audio_only=True, ydl_cls=ydl)
    assert ydl.instances[0].urls == ["https://www.instagram.com/reel/C9abcDEF123/"]


def test_options_are_restrictive_and_send_no_credentials(tmp_path):
    opts = build_options(tmp_path, audio_only=True, stop=threading.Event())
    assert opts["allowed_extractors"] == ["Instagram"]
    assert opts["noplaylist"] is True
    assert not {"cookiefile", "cookiesfrombrowser", "username", "password", "netrc"} & set(opts)


def test_audio_vs_video_format(tmp_path):
    a = build_options(tmp_path, True, threading.Event())["format"]
    v = build_options(tmp_path, False, threading.Event())["format"]
    assert a.startswith("bestaudio")
    assert "height<=480" in v


def test_size_limit_follows_env(tmp_path, monkeypatch):
    assert build_options(tmp_path, True, threading.Event())["max_filesize"] == 100 * 1024 * 1024
    monkeypatch.setenv("MAX_VIDEO_MB", "5")
    assert build_options(tmp_path, True, threading.Event())["max_filesize"] == 5 * 1024 * 1024


def test_duration_and_live_filter(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_VIDEO_SECONDS", "60")
    accept = build_options(tmp_path, True, threading.Event())["match_filter"]
    assert accept({"duration": 30, "is_live": False}) is None  # None = download it
    assert accept({"duration": 61, "is_live": False}) is not None
    assert accept({"duration": 30, "is_live": True}) is not None


def test_audio_stream_is_not_flagged_as_video(tmp_path):
    got = download_reel(REF, tmp_path, True, ydl_cls=fake_ydl("media.m4a", vcodec="none"))
    assert got.path.name == "media.m4a" and got.is_video is False


def test_muxed_file_from_an_audio_request_is_flagged_as_video(tmp_path):
    got = download_reel(REF, tmp_path, True, ydl_cls=fake_ydl("media.mp4", vcodec="avc1.4d401f"))
    assert got.is_video is True


@pytest.mark.parametrize("name,expected", [("media.mp4", True), ("media.m4a", False)])
def test_falls_back_to_extension_when_codec_unknown(tmp_path, name, expected):
    class NoInfo(fake_ydl(name)):
        def extract_info(self, url, download=True):
            super().extract_info(url, download)
            return {}  # no vcodec anywhere

    assert download_reel(REF, tmp_path, True, ydl_cls=NoInfo).is_video is expected


def test_nothing_downloaded_means_none(tmp_path):
    # e.g. the duration filter skipped it: yt-dlp returns info but writes no file
    assert download_reel(REF, tmp_path, True, ydl_cls=fake_ydl(write=False)) is None


def test_partial_files_are_ignored(tmp_path):
    assert download_reel(REF, tmp_path, True, ydl_cls=fake_ydl("media.m4a.part")) is None


@pytest.mark.parametrize("exc", [RuntimeError("login required"), ValueError("x"), KeyError("y")])
def test_any_failure_returns_none_and_never_raises(tmp_path, exc):
    assert download_reel(REF, tmp_path, True, ydl_cls=fake_ydl(raises=exc)) is None


def test_wall_clock_timeout_returns_none_promptly(tmp_path):
    start = time.monotonic()
    got = download_reel(REF, tmp_path, True, timeout=0.1, ydl_cls=fake_ydl(delay=1.0))
    assert got is None and time.monotonic() - start < 0.8


def test_progress_hook_aborts_after_timeout(tmp_path):
    stop = threading.Event()
    hook = build_options(tmp_path, True, stop)["progress_hooks"][0]
    hook({})  # fine while running
    stop.set()
    with pytest.raises(Exception):
        hook({})


@pytest.mark.parametrize("value,expected", [("1", True), ("true", True), ("YES", True), ("0", False), ("", False), ("no", False)])
def test_enabled_flag(monkeypatch, value, expected):
    monkeypatch.setenv("VIDEO_DOWNLOAD", value)
    assert video_fetch.enabled() is expected


def test_disabled_by_default():
    assert video_fetch.enabled() is False
    assert video_fetch.video_fallback_enabled() is True  # on once downloads are enabled
