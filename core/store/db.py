"""引擎与会话工厂：SQLite 为唯一实现，PostgreSQL 仅保留连接串入口。"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .models import Base


def create_engine_and_session(db_url: str) -> tuple[Engine, sessionmaker[Session]]:
    url = make_url(db_url)
    kwargs: dict[str, object] = {}

    if url.get_backend_name() == "sqlite":
        # API 的请求线程与 worker 共用同一引擎，需关闭 sqlite3 的线程归属检查。
        kwargs["connect_args"] = {"check_same_thread": False}
        database = url.database
        if not database or database == ":memory:":
            # 内存库默认按线程分配连接，这里固定单连接以保证可见性。
            kwargs["poolclass"] = StaticPool
        else:
            Path(database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(db_url, **kwargs)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return engine, session_factory


def init_schema(engine: Engine) -> None:
    Base.metadata.create_all(engine)