import httpx
import pytest

from app.reel import ReelRef, extract_caption, fetch_caption, find_reel


@pytest.mark.parametrize(
    "text,kind,code",
    [
        ("https://www.instagram.com/reel/C9abcDEF123/", "reel", "C9abcDEF123"),
        ("https://instagram.com/reel/C9abcDEF123", "reel", "C9abcDEF123"),
        ("https://www.instagram.com/reels/C9abcDEF123/?igsh=xyz&utm=1", "reel", "C9abcDEF123"),
        ("https://www.instagram.com/p/Bx_1-aZ9qQ/", "p", "Bx_1-aZ9qQ"),
        ("https://www.instagram.com/tv/ABCDE12345/", "tv", "ABCDE12345"),
        ("https://www.instagram.com/somechef.here/reel/C9abcDEF123/", "reel", "C9abcDEF123"),
        ("check this out https://www.instagram.com/reel/C9abcDEF123/ so good", "reel", "C9abcDEF123"),
        ("HTTPS://WWW.INSTAGRAM.COM/REEL/C9abcDEF123/", "reel", "C9abcDEF123"),
    ],
)
def test_find_reel_accepts_instagram_links(text, kind, code):
    ref = find_reel(text)
    assert ref == ReelRef(kind=kind, code=code)
    assert ref.url == f"https://www.instagram.com/{kind}/{code}/"  # rebuilt, canonical


@pytest.mark.parametrize(
    "text",
    [
        "fish molee",
        "",
        "https://www.instagram.com/somechef/",  # profile, not a post
        "https://evil.com/reel/C9abcDEF123/",
        "https://instagram.com.evil.com/reel/C9abcDEF123/",
        "https://evil.com/?u=https://www.instagram.com.evil.io/reel/ABCDE12345",
        "http://localhost:8000/reel/C9abcDEF123/",
        "https://notinstagram.com/reel/C9abcDEF123/",
    ],
)
def test_find_reel_rejects_everything_else(text):
    assert find_reel(text) is None


def test_fetch_url_is_rebuilt_not_user_controlled():
    # a malicious query string / userinfo never reaches the request
    ref = find_reel("https://www.instagram.com/reel/C9abcDEF123/?next=http://169.254.169.254/")
    assert ref.url == "https://www.instagram.com/reel/C9abcDEF123/"


PAGE = """<html><head>
<meta property="og:title" content="Chef on Instagram">
<meta property="og:description" content="1,204 likes, 33 comments - chef on May 1, 2024: &quot;Easy dal tadka! 1 cup toor dal, 2 onions, 1 tsp cumin &amp; salt. #dal&quot;.">
</head></html>"""

LOGIN_WALL = """<html><head>
<meta property="og:description" content="Create an account or log in to Instagram - Share what you're into with the people who get you.">
</head></html>"""


def test_extract_caption_from_og_description():
    cap = extract_caption(PAGE)
    assert cap == "Easy dal tadka! 1 cup toor dal, 2 onions, 1 tsp cumin & salt. #dal"


def test_extract_caption_ignores_login_wall_and_empty_pages():
    assert extract_caption(LOGIN_WALL) is None
    assert extract_caption("<html></html>") is None


def client_returning(status=200, body=PAGE, headers=None):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, text=body, headers=headers or {})

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False), seen


REF = ReelRef(kind="reel", code="C9abcDEF123")


def test_fetch_caption_success_uses_canonical_url_and_honest_ua():
    client, seen = client_returning()
    assert fetch_caption(REF, client).startswith("Easy dal tadka")
    assert str(seen[0].url) == "https://www.instagram.com/reel/C9abcDEF123/"
    assert "RecipeCart" in seen[0].headers["user-agent"]


@pytest.mark.parametrize("status", [301, 302, 403, 404, 429, 500])
def test_fetch_caption_none_on_non_200(status):
    client, _ = client_returning(status, headers={"location": "https://www.instagram.com/accounts/login/"})
    assert fetch_caption(REF, client) is None


def test_fetch_caption_none_on_login_wall():
    client, _ = client_returning(body=LOGIN_WALL)
    assert fetch_caption(REF, client) is None


def test_fetch_caption_none_on_network_error():
    def boom(request):
        raise httpx.ConnectError("down")

    client = httpx.Client(transport=httpx.MockTransport(boom))
    assert fetch_caption(REF, client) is None
