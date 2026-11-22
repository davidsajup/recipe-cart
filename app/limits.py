"""Beta-grade abuse limits for the expensive media paths (download/transcribe/video).

In-memory and per-process: fine for a small beta, not a substitute for real rate limiting.
"""
import os
import threading
from contextlib import contextmanager
from datetime import date

MAX_CONCURRENT = 2


class MediaLimit(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


_lock = threading.Lock()
_sem = threading.BoundedSemaphore(MAX_CONCURRENT)
_usage = {"day": date.today(), "n": 0}


def reset() -> None:
    """For tests."""
    global _sem
    with _lock:
        _sem = threading.BoundedSemaphore(MAX_CONCURRENT)
        _usage.update(day=date.today(), n=0)


def daily_cap() -> int:
    try:
        return int(os.environ.get("VIDEO_MAX_PER_DAY", 30))
    except ValueError:
        return 30


@contextmanager
def media_slot():
    """Hold one of MAX_CONCURRENT slots and count it against today's cap."""
    with _lock:
        today = date.today()
        if _usage["day"] != today:
            _usage.update(day=today, n=0)
        if _usage["n"] >= daily_cap():
            raise MediaLimit(429, "Daily video limit reached - try again tomorrow, or paste the caption instead.")
        if not _sem.acquire(blocking=False):
            raise MediaLimit(503, "Busy processing other videos - try again in a minute.")
        _usage["n"] += 1
    try:
        yield
    finally:
        _sem.release()
