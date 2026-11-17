import pytest

from app.core.pricing import apply_tax, compute_discount, shipping_fee


def test_compute_discount():
    assert compute_discount(200.0, 0.25) == 50.0
    with pytest.raises(ValueError):
        compute_discount(100.0, 1.5)


def test_apply_tax():
    assert apply_tax(100.0, 0.1) == 110.0
    with pytest.raises(ValueError):
        apply_tax(100.0, -0.1)


def test_shipping_fee_free():
    assert shipping_fee(250.0, 200.0) == 0.0


def test_shipping_fee_paid():
    assert shipping_fee(25.0, 200.0) == 12.0