from app.models import Coupon, Order, OrderItem, Product


def test_product_price_default():
    assert Product.__table__.c.price.default.arg == 0.0


def test_order_status_default():
    assert Order.__table__.c.status.default.arg == "created"


def test_order_item_quantity_default():
    assert OrderItem.__table__.c.quantity.default.arg == 1


def test_coupon_active_default():
    assert Coupon.__table__.c.active.default.arg is True