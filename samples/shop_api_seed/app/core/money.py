"""金额相关的纯函数工具。"""

from app.config import DEFAULT_CURRENCY


def round_money(amount: float) -> float:
    return round(amount, 2)


def parse_amount(text: str) -> float:
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("empty amount")
    return round_money(float(cleaned))


def format_amount(amount: float, currency: str = DEFAULT_CURRENCY) -> str:
    return f"{currency} {round_money(amount):.2f}"


def split_amount(amount: float, parts: int) -> list[float]:
    if parts <= 0:
        raise ValueError("parts must be positive")
    share = round_money(amount / parts)
    return [share for _ in range(parts)]