"""Optional link -> media download for Instagram reels, via yt-dlp.

OFF by default (VIDEO_DOWNLOAD=1 to enable). This is unofficial: Instagram's terms
forbid automated downloading, it often needs a logged-in session (we send no cookies
and never handle credentials), and it breaks when Instagram changes. Every failure
returns None so the caller can fall back to paste / upload.

Safety: only a ReelRef is accepted and only its rebuilt canonical URL is passed to
yt-dlp, restricted to the Instagram extractor. Nothing user-controlled picks the host.
"""
import os
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from pathlib import Path

from .reel import ReelRef

AUDIO_FORMAT = "bestaudio[ext=m4a]/bestaudio"
VIDEO_FORMAT = "best[height<=480][ext=mp4]/best[height<=480]/worst"
AUDIO_EXTS = {".m4a", ".mp3", ".aac", ".ogg", ".opus", ".wav", ".flac"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def enabled() -> bool:
    return os.environ.get("VIDEO_DOWNLOAD", "0").strip().lower() in {"1", "true", "yes", "on"}


def video_fallback_enabled() -> bool:
    return os.environ.get("VIDEO_FALLBACK", "1").strip().lower() in {"1", "true", "yes", "on"}


def max_seconds() -> int:
    return _env_int("MAX_VIDEO_SECONDS", 180)


def max_mb() -> int:
    return _env_int("MAX_VIDEO_MB", 100)


@dataclass(frozen=True)
class Downloaded:
    path: Path
    is_video: bool  # True if the file has a picture track (even if we asked for audio)


class _Cancelled(Exception):
    pass


def build_options(workdir: Path, audio_only: bool, stop: threading.Event) -> dict:
    from yt_dlp.utils import match_filter_func

    def hook(_progress):
        if stop.is_set():  # we timed out: abort the download thread
            raise _Cancelled()

    return {
        "format": AUDIO_FORMAT if audio_only else VIDEO_FORMAT,
        "outtmpl": str(workdir / "media.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 15,
        "retries": 1,
        "max_filesize": max_mb() * 1024 * 1024,
        "allowed_extractors": ["Instagram"],
        "match_filter": match_filter_func(f"duration <= {max_seconds()} & !is_live"),
        "progress_hooks": [hook],
        # deliberately NO cookies / username / password
    }


def _pick_file(workdir: Path) -> Path | None:
    files = [p for p in workdir.iterdir() if p.is_file() and not p.name.endswith((".part", ".ytdl"))]
    return max(files, key=lambda p: p.stat().st_size) if files else None


def download_reel(
    ref: ReelRef,
    workdir: Path,
    audio_only: bool,
    *,
    timeout: float = 60.0,
    ydl_cls=None,
) -> Downloaded | None:
    """Download a reel's audio (or low-res video) into `workdir`. Never raises."""
    workdir = Path(workdir)
    stop = threading.Event()

    def run() -> Downloaded | None:
        cls = ydl_cls
        if cls is None:
            from yt_dlp import YoutubeDL as cls  # lazy: only when the feature is used
        with cls(build_options(workdir, audio_only, stop)) as ydl:
            info = ydl.extract_info(ref.url, download=True)
        path = _pick_file(workdir)
        if path is None:  # filtered out (too long / live / too big) or nothing fetched
            return None
        vcodec = None
        if isinstance(info, dict):
            reqs = info.get("requested_downloads") or [info]
            vcodec = reqs[0].get("vcodec")
        is_video = (vcodec not in (None, "none")) if vcodec is not None else path.suffix.lower() not in AUDIO_EXTS
        return Downloaded(path=path, is_video=is_video)

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(run).result(timeout=timeout)
    except FutureTimeout:
        stop.set()
        return None
    except Exception:  # login wall, 404, blocked, yt-dlp changed... all just "no media"
        return None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
