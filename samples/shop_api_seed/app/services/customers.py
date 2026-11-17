"""客户服务：注册、查找与会员折扣。"""

from app.core.validation import validate_email
from app.models import Customer

TIER_DISCOUNTS = {"basic": 0.0, "silver": 0.05, "gold": 0.1}


class CustomerService:
    def __init__(self, customers: list[Customer] | None = None) -> None:
        self._customers = list(customers or [])

    def register(self, name: str, email: str, tier: str = "basic") -> Customer:
        customer = Customer(
            id=len(self._customers) + 1,
            name=name,
            email=validate_email(email),
            tier=tier,
        )
        self._customers.append(customer)
        return customer

    def find(self, email: str) -> Customer | None:
        normalized = email.strip().lower()
        for customer in self._customers:
            if customer.email == normalized:
                return customer
        return None

    def discount_rate(self, customer: Customer | None) -> float:
        if customer is None:
            return 0.0
        return TIER_DISCOUNTS.get(customer.tier, 0.0)