"""订单服务：小计、折扣、税额与运费。"""

from app.config import DEFAULT_TAX_RATE, FREE_SHIPPING_THRESHOLD
from app.core.money import round_money
from app.core.pricing import apply_tax, compute_discount, shipping_fee
from app.models import Order, OrderItem
from app.services.coupons import CouponService


class OrderService:
    def __init__(self, coupons: CouponService | None = None) -> None:
        self.coupons = coupons or CouponService()

    def calculate_total(self, order: Order) -> float:
        return round_money(order.total)

    def subtotal(self, items: list[OrderItem]) -> float:
        total = 0.0
        for item in items:
            total += item.unit_price * item.quantity
        return round_money(total)

    def discount(self, order: Order, items: list[OrderItem]) -> float:
        rate = self.coupons.rate_for(order.coupon_code)
        return compute_discount(self.subtotal(items), rate)

    def total(self, order: Order, items: list[OrderItem], tax_rate: float = DEFAULT_TAX_RATE) -> float:
        net = self.subtotal(items) - self.discount(order, items)
        return apply_tax(net, tax_rate)

    def shipping(self, items: list[OrderItem]) -> float:
        return shipping_fee(self.subtotal(items), FREE_SHIPPING_THRESHOLD)

    def checkout(self, order: Order, items: list[OrderItem], tax_rate: float = DEFAULT_TAX_RATE) -> dict:
        return {
            "order_id": order.id,
            "subtotal": self.subtotal(items),
            "discount": self.discount(order, items),
            "shipping": self.shipping(items),
            "total": self.total(order, items, tax_rate),
        }