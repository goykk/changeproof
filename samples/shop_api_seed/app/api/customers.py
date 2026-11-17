"""客户路由。"""

from fastapi import APIRouter, HTTPException

from app.services.customers import CustomerService

router = APIRouter(prefix="/customers", tags=["customers"])

service = CustomerService()


@router.post("")
def create_customer(payload: dict) -> dict:
    customer = service.register(payload["name"], payload["email"], payload.get("tier", "basic"))
    return {"id": customer.id, "email": customer.email, "tier": customer.tier}


@router.get("/{email}")
def get_customer(email: str) -> dict:
    customer = service.find(email)
    if customer is None:
        raise HTTPException(status_code=404, detail="customer_not_found")
    return {"id": customer.id, "email": customer.email, "tier": customer.tier, "discount": service.discount_rate(customer)}