"""订单路由。"""

from fastapi import APIRouter

from app.models import Order, OrderItem
from app.services.order import OrderService

router = APIRouter(prefix="/orders", tags=["orders"])

service = OrderService()


@router.get("/{order_id}")
def get_order(order_id: int) -> dict:
    order = Order(id=order_id)
    items = [OrderItem(id=1, order_id=order_id, product_id=1, quantity=2, unit_price=25.0)]
    return service.checkout(order, items)


@router.get("/{order_id}/shipping")
def get_shipping(order_id: int) -> dict:
    items = [OrderItem(id=1, order_id=order_id, product_id=1, quantity=1, unit_price=25.0)]
    return {"order_id": order_id, "shipping": service.shipping(items)}