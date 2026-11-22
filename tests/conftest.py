import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session, read_seed_file, seed_products
from app.main import app


@pytest.fixture
def engine():
    """In-memory SQLite so tests never need a Postgres server."""
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def seeded_engine(engine):
    with Session(engine) as s:
        seed_products(s, read_seed_file())
    return engine


@pytest.fixture
def client_factory():
    """Build a TestClient whose DB session comes from the given engine."""

    def make(engine) -> TestClient:
        def override():
            with Session(engine) as session:
                yield session

        app.dependency_overrides[get_session] = override
        return TestClient(app)

    yield make
    app.dependency_overrides.clear()
