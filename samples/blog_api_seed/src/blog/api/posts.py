"""文章路由：演示 `Depends(get_db)` 与服务注入。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..core.text import slugify
from ..db import get_db
from ..services.posts import PostService

router = APIRouter(prefix="/posts", tags=["posts"])


def service(db: Session = Depends(get_db)) -> PostService:
    return PostService(db)


@router.get("")
def list_posts(svc: PostService = Depends(service)):
    return [{"id": p.id, "title": p.title, "slug": slugify(p.title)} for p in svc.list()]


@router.get("/{post_id}")
def get_post(post_id: int, svc: PostService = Depends(service)):
    return {"id": post_id, "title": svc.title_of(post_id), "excerpt": svc.excerpt(post_id)}