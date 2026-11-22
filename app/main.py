import hashlib
import tempfile
from pathlib import Path

from dotenv import load_dotenv

# Load .env before anything reads DATABASE_URL / API keys.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402
from sqlmodel import Session  # noqa: E402

from . import video_fetch  # noqa: E402
from .admin import router as catalogue_router  # noqa: E402
from .db import get_session, load_catalogue  # noqa: E402
from .dishcache import parse_cached  # noqa: E402
from .limits import MediaLimit, media_slot  # noqa: E402
from .matcher import build_cart  # noqa: E402
from .parser import (  # noqa: E402
    NoRecipeError,
    compose_post,
    current_model,
    is_builtin,
    parse_dish,
    parse_recipe_text,
    parse_recipe_video,
    transcribe,
)
from .reel import ReelRef, fetch_caption, find_reel  # noqa: E402
from .schemas import CartRequest, CartResponse, ParsedDish, Product  # noqa: E402
from .video_fetch import download_reel  # noqa: E402

app = FastAPI(title="Recipe Cart")
app.include_router(catalogue_router)

STATIC = Path(__file__).resolve().parent / "static"
CHUNK = 1024 * 1024


def get_catalogue(session: Session = Depends(get_session)) -> list[Product]:
    try:
        catalogue = load_catalogue(session)
    except SQLAlchemyError as e:
        raise HTTPException(status_code=503, detail=f"Catalogue database unavailable: {e.__class__.__name__}")
    if not catalogue:
        raise HTTPException(status_code=503, detail="Catalogue is empty - run `python -m app.seed`.")
    return catalogue


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/catalogue", include_in_schema=False)
def catalogue_page() -> FileResponse:
    return FileResponse(STATIC / "catalogue.html")


class NeedsCaption(Exception):
    """We couldn't get the recipe from the reel (login wall / nothing usable in it)."""


def to_http(e: Exception) -> HTTPException:
    """One place that maps pipeline errors to HTTP responses."""
    if isinstance(e, HTTPException):
        return e
    if isinstance(e, NeedsCaption):
        return HTTPException(422, {
            "code": "needs_caption",
            "message": f"{e} Paste the caption (the recipe text) below, or upload the video.",
            "can_upload_video": True,
        })
    if isinstance(e, NoRecipeError):
        return HTTPException(422, {"code": "no_recipe", "message": str(e)})
    if isinstance(e, MediaLimit):
        return HTTPException(e.status, e.message)
    if isinstance(e, LookupError):
        return HTTPException(404, str(e))
    if isinstance(e, ValueError):
        return HTTPException(502, str(e))
    if getattr(e, "code", None) == 400 and "api key" not in str(e).lower():
        # Gemini rejected the media itself (corrupt / unsupported). A bad API key is also
        # a 400, so that case keeps the raw message below.
        return HTTPException(422, "The AI couldn't read that file - it may be corrupt or in an unsupported format.")
    return HTTPException(503, f"Parser unavailable: {e}")  # missing API key, network, ...


def _text_key(text: str) -> str:
    return "text:" + hashlib.sha1(" ".join(text.lower().split()).encode()).hexdigest()[:20]


def recipe_from_media(ref: ReelRef, caption: str | None) -> ParsedDish | None:
    """Optional, flag-gated: download the reel's audio, transcribe it, and parse
    caption + transcript. If the transcript has no recipe (silent reels with text
    overlays), fall back to the video. Returns None if nothing usable was found."""
    with media_slot(), tempfile.TemporaryDirectory(prefix="rc-") as tmp:
        work = Path(tmp)
        got = download_reel(ref, work, audio_only=True)
        if got is None:
            return None
        if not got.is_video:  # a real audio stream: cheap path first
            transcript = transcribe(got.path)
            if transcript:
                try:
                    return parse_recipe_text(compose_post(caption, transcript))
                except NoRecipeError:
                    pass
            if not video_fetch.video_fallback_enabled():
                return None
            (work / "video").mkdir()
            got = download_reel(ref, work / "video", audio_only=False)
            if got is None:
                return None
        try:  # muxed file from the first request, or the fallback download
            return parse_recipe_video(got.path)
        except NoRecipeError:
            return None


def resolve_recipe(req: CartRequest, session: Session) -> tuple[ParsedDish, str]:
    """Dish name -> parse by name; reel link and/or pasted text -> parse the text."""
    ref = find_reel(req.dish)
    pasted = (req.caption or "").strip() or None

    if ref is None and pasted is None:
        parsed, _ = parse_cached(
            session, req.dish, parse_dish,
            refresh=req.refresh, is_builtin=is_builtin, model=current_model(),
        )
        return parsed, "dish"

    def from_text(_: str) -> ParsedDish:
        caption = pasted
        if caption is None:  # reel link only: try the public caption
            caption = fetch_caption(ref)
        if caption:
            try:
                return parse_recipe_text(caption)
            except NoRecipeError:
                if pasted is not None:
                    raise  # the user gave us the text and there's no recipe in it
        if pasted is None and video_fetch.enabled():
            parsed = recipe_from_media(ref, caption)
            if parsed is not None:
                return parsed
        if caption:
            raise NeedsCaption("The reel's caption doesn't seem to contain a recipe.")
        raise NeedsCaption("Instagram wouldn't show us that reel's caption.")

    key = ref.key if ref else _text_key(pasted)
    parsed, _ = parse_cached(
        session, req.dish, from_text, refresh=req.refresh, key=key, model=current_model()
    )
    return parsed, "instagram" if ref else "text"


def finish_cart(parsed: ParsedDish, source: str, servings: int, pantry: list[str], catalogue: list[Product]) -> CartResponse:
    cart = build_cart(parsed, servings, pantry, catalogue)
    cart.source = source
    return cart


@app.post("/cart", response_model=CartResponse)
def create_cart(
    req: CartRequest,
    catalogue: list[Product] = Depends(get_catalogue),
    session: Session = Depends(get_session),
) -> CartResponse:
    try:
        parsed, source = resolve_recipe(req, session)
    except Exception as e:
        raise to_http(e)
    return finish_cart(parsed, source, req.servings, req.pantry, catalogue)


# --- video upload ----------------------------------------------------------

def _sniff_extension(head: bytes) -> str | None:
    """File type from magic bytes (never trust the filename or Content-Type)."""
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand == b"qt  ":
            return ".mov"
        if brand in (b"M4A ", b"M4B "):  # audio-only MP4: label it correctly for Gemini
            return ".m4a"
        return ".mp4"
    if head[:4] == bytes.fromhex("1a45dfa3"):  # Matroska / WebM
        return ".webm"
    return None


def _save_upload(upload: UploadFile, dest: Path) -> tuple[Path, str]:
    """Stream to disk, hashing as we go. Returns (path, short sha256)."""
    limit = video_fetch.max_mb() * 1024 * 1024
    first = upload.file.read(CHUNK)
    ext = _sniff_extension(first[:16])
    if ext is None:
        raise HTTPException(415, "That doesn't look like a video file (try an .mp4 or .mov).")
    path = dest / f"upload{ext}"
    digest, size, chunk = hashlib.sha256(), 0, first
    with path.open("wb") as f:
        while chunk:
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, f"Video is larger than {video_fetch.max_mb()} MB.")
            digest.update(chunk)
            f.write(chunk)
            chunk = upload.file.read(CHUNK)
    return path, digest.hexdigest()[:20]


@app.post("/cart/video", response_model=CartResponse)
def create_cart_from_video(
    request: Request,
    file: UploadFile = File(...),
    servings: int = Form(4),
    pantry: str = Form(""),
    refresh: bool = Form(False),
    catalogue: list[Product] = Depends(get_catalogue),
    session: Session = Depends(get_session),
) -> CartResponse:
    if servings < 1:
        raise HTTPException(422, "servings must be at least 1.")
    try:
        declared = int(request.headers.get("content-length", 0))
        if declared > (video_fetch.max_mb() + 1) * 1024 * 1024:  # reject before reading it all
            raise HTTPException(413, f"Video is larger than {video_fetch.max_mb()} MB.")
        with tempfile.TemporaryDirectory(prefix="rc-up-") as tmp:
            path, digest = _save_upload(file, Path(tmp))
            key = "vid:" + digest

            def parse(_: str) -> ParsedDish:
                with media_slot():
                    return parse_recipe_video(path)

            parsed, _ = parse_cached(
                session, key, parse, refresh=refresh, key=key, model=current_model()
            )
    except Exception as e:
        raise to_http(e)
    items = [p.strip() for p in pantry.split(",") if p.strip()]
    return finish_cart(parsed, "video", servings, items, catalogue)
