"""应用级配置：默认值集中在此，便于观测 config 类变更。"""

import os
from dataclasses import dataclass

DEFAULT_TAX_RATE = 0.1
DEFAULT_CURRENCY = "CNY"
FREE_SHIPPING_THRESHOLD = 200.0


@dataclass(frozen=True)
class Settings:
    currency: str = DEFAULT_CURRENCY
    tax_rate: float = DEFAULT_TAX_RATE
    free_shipping_threshold: float = FREE_SHIPPING_THRESHOLD


def load_settings() -> Settings:
    currency = os.getenv("SHOP_CURRENCY", DEFAULT_CURRENCY)
    return Settings(currency=currency)