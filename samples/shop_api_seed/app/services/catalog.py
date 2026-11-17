"""商品目录服务。"""

from app.core.money import round_money
from app.models import Product


class ProductService:
    def __init__(self, products: list[Product] | None = None) -> None:
        self._products = list(products or [])

    def add(self, product: Product) -> Product:
        self._products.append(product)
        return product

    def list_all(self) -> list[Product]:
        return list(self._products)

    def get(self, sku: str) -> Product | None:
        for product in self._products:
            if product.sku == sku:
                return product
        return None

    def price_of(self, sku: str) -> float:
        product = self.get(sku)
        if product is None:
            raise LookupError(f"unknown sku: {sku}")
        return round_money(product.price)