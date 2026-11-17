"""SQLAlchemy 模型：客户、商品、订单、订单项与优惠券。"""

from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(default="")
    email: Mapped[str] = mapped_column(default="")
    tier: Mapped[str] = mapped_column(default="basic")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(default="")
    name: Mapped[str] = mapped_column(default="")
    price: Mapped[float] = mapped_column(default=0.0)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(default="created")
    coupon_code: Mapped[str | None] = mapped_column(default=None)
    total: Mapped[float] = mapped_column(default=0.0)


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(default=0)
    product_id: Mapped[int] = mapped_column(default=0)
    quantity: Mapped[int] = mapped_column(default=1)
    unit_price: Mapped[float] = mapped_column(default=0.0)


class Coupon(Base):
    __tablename__ = "coupons"

    code: Mapped[str] = mapped_column(primary_key=True)
    percent: Mapped[float] = mapped_column(default=0.0)
    active: Mapped[bool] = mapped_column(default=True)