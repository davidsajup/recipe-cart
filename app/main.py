from pathlib import Path

from dotenv import load_dotenv

# Load .env before anything reads DATABASE_URL / API keys.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

from fastapi import Depends, FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402
from sqlmodel import Session  # noqa: E402

from .admin import router as catalogue_router  # noqa: E402
from .db import get_session, load_catalogue  # noqa: E402
from .matcher import build_cart  # noqa: E402
from .parser import parse_dish  # noqa: E402
from .schemas import CartRequest, CartResponse, Product  # noqa: E402

app = FastAPI(title="Recipe Cart")
app.include_router(catalogue_router)

STATIC = Path(__file__).resolve().parent / "static"


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


@app.post("/cart", response_model=CartResponse)
def create_cart(req: CartRequest, catalogue: list[Product] = Depends(get_catalogue)) -> CartResponse:
    try:
        parsed = parse_dish(req.dish)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:  # missing API key, network, etc.
        raise HTTPException(status_code=503, detail=f"Parser unavailable: {e}")
    return build_cart(parsed, req.servings, req.pantry, catalogue)
