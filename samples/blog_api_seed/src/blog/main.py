"""FastAPI 应用入口。"""
from fastapi import FastAPI

from .api import authors, posts
from .db import SessionLocal, init_db, seed

app = FastAPI(title="Blog API")
app.include_router(posts.router)
app.include_router(authors.router)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    db = SessionLocal()
    try:
        seed(db)
    except Exception:
        db.rollback()
    finally:
        db.close()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}