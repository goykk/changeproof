"""优惠券服务。"""

from app.models import Coupon


class CouponService:
    def __init__(self, coupons: list[Coupon] | None = None) -> None:
        self._coupons = {coupon.code: coupon for coupon in (coupons or [])}

    def add(self, coupon: Coupon) -> Coupon:
        self._coupons[coupon.code] = coupon
        return coupon

    def rate_for(self, code: str | None) -> float:
        if not code:
            return 0.0
        coupon = self._coupons.get(code)
        if coupon is None or not coupon.active:
            return 0.0
        return coupon.percent

    def deactivate(self, code: str) -> bool:
        coupon = self._coupons.get(code)
        if coupon is None:
            return False
        coupon.active = False
        return True