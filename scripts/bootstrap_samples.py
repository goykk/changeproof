"""受控多样本评测语料生成器（C-SAMPLE-V2@1）。

从 ``samples/shop_api_seed/`` 幂等重建 ``var/samples/shop_api``：
``git init`` → 基线提交并打 tag ``v1-baseline`` → 每个样本从基线派生分支、
注入一次显式声明的变更并打 tag ``sample/<sample_id>``，同时写出
``eval/samples/<sample_id>.json`` 与 ``eval/samples/lock.json``。

真值只来自本文件里显式声明的注入点与人工核对的用例，不由运行期预测结果回填。
提交 sha 每次重建都会变化，任何代码都不得写死 sha，只能从 lock.json 读取。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

SEED_DIRNAME = "samples/shop_api_seed"
REPO_NAME = "shop_api"
REPO_MARKER = "var/samples/shop_api"
BASE_REF = "v1-baseline"
SAMPLE_REF_PREFIX = "sample"
DEFAULT_HIGH_RISK_GLOBS = ("app/core/**",)

CHANGE_KINDS = (
    "rename_field",
    "rename_function",
    "signature_change",
    "route_param",
    "shared_util",
    "config",
    "comment_only",
    "cross_module",
)


@dataclass(frozen=True)
class SampleSpec:
    """一个样本 = 基线之上的一次显式变更 + 人工核对的影响面与失败用例。"""

    sample_id: str
    change_kind: str
    edits: tuple[tuple[str, str, str], ...]
    expected_impact_files: tuple[str, ...]
    expected_impact_symbols: tuple[str, ...]
    expected_failing_tests: tuple[str, ...] = ()
    expected_no_behavior_change: bool = False
    high_risk_globs: tuple[str, ...] = DEFAULT_HIGH_RISK_GLOBS

    @property
    def target_ref(self) -> str:
        return f"{SAMPLE_REF_PREFIX}/{self.sample_id}"

    @property
    def branch_name(self) -> str:
        # 分支名与 tag 名分离，避免 git ref 同名歧义（target_ref 指 tag）
        return f"{SAMPLE_REF_PREFIX}_work/{self.sample_id}"


SAMPLES: tuple[SampleSpec, ...] = (
    # ---- rename_field ----
    SampleSpec(
        sample_id="shop_api_rename_q1",
        change_kind="rename_field",
        edits=(
            (
                "app/models.py",
                "    total: Mapped[float] = mapped_column(default=0.0)",
                "    total_amount: Mapped[float] = mapped_column(default=0.0)",
            ),
        ),
        expected_impact_files=("app/models.py", "app/services/order.py", "app/api/orders.py"),
        expected_impact_symbols=(
            "app/models.py::Order.total",
            "app/services/order.py::OrderService.calculate_total",
        ),
        expected_failing_tests=("tests/test_orders.py::test_order_total",),
    ),
    SampleSpec(
        sample_id="rename_field_product_price",
        change_kind="rename_field",
        edits=(
            (
                "app/models.py",
                "    price: Mapped[float] = mapped_column(default=0.0)",
                "    unit_price: Mapped[float] = mapped_column(default=0.0)",
            ),
            (
                "app/services/catalog.py",
                "        return round_money(product.price)",
                "        return round_money(product.unit_price)",
            ),
            (
                "app/api/products.py",
                '        Product(id=1, sku="SKU-1", name="Mug", price=19.5),',
                '        Product(id=1, sku="SKU-1", name="Mug", unit_price=19.5),',
            ),
            (
                "app/api/products.py",
                '        Product(id=2, sku="SKU-2", name="Teapot", price=89.0),',
                '        Product(id=2, sku="SKU-2", name="Teapot", unit_price=89.0),',
            ),
            (
                "app/api/products.py",
                '        {"sku": product.sku, "name": product.name, "price": product.price}',
                '        {"sku": product.sku, "name": product.name, "price": product.unit_price}',
            ),
        ),
        expected_impact_files=("app/models.py", "app/services/catalog.py", "app/api/products.py"),
        expected_impact_symbols=(
            "app/models.py::Product.price",
            "app/services/catalog.py::ProductService.price_of",
        ),
        expected_failing_tests=(
            "tests/test_models.py::test_product_price_default",
            "tests/test_services_catalog.py::test_add_and_get",
            "tests/test_services_catalog.py::test_price_of",
        ),
    ),
    SampleSpec(
        sample_id="rename_field_order_coupon_code",
        change_kind="rename_field",
        edits=(
            (
                "app/models.py",
                "    coupon_code: Mapped[str | None] = mapped_column(default=None)",
                "    promotion_code: Mapped[str | None] = mapped_column(default=None)",
            ),
            (
                "app/services/order.py",
                "        rate = self.coupons.rate_for(order.coupon_code)",
                "        rate = self.coupons.rate_for(order.promotion_code)",
            ),
        ),
        expected_impact_files=("app/models.py", "app/services/order.py"),
        expected_impact_symbols=(
            "app/models.py::Order.coupon_code",
            "app/services/order.py::OrderService.discount",
        ),
        expected_failing_tests=("tests/test_orders.py::test_discount_with_coupon",),
    ),
    SampleSpec(
        sample_id="rename_field_api_price_key",
        change_kind="rename_field",
        edits=(
            (
                "app/api/products.py",
                '"price": product.price}',
                '"unit_price": product.price}',
            ),
            (
                "app/api/products.py",
                '"price": catalog.price_of(sku)}',
                '"unit_price": catalog.price_of(sku)}',
            ),
        ),
        expected_impact_files=("app/api/products.py",),
        expected_impact_symbols=("app/api/products.py::list_products",),
        expected_failing_tests=(
            "tests/test_api_products.py::test_list_products",
            "tests/test_api_products.py::test_get_product",
        ),
    ),
    SampleSpec(
        sample_id="rename_field_customer_email",
        change_kind="rename_field",
        edits=(
            (
                "app/models.py",
                '    email: Mapped[str] = mapped_column(default="")',
                '    contact_email: Mapped[str] = mapped_column(default="")',
            ),
            (
                "app/services/customers.py",
                "            email=validate_email(email),",
                "            contact_email=validate_email(email),",
            ),
            (
                "app/services/customers.py",
                "            if customer.email == normalized:",
                "            if customer.contact_email == normalized:",
            ),
            (
                "app/api/customers.py",
                '"email": customer.email, "tier": customer.tier, "discount"',
                '"email": customer.contact_email, "tier": customer.tier, "discount"',
            ),
            (
                "app/api/customers.py",
                '    return {"id": customer.id, "email": customer.email, "tier": customer.tier}\n',
                '    return {"id": customer.id, "email": customer.contact_email, "tier": customer.tier}\n',
            ),
        ),
        expected_impact_files=("app/models.py", "app/services/customers.py", "app/api/customers.py"),
        expected_impact_symbols=(
            "app/models.py::Customer.email",
            "app/services/customers.py::CustomerService.find",
        ),
        expected_failing_tests=("tests/test_services_customers.py::test_register_normalizes_email",),
    ),
    # ---- rename_function ----
    SampleSpec(
        sample_id="rename_function_price_of",
        change_kind="rename_function",
        edits=(
            (
                "app/services/catalog.py",
                "    def price_of(self, sku: str) -> float:",
                "    def price_for(self, sku: str) -> float:",
            ),
            (
                "app/api/products.py",
                "catalog.price_of(sku)}",
                "catalog.price_for(sku)}",
            ),
        ),
        expected_impact_files=("app/services/catalog.py", "app/api/products.py"),
        expected_impact_symbols=("app/services/catalog.py::ProductService.price_of",),
        expected_failing_tests=(
            "tests/test_services_catalog.py::test_price_of",
            "tests/test_services_catalog.py::test_price_of_unknown",
        ),
    ),
    SampleSpec(
        sample_id="rename_function_order_discount",
        change_kind="rename_function",
        edits=(
            (
                "app/services/order.py",
                "    def discount(self, order: Order, items: list[OrderItem]) -> float:",
                "    def coupon_discount(self, order: Order, items: list[OrderItem]) -> float:",
            ),
            (
                "app/services/order.py",
                "        net = self.subtotal(items) - self.discount(order, items)",
                "        net = self.subtotal(items) - self.coupon_discount(order, items)",
            ),
            (
                "app/services/order.py",
                '            "discount": self.discount(order, items),',
                '            "discount": self.coupon_discount(order, items),',
            ),
        ),
        expected_impact_files=("app/services/order.py",),
        expected_impact_symbols=("app/services/order.py::OrderService.discount",),
        expected_failing_tests=("tests/test_orders.py::test_discount_with_coupon",),
    ),
    SampleSpec(
        sample_id="rename_function_customer_discount_rate",
        change_kind="rename_function",
        edits=(
            (
                "app/services/customers.py",
                "    def discount_rate(self, customer: Customer | None) -> float:",
                "    def tier_discount_rate(self, customer: Customer | None) -> float:",
            ),
            (
                "app/api/customers.py",
                "service.discount_rate(customer)}",
                "service.tier_discount_rate(customer)}",
            ),
        ),
        expected_impact_files=("app/services/customers.py", "app/api/customers.py"),
        expected_impact_symbols=("app/services/customers.py::CustomerService.discount_rate",),
        expected_failing_tests=("tests/test_services_customers.py::test_discount_rate_by_tier",),
    ),
    SampleSpec(
        sample_id="rename_function_inventory_reserve",
        change_kind="rename_function",
        edits=(
            (
                "app/services/inventory.py",
                "    def reserve(self, sku: str, quantity: int) -> int:",
                "    def hold(self, sku: str, quantity: int) -> int:",
            ),
        ),
        expected_impact_files=("app/services/inventory.py", "app/api/products.py"),
        expected_impact_symbols=("app/services/inventory.py::InventoryService.reserve",),
        expected_failing_tests=(
            "tests/test_services_inventory.py::test_reserve",
            "tests/test_services_inventory.py::test_reserve_insufficient",
            "tests/test_api_products.py::test_reserve_stock",
        ),
    ),
    SampleSpec(
        sample_id="rename_function_coupon_deactivate",
        change_kind="rename_function",
        edits=(
            (
                "app/services/coupons.py",
                "    def deactivate(self, code: str) -> bool:",
                "    def disable(self, code: str) -> bool:",
            ),
        ),
        expected_impact_files=("app/services/coupons.py",),
        expected_impact_symbols=("app/services/coupons.py::CouponService.deactivate",),
        expected_failing_tests=("tests/test_services_coupons.py::test_deactivate",),
    ),
    # ---- signature_change ----
    SampleSpec(
        sample_id="signature_change_apply_tax_floor",
        change_kind="signature_change",
        edits=(
            (
                "app/core/pricing.py",
                "def apply_tax(amount: float, rate: float) -> float:\n"
                "    if rate < 0:\n"
                '        raise ValueError("rate out of range")\n'
                "    return round_money(amount * (1 + rate))",
                "def apply_tax(amount: float, rate: float, floor_zero: bool) -> float:\n"
                "    if rate < 0:\n"
                '        raise ValueError("rate out of range")\n'
                "    taxed = round_money(amount * (1 + rate))\n"
                "    return max(taxed, 0.0) if floor_zero else taxed",
            ),
            (
                "app/services/order.py",
                "        return apply_tax(net, tax_rate)",
                "        return apply_tax(net, tax_rate, True)",
            ),
        ),
        expected_impact_files=("app/core/pricing.py", "app/services/order.py"),
        expected_impact_symbols=("app/core/pricing.py::apply_tax",),
        expected_failing_tests=("tests/test_core_pricing.py::test_apply_tax",),
    ),
    SampleSpec(
        sample_id="signature_change_checkout_tax_rate",
        change_kind="signature_change",
        edits=(
            (
                "app/services/order.py",
                "    def checkout(self, order: Order, items: list[OrderItem], tax_rate: float = DEFAULT_TAX_RATE) -> dict:",
                "    def checkout(self, order: Order, items: list[OrderItem], tax_rate: float) -> dict:",
            ),
            (
                "app/api/orders.py",
                "from app.models import Order, OrderItem",
                "from app.config import DEFAULT_TAX_RATE\n\nfrom app.models import Order, OrderItem",
            ),
            (
                "app/api/orders.py",
                "    return service.checkout(order, items)",
                "    return service.checkout(order, items, DEFAULT_TAX_RATE)",
            ),
        ),
        expected_impact_files=("app/services/order.py", "app/api/orders.py"),
        expected_impact_symbols=("app/services/order.py::OrderService.checkout",),
        expected_failing_tests=("tests/test_orders.py::test_checkout_summary",),
    ),
    SampleSpec(
        sample_id="signature_change_customer_register_tier",
        change_kind="signature_change",
        edits=(
            (
                "app/services/customers.py",
                '    def register(self, name: str, email: str, tier: str = "basic") -> Customer:',
                "    def register(self, name: str, email: str, tier: str) -> Customer:",
            ),
        ),
        expected_impact_files=("app/services/customers.py", "app/api/customers.py"),
        expected_impact_symbols=("app/services/customers.py::CustomerService.register",),
        expected_failing_tests=("tests/test_services_customers.py::test_register_normalizes_email",),
    ),
    SampleSpec(
        sample_id="signature_change_split_amount_mode",
        change_kind="signature_change",
        edits=(
            (
                "app/core/money.py",
                "def split_amount(amount: float, parts: int) -> list[float]:\n"
                "    if parts <= 0:\n"
                '        raise ValueError("parts must be positive")\n'
                "    share = round_money(amount / parts)\n"
                "    return [share for _ in range(parts)]",
                "def split_amount(amount: float, parts: int, *, mode: str) -> list[float]:\n"
                "    if parts <= 0:\n"
                '        raise ValueError("parts must be positive")\n'
                '    if mode not in {"even", "remainder_first"}:\n'
                '        raise ValueError("unknown mode")\n'
                "    share = round_money(amount / parts)\n"
                "    return [share for _ in range(parts)]",
            ),
        ),
        expected_impact_files=("app/core/money.py",),
        expected_impact_symbols=("app/core/money.py::split_amount",),
        expected_failing_tests=("tests/test_core_money.py::test_split_amount",),
    ),
    SampleSpec(
        sample_id="signature_change_coupon_rate_kwonly",
        change_kind="signature_change",
        edits=(
            (
                "app/services/coupons.py",
                "    def rate_for(self, code: str | None) -> float:",
                "    def rate_for(self, *, code: str | None) -> float:",
            ),
        ),
        expected_impact_files=("app/services/coupons.py", "app/services/order.py"),
        expected_impact_symbols=("app/services/coupons.py::CouponService.rate_for",),
        expected_failing_tests=(
            "tests/test_services_coupons.py::test_rate_for_active",
            "tests/test_services_coupons.py::test_rate_for_missing",
            "tests/test_services_coupons.py::test_deactivate",
            "tests/test_orders.py::test_discount_with_coupon",
            "tests/test_orders.py::test_total_with_tax",
            "tests/test_orders.py::test_checkout_summary",
            "tests/test_api_orders.py::test_get_order_total",
        ),
    ),
    # ---- route_param ----
    SampleSpec(
        sample_id="route_param_product_detail_path",
        change_kind="route_param",
        edits=(
            (
                "app/api/products.py",
                '@router.get("/{sku}")',
                '@router.get("/{sku}/detail")',
            ),
        ),
        expected_impact_files=("app/api/products.py",),
        expected_impact_symbols=("app/api/products.py::get_product",),
        expected_failing_tests=("tests/test_api_products.py::test_get_product",),
    ),
    SampleSpec(
        sample_id="route_param_order_by_id_prefix",
        change_kind="route_param",
        edits=(
            (
                "app/api/orders.py",
                '@router.get("/{order_id}")',
                '@router.get("/by-id/{order_id}")',
            ),
        ),
        expected_impact_files=("app/api/orders.py",),
        expected_impact_symbols=("app/api/orders.py::get_order",),
        expected_failing_tests=(
            "tests/test_orders.py::test_order_list",
            "tests/test_api_orders.py::test_get_order_total",
        ),
    ),
    SampleSpec(
        sample_id="route_param_order_shipping_prefix",
        change_kind="route_param",
        edits=(
            (
                "app/api/orders.py",
                '@router.get("/{order_id}/shipping")',
                '@router.get("/shipping/{order_id}")',
            ),
        ),
        expected_impact_files=("app/api/orders.py",),
        expected_impact_symbols=("app/api/orders.py::get_shipping",),
        expected_failing_tests=("tests/test_api_orders.py::test_get_order_shipping",),
    ),
    SampleSpec(
        sample_id="route_param_customer_query_email",
        change_kind="route_param",
        edits=(
            (
                "app/api/customers.py",
                '@router.get("/{email}")',
                '@router.get("")',
            ),
        ),
        expected_impact_files=("app/api/customers.py",),
        expected_impact_symbols=("app/api/customers.py::get_customer",),
        expected_failing_tests=("tests/test_api_customers.py::test_get_customer",),
    ),
    SampleSpec(
        sample_id="route_param_health_version_query",
        change_kind="route_param",
        edits=(
            (
                "app/api/health.py",
                '@router.get("/health")\n'
                "def health() -> dict:\n"
                "    settings = load_settings()\n"
                '    return {"status": "ok", "currency": settings.currency}',
                '@router.get("/health")\n'
                "def health(version: str) -> dict:\n"
                "    settings = load_settings()\n"
                '    return {"status": "ok", "currency": settings.currency, "version": version}',
            ),
        ),
        expected_impact_files=("app/api/health.py",),
        expected_impact_symbols=("app/api/health.py::health",),
        expected_failing_tests=(
            "tests/test_api_health.py::test_health_ok",
            "tests/test_api_health.py::test_health_env_currency",
        ),
    ),
    # ---- shared_util ----
    SampleSpec(
        sample_id="shared_util_round_money_precision",
        change_kind="shared_util",
        edits=(
            (
                "app/core/money.py",
                "    return round(amount, 2)",
                "    return round(amount, 1)",
            ),
        ),
        expected_impact_files=("app/core/money.py",),
        expected_impact_symbols=("app/core/money.py::round_money",),
        expected_failing_tests=(
            "tests/test_core_money.py::test_round_money",
            "tests/test_core_money.py::test_parse_amount",
        ),
    ),
    SampleSpec(
        sample_id="shared_util_validate_quantity_cap",
        change_kind="shared_util",
        edits=(
            (
                "app/core/validation.py",
                "    if quantity <= 0:\n"
                '        raise ValueError("quantity must be positive")',
                "    if quantity <= 0 or quantity > 5:\n"
                '        raise ValueError("quantity must be between 1 and 5")',
            ),
        ),
        expected_impact_files=("app/core/validation.py",),
        expected_impact_symbols=("app/core/validation.py::validate_quantity",),
        expected_failing_tests=("tests/test_core_validation.py::test_validate_quantity",),
    ),
    SampleSpec(
        sample_id="shared_util_shipping_fee_amount",
        change_kind="shared_util",
        edits=(
            (
                "app/core/pricing.py",
                "    return 12.0",
                "    return 8.0",
            ),
        ),
        expected_impact_files=("app/core/pricing.py", "app/services/order.py"),
        expected_impact_symbols=("app/core/pricing.py::shipping_fee",),
        expected_failing_tests=(
            "tests/test_core_pricing.py::test_shipping_fee_paid",
            "tests/test_orders.py::test_shipping_threshold_boundary",
            "tests/test_orders.py::test_shipping_paid",
            "tests/test_api_orders.py::test_get_order_shipping",
            "tests/test_api_orders.py::test_get_order_total",
        ),
    ),
    SampleSpec(
        sample_id="shared_util_coupon_rate_cap",
        change_kind="shared_util",
        edits=(
            (
                "app/services/coupons.py",
                "        return coupon.percent",
                "        return min(coupon.percent, 0.5)",
            ),
        ),
        expected_impact_files=("app/services/coupons.py",),
        expected_impact_symbols=("app/services/coupons.py::CouponService.rate_for",),
        expected_failing_tests=("tests/test_services_coupons.py::test_rate_for_active",),
    ),
    SampleSpec(
        sample_id="shared_util_tier_discount_table",
        change_kind="shared_util",
        edits=(
            (
                "app/services/customers.py",
                'TIER_DISCOUNTS = {"basic": 0.0, "silver": 0.05, "gold": 0.1}',
                'TIER_DISCOUNTS = {"basic": 0.0, "silver": 0.05, "gold": 0.15}',
            ),
        ),
        expected_impact_files=("app/services/customers.py",),
        expected_impact_symbols=("app/services/customers.py::TIER_DISCOUNTS",),
        expected_failing_tests=("tests/test_services_customers.py::test_discount_rate_by_tier",),
    ),
    # ---- config ----
    SampleSpec(
        sample_id="config_tax_rate_default",
        change_kind="config",
        edits=(
            (
                "app/config.py",
                "DEFAULT_TAX_RATE = 0.1",
                "DEFAULT_TAX_RATE = 0.13",
            ),
        ),
        expected_impact_files=("app/config.py", "app/services/order.py"),
        expected_impact_symbols=("app/config.py::DEFAULT_TAX_RATE",),
        expected_failing_tests=(
            "tests/test_config.py::test_default_settings",
            "tests/test_orders.py::test_total_with_tax",
            "tests/test_orders.py::test_checkout_summary",
            "tests/test_api_orders.py::test_get_order_total",
        ),
    ),
    SampleSpec(
        sample_id="config_free_shipping_threshold",
        change_kind="config",
        edits=(
            (
                "app/config.py",
                "FREE_SHIPPING_THRESHOLD = 200.0",
                "FREE_SHIPPING_THRESHOLD = 150.0",
            ),
        ),
        expected_impact_files=("app/config.py", "app/services/order.py"),
        expected_impact_symbols=("app/config.py::FREE_SHIPPING_THRESHOLD",),
        expected_failing_tests=(
            "tests/test_config.py::test_default_settings",
            "tests/test_orders.py::test_shipping_threshold_boundary",
        ),
    ),
    SampleSpec(
        sample_id="config_default_currency",
        change_kind="config",
        edits=(
            (
                "app/config.py",
                'DEFAULT_CURRENCY = "CNY"',
                'DEFAULT_CURRENCY = "USD"',
            ),
        ),
        expected_impact_files=("app/config.py", "app/core/money.py", "app/api/health.py"),
        expected_impact_symbols=("app/config.py::DEFAULT_CURRENCY",),
        expected_failing_tests=(
            "tests/test_config.py::test_default_settings",
            "tests/test_core_money.py::test_format_amount",
            "tests/test_api_health.py::test_health_ok",
        ),
    ),
    SampleSpec(
        sample_id="config_currency_env_name",
        change_kind="config",
        edits=(
            (
                "app/config.py",
                '    currency = os.getenv("SHOP_CURRENCY", DEFAULT_CURRENCY)',
                '    currency = os.getenv("SHOPAPI_CURRENCY", DEFAULT_CURRENCY)',
            ),
        ),
        expected_impact_files=("app/config.py",),
        expected_impact_symbols=("app/config.py::load_settings",),
        expected_failing_tests=(
            "tests/test_config.py::test_currency_from_env",
            "tests/test_api_health.py::test_health_env_currency",
        ),
    ),
    SampleSpec(
        sample_id="config_settings_tax_override",
        change_kind="config",
        edits=(
            (
                "app/config.py",
                "    return Settings(currency=currency)",
                "    return Settings(currency=currency, tax_rate=0.2)",
            ),
        ),
        expected_impact_files=("app/config.py",),
        expected_impact_symbols=("app/config.py::load_settings",),
        expected_failing_tests=("tests/test_config.py::test_default_settings",),
    ),
    # ---- comment_only ----
    SampleSpec(
        sample_id="comment_only_pricing_docstring",
        change_kind="comment_only",
        edits=(
            (
                "app/core/pricing.py",
                '"""定价相关的纯函数：折扣、税额与运费。"""',
                '"""定价相关的纯函数：折扣、税额与运费（不含促销叠加逻辑）。"""',
            ),
        ),
        expected_impact_files=(),
        expected_impact_symbols=(),
        expected_no_behavior_change=True,
    ),
    SampleSpec(
        sample_id="comment_only_order_comment",
        change_kind="comment_only",
        edits=(
            (
                "app/services/order.py",
                "    def subtotal(self, items: list[OrderItem]) -> float:\n        total = 0.0",
                "    def subtotal(self, items: list[OrderItem]) -> float:\n"
                "        # 逐项累加后统一取整，避免中间结果反复舍入\n"
                "        total = 0.0",
            ),
        ),
        expected_impact_files=(),
        expected_impact_symbols=(),
        expected_no_behavior_change=True,
    ),
    SampleSpec(
        sample_id="comment_only_catalog_docstring",
        change_kind="comment_only",
        edits=(
            (
                "app/services/catalog.py",
                "    def price_of(self, sku: str) -> float:\n        product = self.get(sku)",
                '    def price_of(self, sku: str) -> float:\n        """返回 SKU 的展示价，未知 SKU 抛 LookupError。"""\n        product = self.get(sku)',
            ),
        ),
        expected_impact_files=(),
        expected_impact_symbols=(),
        expected_no_behavior_change=True,
    ),
    SampleSpec(
        sample_id="comment_only_config_comment",
        change_kind="comment_only",
        edits=(
            (
                "app/config.py",
                "DEFAULT_TAX_RATE = 0.1",
                "# 默认税率，基线与测试都以该字面量为准\nDEFAULT_TAX_RATE = 0.1",
            ),
        ),
        expected_impact_files=(),
        expected_impact_symbols=(),
        expected_no_behavior_change=True,
    ),
    SampleSpec(
        sample_id="comment_only_money_docstrings",
        change_kind="comment_only",
        edits=(
            (
                "app/core/money.py",
                "def round_money(amount: float) -> float:\n    return round(amount, 2)",
                'def round_money(amount: float) -> float:\n    """按分（两位小数）取整。"""\n    return round(amount, 2)',
            ),
            (
                "app/core/money.py",
                'def format_amount(amount: float, currency: str = DEFAULT_CURRENCY) -> str:\n'
                '    return f"{currency} {round_money(amount):.2f}"',
                'def format_amount(amount: float, currency: str = DEFAULT_CURRENCY) -> str:\n'
                '    """格式化为「币种 金额」的展示字符串。"""\n'
                '    return f"{currency} {round_money(amount):.2f}"',
            ),
        ),
        expected_impact_files=(),
        expected_impact_symbols=(),
        expected_no_behavior_change=True,
    ),
    # ---- cross_module ----
    SampleSpec(
        sample_id="cross_module_checkout_breakdown",
        change_kind="cross_module",
        edits=(
            (
                "app/services/order.py",
                '            "subtotal": self.subtotal(items),',
                '            "items_subtotal": self.subtotal(items),',
            ),
            (
                "app/api/orders.py",
                "    items = [OrderItem(id=1, order_id=order_id, product_id=1, quantity=2, unit_price=25.0)]\n"
                "    return service.checkout(order, items)",
                "    items = [OrderItem(id=1, order_id=order_id, product_id=1, quantity=2, unit_price=25.0)]\n"
                "    summary = service.checkout(order, items)\n"
                "    return {\n"
                '        "order_id": summary["order_id"],\n'
                '        "items_subtotal": summary["items_subtotal"],\n'
                '        "discount": summary["discount"],\n'
                '        "shipping": summary["shipping"],\n'
                '        "total": summary["total"],\n'
                "    }",
            ),
        ),
        expected_impact_files=("app/services/order.py", "app/api/orders.py"),
        expected_impact_symbols=("app/services/order.py::OrderService.checkout",),
        expected_failing_tests=("tests/test_api_orders.py::test_get_order_total",),
    ),
    SampleSpec(
        sample_id="cross_module_checkout_currency",
        change_kind="cross_module",
        edits=(
            (
                "app/config.py",
                "FREE_SHIPPING_THRESHOLD = 200.0",
                "FREE_SHIPPING_THRESHOLD = 200.0\nREPORT_CURRENCY = DEFAULT_CURRENCY",
            ),
            (
                "app/services/order.py",
                "from app.config import DEFAULT_TAX_RATE, FREE_SHIPPING_THRESHOLD",
                "from app.config import DEFAULT_TAX_RATE, FREE_SHIPPING_THRESHOLD, REPORT_CURRENCY",
            ),
            (
                "app/services/order.py",
                '            "total": self.total(order, items, tax_rate),\n        }',
                '            "total": self.total(order, items, tax_rate),\n'
                '            "currency": REPORT_CURRENCY,\n'
                "        }",
            ),
        ),
        expected_impact_files=("app/config.py", "app/services/order.py", "app/api/orders.py"),
        expected_impact_symbols=("app/services/order.py::OrderService.checkout",),
        expected_failing_tests=("tests/test_api_orders.py::test_get_order_total",),
    ),
    SampleSpec(
        sample_id="cross_module_service_fee_line",
        change_kind="cross_module",
        edits=(
            (
                "app/core/pricing.py",
                "def shipping_fee(subtotal: float, threshold: float) -> float:\n"
                "    if subtotal >= threshold:\n"
                "        return 0.0\n"
                "    return 12.0",
                "def shipping_fee(subtotal: float, threshold: float) -> float:\n"
                "    if subtotal >= threshold:\n"
                "        return 0.0\n"
                "    return 12.0\n"
                "\n"
                "\n"
                "def service_fee(amount: float) -> float:\n"
                "    return round_money(amount * 0.01)",
            ),
            (
                "app/services/order.py",
                "from app.core.pricing import apply_tax, compute_discount, shipping_fee",
                "from app.core.pricing import apply_tax, compute_discount, service_fee, shipping_fee",
            ),
            (
                "app/services/order.py",
                "        net = self.subtotal(items) - self.discount(order, items)",
                "        net = self.subtotal(items) - self.discount(order, items) - service_fee(self.subtotal(items))",
            ),
        ),
        expected_impact_files=("app/core/pricing.py", "app/services/order.py"),
        expected_impact_symbols=("app/services/order.py::OrderService.total",),
        expected_failing_tests=(
            "tests/test_orders.py::test_total_with_tax",
            "tests/test_orders.py::test_checkout_summary",
            "tests/test_api_orders.py::test_get_order_total",
        ),
    ),
    SampleSpec(
        sample_id="cross_module_order_item_default",
        change_kind="cross_module",
        edits=(
            (
                "app/models.py",
                "    quantity: Mapped[int] = mapped_column(default=1)",
                "    quantity: Mapped[int] = mapped_column(default=0)",
            ),
            (
                "app/services/order.py",
                "from app.core.pricing import apply_tax, compute_discount, shipping_fee",
                "from app.core.pricing import apply_tax, compute_discount, shipping_fee\n"
                "from app.core.validation import validate_quantity",
            ),
            (
                "app/services/order.py",
                "            total += item.unit_price * item.quantity",
                "            total += item.unit_price * validate_quantity(item.quantity)",
            ),
        ),
        expected_impact_files=("app/models.py", "app/services/order.py"),
        expected_impact_symbols=("app/models.py::OrderItem.quantity",),
        expected_failing_tests=("tests/test_models.py::test_order_item_quantity_default",),
    ),
    SampleSpec(
        sample_id="cross_module_amount_format_health",
        change_kind="cross_module",
        edits=(
            (
                "app/core/money.py",
                '    return f"{currency} {round_money(amount):.2f}"',
                '    return f"{round_money(amount):.2f} {currency}"',
            ),
            (
                "app/api/health.py",
                "from app.config import load_settings",
                "from app.config import load_settings\nfrom app.core.money import format_amount",
            ),
            (
                "app/api/health.py",
                '    return {"status": "ok", "currency": settings.currency}',
                '    return {"status": "ok", "currency": settings.currency, "sample_amount": format_amount(12.3)}',
            ),
        ),
        expected_impact_files=("app/core/money.py", "app/api/health.py"),
        expected_impact_symbols=("app/core/money.py::format_amount",),
        expected_failing_tests=(
            "tests/test_core_money.py::test_format_amount",
            "tests/test_api_health.py::test_health_ok",
        ),
    ),
)

_GIT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_AUTHOR_NAME": "ChangeProof Samples",
    "GIT_AUTHOR_EMAIL": "samples@changeproof.local",
    "GIT_COMMITTER_NAME": "ChangeProof Samples",
    "GIT_COMMITTER_EMAIL": "samples@changeproof.local",
}

_COPY_IGNORE = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc")


def _require_git() -> str:
    git = shutil.which("git")
    if not git:
        raise RuntimeError("git_unavailable")
    return git


def _git(git: str, repo: Path, *args: str) -> str:
    env = {**os.environ, **_GIT_ENV}
    completed = subprocess.run(
        [git, "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    return completed.stdout.strip()


def _force_remove(path: Path) -> None:
    def _on_error(func, target, _exc_info):  # noqa: ANN001 - shutil 回调签名固定
        os.chmod(target, stat.S_IWRITE)
        func(target)

    shutil.rmtree(path, onerror=_on_error)


def _apply_edits(repo_dir: Path, spec: SampleSpec) -> None:
    for relative, old, new in spec.edits:
        target = repo_dir / relative
        content = target.read_text(encoding="utf-8")
        if content.count(old) != 1:
            raise RuntimeError(f"edit_mismatch:{spec.sample_id}:{relative}")
        target.write_text(content.replace(old, new), encoding="utf-8", newline="\n")


def _commit_all(git: str, repo: Path, message: str, tag: str) -> str:
    _git(git, repo, "add", "-A")
    _git(git, repo, "commit", "-m", message)
    _git(git, repo, "tag", "-a", tag, "-m", tag)
    return _git(git, repo, "rev-parse", "HEAD")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _sample_payload(spec: SampleSpec) -> dict:
    return {
        "sample_id": spec.sample_id,
        "repo_name": REPO_NAME,
        "repo_path_marker": REPO_MARKER,
        "base_ref": BASE_REF,
        "target_ref": spec.target_ref,
        "change_kind": spec.change_kind,
        "expected_impact_files": list(spec.expected_impact_files),
        "expected_impact_symbols": list(spec.expected_impact_symbols),
        "expected_failing_tests": list(spec.expected_failing_tests),
        "expected_no_behavior_change": spec.expected_no_behavior_change,
        "high_risk_globs": list(spec.high_risk_globs),
    }


def build_samples(project_root: Path = PROJECT_ROOT) -> dict[str, dict[str, str]]:
    """幂等重建样例仓库与全部标注文件，返回 ``{sample_id: {base_sha, target_sha}}``。"""
    git = _require_git()
    seed_dir = project_root / SEED_DIRNAME
    if not seed_dir.is_dir():
        raise RuntimeError(f"seed_missing:{SEED_DIRNAME}")

    repo_dir = project_root / REPO_MARKER
    if repo_dir.exists():
        _force_remove(repo_dir)
    shutil.copytree(seed_dir, repo_dir, ignore=_COPY_IGNORE)

    _git(git, repo_dir, "init", "-b", "main")
    _git(git, repo_dir, "config", "user.name", "ChangeProof Samples")
    _git(git, repo_dir, "config", "user.email", "samples@changeproof.local")
    _git(git, repo_dir, "config", "core.autocrlf", "false")
    _git(git, repo_dir, "config", "commit.gpgsign", "false")
    base_sha = _commit_all(git, repo_dir, "v1-baseline: shop_api 基线", BASE_REF)

    shas: dict[str, dict[str, str]] = {}
    samples_dir = project_root / "eval" / "samples"
    for stale in samples_dir.glob("*.json"):
        stale.unlink()

    for spec in SAMPLES:
        _git(git, repo_dir, "checkout", "-B", spec.branch_name, BASE_REF)
        _apply_edits(repo_dir, spec)
        target_sha = _commit_all(
            git,
            repo_dir,
            f"{spec.change_kind}: {spec.sample_id}",
            spec.target_ref,
        )
        shas[spec.sample_id] = {"base_sha": base_sha, "target_sha": target_sha}
        _write_json(samples_dir / f"{spec.sample_id}.json", _sample_payload(spec))

    _git(git, repo_dir, "checkout", "main")

    lock = {REPO_NAME: shas}
    _write_json(samples_dir / "lock.json", lock)
    return shas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="重建 ChangeProof 多样本评测语料")
    parser.add_argument(
        "--project-root",
        default=str(PROJECT_ROOT),
        help="项目根目录（默认取脚本上级目录）",
    )
    args = parser.parse_args(argv)

    shas = build_samples(Path(args.project_root).expanduser().resolve())
    kinds: dict[str, int] = {}
    for spec in SAMPLES:
        kinds[spec.change_kind] = kinds.get(spec.change_kind, 0) + 1
    print(f"sample repo: {REPO_MARKER}")
    print(f"samples: {len(shas)}")
    for kind in CHANGE_KINDS:
        print(f"  {kind}: {kinds.get(kind, 0)}")
    print(f"lock: eval/samples/lock.json")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)