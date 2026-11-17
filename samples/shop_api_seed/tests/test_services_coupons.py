from app.models import Coupon
from app.services.coupons import CouponService


def test_rate_for_active():
    service = CouponService([Coupon(code="BIG", percent=0.6, active=True)])
    assert service.rate_for("BIG") == 0.6


def test_rate_for_missing():
    service = CouponService()
    assert service.rate_for("NONE") == 0.0
    assert service.rate_for(None) == 0.0


def test_deactivate():
    service = CouponService([Coupon(code="BIG", percent=0.6, active=True)])
    assert service.deactivate("BIG") is True
    assert service.rate_for("BIG") == 0.0
    assert service.deactivate("NONE") is False