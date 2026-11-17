from app.models import Customer
from app.services.customers import CustomerService


def test_register_normalizes_email():
    service = CustomerService()
    customer = service.register("Ada", " ADA@Example.COM ")
    assert customer.email == "ada@example.com"
    assert customer.id == 1


def test_find_by_email():
    service = CustomerService()
    service.register("Ada", "ada@example.com", "silver")
    found = service.find(" ADA@Example.com ")
    assert found is not None
    assert found.tier == "silver"


def test_discount_rate_by_tier():
    service = CustomerService()
    assert service.discount_rate(Customer(tier="basic")) == 0.0
    assert service.discount_rate(Customer(tier="silver")) == 0.05
    assert service.discount_rate(Customer(tier="gold")) == 0.1
    assert service.discount_rate(None) == 0.0