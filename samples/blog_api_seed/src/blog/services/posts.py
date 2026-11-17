"""Post 业务服务：通过 Depends 注入的 Session 访问模型。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Post


class PostService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, post_id: int) -> Post | None:
        return self.db.get(Post, post_id)

    def list(self) -> list[Post]:
        return list(self.db.scalars(select(Post)).all())

    def title_of(self, post_id: int) -> str:
        post = self.get(post_id)
        return post.title if post else ""

    def excerpt(self, post_id: int) -> str:
        post = self.get(post_id)
        if not post:
            return ""
        return " ".join(post.body.split()[:8])