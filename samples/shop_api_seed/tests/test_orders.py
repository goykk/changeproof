from app.api import orders as orders_api
from app.models import Coupon, Order, OrderItem
from app.services.coupons import CouponService
from app.services.order import OrderService


def _items(quantity: int = 2, unit_price: float = 25.0) -> list[OrderItem]:
    return [OrderItem(id=1, order_id=7, product_id=1, quantity=quantity, unit_price=unit_price)]


def test_order_total():
    order = Order(id=1, total=99.5)
    assert OrderService().calculate_total(order) == 99.5


def test_order_list():
    paths = {route.path for route in orders_api.router.routes}
    assert "/orders/{order_id}" in paths


def test_subtotal():
    assert OrderService().subtotal(_items()) == 50.0


def test_discount_with_coupon():
    coupons = CouponService([Coupon(code="SAVE10", percent=0.1, active=True)])
    order = Order(id=7, coupon_code="SAVE10")
    assert OrderService(coupons).discount(order, _items()) == 5.0


def test_total_with_tax():
    assert OrderService().total(Order(id=7), _items()) == 55.0


def test_shipping_free_threshold():
    assert OrderService().shipping(_items(quantity=10)) == 0.0


def test_shipping_threshold_boundary():
    assert OrderService().shipping(_items(quantity=1, unit_price=180.0)) == 12.0


def test_shipping_paid():
    assert OrderService().shipping(_items()) == 12.0


def test_checkout_summary():
    summary = OrderService().checkout(Order(id=7), _items())
    assert summary["order_id"] == 7
    assert summary["total"] == 55.0