"""库存服务。"""


class InventoryService:
    def __init__(self, stock: dict[str, int] | None = None) -> None:
        self._stock = dict(stock or {})

    def available(self, sku: str) -> int:
        return self._stock.get(sku, 0)

    def reserve(self, sku: str, quantity: int) -> int:
        available = self.available(sku)
        if quantity > available:
            raise ValueError("insufficient stock")
        self._stock[sku] = available - quantity
        return self._stock[sku]

    def restock(self, sku: str, quantity: int) -> int:
        self._stock[sku] = self.available(sku) + quantity
        return self._stock[sku]