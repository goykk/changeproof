"""测试共享 fixture：保证数据库已建表并完成种子数据。"""
import pytest

from blog.db import SessionLocal, init_db, seed


@pytest.fixture(autouse=True)
def _seed_database():
    init_db()
    db = SessionLocal()
    try:
        seed(db)
    except Exception:
        db.rollback()
    finally:
        db.close()