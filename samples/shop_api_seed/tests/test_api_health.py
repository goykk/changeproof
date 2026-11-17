from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "currency": "CNY"}


def test_health_env_currency(monkeypatch):
    monkeypatch.setenv("SHOP_CURRENCY", "EUR")
    response = client.get("/health")
    assert response.json()["currency"] == "EUR"