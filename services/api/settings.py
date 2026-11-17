"""API 运行时配置：全部通过环境变量注入，默认值面向本地 SQLite 演示。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

_DEFAULT_DB_URL = "sqlite:///./var/changeproof.db"
_DEFAULT_ARTIFACTS_ROOT = "var/artifacts"
_DEFAULT_CORS_ORIGINS = "http://localhost:3000"


@dataclass(frozen=True)
class Settings:
    db_url: str = _DEFAULT_DB_URL
    artifacts_root: str = _DEFAULT_ARTIFACTS_ROOT
    llm: str = "mock"
    isolation_mode: str = "local"
    cors_origins: tuple[str, ...] = ("http://localhost:3000",)


def _choice(value: str, allowed: tuple[str, ...], default: str) -> str:
    normalized = value.strip().lower()
    return normalized if normalized in allowed else default


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    origins_raw = os.environ.get("CHANGEPROOF_CORS_ORIGINS", _DEFAULT_CORS_ORIGINS)
    origins = tuple(item.strip() for item in origins_raw.split(",") if item.strip())
    return Settings(
        db_url=os.environ.get("CHANGEPROOF_DB_URL", _DEFAULT_DB_URL),
        artifacts_root=os.environ.get("CHANGEPROOF_ARTIFACTS_ROOT", _DEFAULT_ARTIFACTS_ROOT),
        llm=_choice(os.environ.get("CHANGEPROOF_LLM", "mock"), ("mock", "ollama"), "mock"),
        isolation_mode=_choice(
            os.environ.get("CHANGEPROOF_ISOLATION_MODE", "local"), ("local", "docker"), "local"
        ),
        cors_origins=origins or ("http://localhost:3000",),
    )