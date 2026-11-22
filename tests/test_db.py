from sqlmodel import Session

from app.db import load_catalogue, read_seed_file, seed_products
from app.schemas import Product


def test_seed_is_idempotent_and_updates(engine):
    products = read_seed_file()
    with Session(engine) as s:
        assert seed_products(s, products) == (len(products), 0)
        assert seed_products(s, products) == (0, 0)

        changed = [Product(name=products[0].name, pack_size=1, unit="g", price=1)]
        assert seed_products(s, changed) == (0, 1)
        assert load_catalogue(s)[0].price == 1
        assert len(load_catalogue(s)) == len(products)  # nothing duplicated


def test_load_catalogue_preserves_insertion_order(engine):
    products = read_seed_file()
    with Session(engine) as s:
        seed_products(s, products)
        assert [p.name for p in load_catalogue(s)] == [p.name for p in products]
