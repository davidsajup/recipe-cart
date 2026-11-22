"""Instagram reel/post links -> public caption text (best effort).

We only read the page's public Open Graph description. Instagram frequently
serves a login wall to servers instead; in that case `fetch_caption` returns
None and the app asks the user to paste the caption. We do not download video or
try to get around the login wall.

SSRF safety: the URL is parsed, the host must be instagram.com, and the URL we
fetch is rebuilt from the post id, so nothing user-controlled picks the target.
"""
import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

HOSTS = {"instagram.com", "www.instagram.com"}
USER_AGENT = "RecipeCart/1.0 (personal recipe tool)"
MAX_BYTES = 500_000

# https://www.instagram.com/reel/CODE/, /reels/CODE, /p/CODE, /tv/CODE, /user/reel/CODE
_URL = re.compile(
    r"https?://(?:www\.)?instagram\.com/(?:[A-Za-z0-9_.]+/)?(reel|reels|p|tv)/([A-Za-z0-9_-]{5,})",
    re.IGNORECASE,
)
# og:description looks like:  12 likes, 3 comments - user on May 1, 2024: "the caption"
_CAPTION = re.compile(r':\s*"(?P<c>.*)"\s*\.?\s*$', re.DOTALL)


@dataclass(frozen=True)
class ReelRef:
    kind: str  # "reel" | "p" | "tv"
    code: str

    @property
    def url(self) -> str:
        return f"https://www.instagram.com/{self.kind}/{self.code}/"

    @property
    def key(self) -> str:
        return f"ig:{self.code}"


def find_reel(text: str) -> ReelRef | None:
    """Return the first Instagram post link inside `text`, or None."""
    m = _URL.search(text or "")
    if not m:
        return None
    host = (urlparse(m.group(0)).hostname or "").lower()
    if host not in HOSTS:
        return None
    kind = m.group(1).lower()
    return ReelRef(kind="reel" if kind == "reels" else kind, code=m.group(2))


class _Meta(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.og: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        if tag != "meta":
            return
        a = dict(attrs)
        prop = a.get("property") or a.get("name")
        if prop and a.get("content") is not None:
            self.og.setdefault(prop, a["content"])


def extract_caption(page: str) -> str | None:
    """Pull the caption out of the page's og:description; None if not a real post."""
    meta = _Meta()
    meta.feed(page)
    desc = html.unescape(meta.og.get("og:description", "")).strip()
    m = _CAPTION.search(desc)
    return m.group("c").strip() or None if m else None


def fetch_caption(ref: ReelRef, client: httpx.Client | None = None) -> str | None:
    """Best-effort public caption; None on login wall, errors or no caption."""
    own = client is None
    client = client or httpx.Client(timeout=8.0, follow_redirects=False)
    try:
        resp = client.get(
            ref.url,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en"},
        )
        if resp.status_code != 200:  # 3xx redirects to login land here too
            return None
        return extract_caption(resp.text[:MAX_BYTES])
    except httpx.HTTPError:
        return None
    finally:
        if own:
            client.close()
