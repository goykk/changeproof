from fastapi import FastAPI

from app.api import customers, health, orders, products

app = FastAPI(title="shop_api")
app.include_router(health.router)
app.include_router(products.router)
app.include_router(orders.router)
app.include_router(customers.router)