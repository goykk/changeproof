"""输入校验工具。"""


def require_positive(value: float, field: str) -> float:
    if value <= 0:
        raise ValueError(f"{field} must be positive")
    return value


def validate_quantity(quantity: int) -> int:
    if not isinstance(quantity, int) or isinstance(quantity, bool):
        raise TypeError("quantity must be int")
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    return quantity


def validate_email(email: str) -> str:
    cleaned = email.strip().lower()
    if "@" not in cleaned or cleaned.startswith("@") or cleaned.endswith("@"):
        raise ValueError("invalid email")
    return cleaned