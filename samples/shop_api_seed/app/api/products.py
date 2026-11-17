"""商品路由。"""

from fastapi import APIRouter, HTTPException

from app.models import Product
from app.services.catalog import ProductService
from app.services.inventory import InventoryService

router = APIRouter(prefix="/products", tags=["products"])

STOCK = {"SKU-1": 5, "SKU-2": 1}

catalog = ProductService(
    [
        Product(id=1, sku="SKU-1", name="Mug", price=19.5),
        Product(id=2, sku="SKU-2", name="Teapot", price=89.0),
    ]
)


def _inventory() -> InventoryService:
    # 每次请求使用独立库存副本，保证重复执行同一请求结果一致
    return InventoryService(dict(STOCK))


@router.get("")
def list_products() -> list[dict]:
    return [
        {"sku": product.sku, "name": product.name, "price": product.price}
        for product in catalog.list_all()
    ]


@router.get("/{sku}")
def get_product(sku: str) -> dict:
    product = catalog.get(sku)
    if product is None:
        raise HTTPException(status_code=404, detail="product_not_found")
    return {"sku": product.sku, "name": product.name, "price": catalog.price_of(sku)}


@router.get("/{sku}/stock")
def get_stock(sku: str) -> dict:
    return {"sku": sku, "available": _inventory().available(sku)}


@router.post("/{sku}/reserve")
def reserve_stock(sku: str, quantity: int) -> dict:
    return {"sku": sku, "remaining": _inventory().reserve(sku, quantity)}