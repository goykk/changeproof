"""健康检查路由。"""

from fastapi import APIRouter

from app.config import load_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    settings = load_settings()
    return {"status": "ok", "currency": settings.currency}