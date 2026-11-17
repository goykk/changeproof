"""定价相关的纯函数：折扣、税额与运费。"""

from app.core.money import round_money


def compute_discount(amount: float, rate: float) -> float:
    if rate < 0 or rate > 1:
        raise ValueError("rate out of range")
    return round_money(amount * rate)


def apply_tax(amount: float, rate: float) -> float:
    if rate < 0:
        raise ValueError("rate out of range")
    return round_money(amount * (1 + rate))


def shipping_fee(subtotal: float, threshold: float) -> float:
    if subtotal >= threshold:
        return 0.0
    return 12.0