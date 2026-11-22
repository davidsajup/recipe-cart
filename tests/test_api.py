def test_index_served(client_factory, engine):
    r = client_factory(engine).get("/")
    assert r.status_code == 200
    assert "Recipe Cart" in r.text


def test_cart_endpoint_known_dish(client_factory, seeded_engine):
    client = client_factory(seeded_engine)
    r = client.post("/cart", json={"dish": "fish molee", "servings": 4, "pantry": ["salt"]})
    assert r.status_code == 200
    body = r.json()
    assert body["skipped_pantry"] == ["salt"]
    assert body["unmatched"] == []
    assert body["total"] > 0


def test_cart_uses_database_prices(client_factory, seeded_engine):
    from sqlmodel import Session, select

    from app.db import ProductRow

    client = client_factory(seeded_engine)
    with Session(seeded_engine) as s:
        milk = s.exec(select(ProductRow).where(ProductRow.name == "Coconut Milk 400ml")).one()
        milk.price = 999
        s.add(milk)
        s.commit()
    body = client.post("/cart", json={"dish": "fish molee", "servings": 4}).json()
    item = next(i for i in body["items"] if i["ingredient"] == "coconut milk")
    assert item["price"] == 999  # edit in the DB takes effect without a restart


def test_empty_catalogue_gives_503(client_factory, engine):
    r = client_factory(engine).post("/cart", json={"dish": "fish molee"})
    assert r.status_code == 503
    assert "app.seed" in r.json()["detail"]
