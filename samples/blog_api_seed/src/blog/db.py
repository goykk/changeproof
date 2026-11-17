"""数据库会话与初始化；提供 FastAPI `Depends(get_db)` 依赖。"""
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .models import Author, Base, Comment, Post

# SQLite 内存库必须共享同一连接，否则 TestClient 的请求线程拿到的是空库。
engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False)


def init_db() -> None:
    Base.metadata.create_all(engine)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def seed(db: Session) -> None:
    author = Author(id=1, name="Ada")
    post = Post(id=1, title="ChangeProof", body="impact analysis", author=author)
    db.add_all([author, post, Comment(id=1, post=post, content="useful")])
    db.commit()