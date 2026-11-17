from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_get_order_total():
    response = client.get("/orders/7")
    assert response.status_code == 200
    assert response.json() == {
        "order_id": 7,
        "subtotal": 50.0,
        "discount": 0.0,
        "shipping": 12.0,
        "total": 55.0,
    }


def test_get_order_shipping():
    response = client.get("/orders/7/shipping")
    assert response.status_code == 200
    assert response.json() == {"order_id": 7, "shipping": 12.0}