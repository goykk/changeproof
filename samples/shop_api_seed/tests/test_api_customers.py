from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_create_customer():
    response = client.post("/customers", json={"name": "Ada", "email": " ADA@Example.COM "})
    assert response.status_code == 200
    assert response.json()["email"] == "ada@example.com"
    assert response.json()["tier"] == "basic"


def test_get_customer():
    client.post("/customers", json={"name": "Bob", "email": "bob@example.com", "tier": "silver"})
    response = client.get("/customers/bob@example.com")
    assert response.status_code == 200
    assert response.json()["tier"] == "silver"
    assert response.json()["discount"] == 0.05


def test_get_customer_missing():
    response = client.get("/customers/nobody@example.com")
    assert response.status_code == 404