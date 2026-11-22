import pytest

NEW = {"name": "Saffron 1g", "pack_size": 1, "unit": "g", "price": 250}


@pytest.fixture
def client(client_factory, seeded_engine):
    return client_factory(seeded_engine)


def test_list_products(client):
    r = client.get("/products")
    assert r.status_code == 200
    names = [p["name"] for p in r.json()]
    assert len(names) == 50 and names == sorted(names)


def test_create_then_cart_uses_it(client):
    r = client.post("/products", json=NEW)
    assert r.status_code == 201 and r.json()["id"]
    assert any(p["name"] == "Saffron 1g" for p in client.get("/products").json())


def test_duplicate_name_conflicts(client):
    client.post("/products", json=NEW)
    r = client.post("/products", json=NEW)
    assert r.status_code == 409


def test_update_and_effect_on_cart(client):
    milk = next(p for p in client.get("/products").json() if p["name"] == "Coconut Milk 400ml")
    r = client.put(f"/products/{milk['id']}", json={**milk, "price": 123})
    assert r.status_code == 200 and r.json()["price"] == 123
    cart = client.post("/cart", json={"dish": "fish molee", "servings": 4}).json()
    item = next(i for i in cart["items"] if i["ingredient"] == "coconut milk")
    assert item["price"] == 123


def test_update_to_existing_name_conflicts(client):
    ps = client.get("/products").json()
    a, b = ps[0], ps[1]
    r = client.put(f"/products/{a['id']}", json={**a, "name": b["name"]})
    assert r.status_code == 409


def test_delete(client):
    created = client.post("/products", json=NEW).json()
    assert client.delete(f"/products/{created['id']}").status_code == 204
    assert client.delete(f"/products/{created['id']}").status_code == 404
    assert client.put(f"/products/{created['id']}", json=NEW).status_code == 404


@pytest.mark.parametrize(
    "bad",
    [
        {**NEW, "name": "   "},
        {**NEW, "pack_size": 0},
        {**NEW, "pack_size": -5},
        {**NEW, "price": -1},
        {**NEW, "unit": "kg"},
    ],
)
def test_validation_rejects_bad_input(client, bad):
    assert client.post("/products", json=bad).status_code == 422


def test_name_is_trimmed(client):
    r = client.post("/products", json={**NEW, "name": "  Saffron 1g  "})
    assert r.json()["name"] == "Saffron 1g"


def test_admin_token_enforced_on_writes_only(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "s3cret")
    assert client.get("/products").status_code == 200  # reads stay open
    assert client.post("/products", json=NEW).status_code == 401
    assert client.post("/products", json=NEW, headers={"X-Admin-Token": "wrong"}).status_code == 401
    assert client.post("/products", json=NEW, headers={"X-Admin-Token": "s3cret"}).status_code == 201


def test_catalogue_page_served(client):
    r = client.get("/catalogue")
    assert r.status_code == 200 and "Catalogue" in r.text
