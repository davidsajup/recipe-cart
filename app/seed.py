"""Load seed/products.json into the database (idempotent upsert by name).

    python -m app.seed
"""
from dotenv import load_dotenv

from .db import SEED, Session, get_engine, read_seed_file, seed_products


def main() -> None:
    load_dotenv(SEED.parent.parent / ".env", override=True)
    with Session(get_engine()) as session:
        inserted, updated = seed_products(session, read_seed_file())
    print(f"seeded: {inserted} inserted, {updated} updated")


if __name__ == "__main__":
    main()
