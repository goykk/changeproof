from app.config import load_settings


def test_default_settings():
    settings = load_settings()
    assert settings.currency == "CNY"
    assert settings.tax_rate == 0.1
    assert settings.free_shipping_threshold == 200.0


def test_currency_from_env(monkeypatch):
    monkeypatch.setenv("SHOP_CURRENCY", "EUR")
    assert load_settings().currency == "EUR"