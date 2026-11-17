from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_list_products():
    response = client.get("/products")
    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["sku"] == "SKU-1"
    assert payload[0]["price"] == 19.5
    assert payload[1]["price"] == 89.0


def test_get_product():
    response = client.get("/products/SKU-1")
    assert response.status_code == 200
    assert response.json()["price"] == 19.5


def test_get_product_missing():
    response = client.get("/products/MISSING")
    assert response.status_code == 404


def test_stock_available():
    response = client.get("/products/SKU-2/stock")
    assert response.status_code == 200
    assert response.json()["available"] == 1


def test_reserve_stock():
    response = client.post("/products/SKU-2/reserve", params={"quantity": 1})
    assert response.status_code == 200
    assert response.json()["remaining"] == 0