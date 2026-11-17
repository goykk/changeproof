import pytest

from app.services.inventory import InventoryService


def test_available_default():
    assert InventoryService().available("SKU-1") == 0


def test_reserve():
    service = InventoryService({"SKU-1": 5})
    assert service.reserve("SKU-1", 2) == 3
    assert service.available("SKU-1") == 3


def test_reserve_insufficient():
    service = InventoryService({"SKU-1": 1})
    with pytest.raises(ValueError):
        service.reserve("SKU-1", 2)


def test_restock():
    service = InventoryService({"SKU-1": 1})
    assert service.restock("SKU-1", 4) == 5