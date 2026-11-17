import pytest

from app.models import Product
from app.services.catalog import ProductService


def test_add_and_get():
    service = ProductService()
    product = Product(id=1, sku="SKU-9", name="Lamp", price=42.0)
    service.add(product)
    assert service.get("SKU-9") is product
    assert len(service.list_all()) == 1


def test_price_of():
    service = ProductService([Product(id=1, sku="SKU-9", name="Lamp", price=42.004)])
    assert service.price_of("SKU-9") == 42.0


def test_price_of_unknown():
    service = ProductService()
    with pytest.raises(LookupError):
        service.price_of("MISSING")