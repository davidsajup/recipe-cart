"""Database access: engine, table model and catalogue queries."""
import json
import os
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine
from sqlmodel import Field, Session, SQLModel, create_engine, select

from .schemas import Product

DEFAULT_URL = "postgresql+psycopg://recipe:recipe@localhost:5433/recipecart"
SEED = Path(__file__).resolve().parent.parent / "seed" / "products.json"


class ProductRow(SQLModel, table=True):
    __tablename__ = "products"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    pack_size: float
    unit: str  # g | ml | count
    price: float


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


@lru_cache
def get_engine() -> Engine:
    return create_engine(database_url(), pool_pre_ping=True)


def get_session():
    """FastAPI dependency."""
    with Session(get_engine()) as session:
        yield session


def load_catalogue(session: Session) -> list[Product]:
    rows = session.exec(select(ProductRow).order_by(ProductRow.id)).all()
    return [
        Product(name=r.name, pack_size=r.pack_size, unit=r.unit, price=r.price)
        for r in rows
    ]


def seed_products(session: Session, products: list[Product]) -> tuple[int, int]:
    """Upsert by name. Returns (inserted, updated)."""
    existing = {r.name: r for r in session.exec(select(ProductRow)).all()}
    inserted = updated = 0
    for p in products:
        row = existing.get(p.name)
        if row is None:
            session.add(ProductRow(**p.model_dump()))
            inserted += 1
        elif (row.pack_size, row.unit, row.price) != (p.pack_size, p.unit, p.price):
            row.pack_size, row.unit, row.price = p.pack_size, p.unit, p.price
            session.add(row)
            updated += 1
    session.commit()
    return inserted, updated


def read_seed_file() -> list[Product]:
    return [Product(**p) for p in json.loads(SEED.read_text())]
