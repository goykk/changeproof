from blog.db import SessionLocal
from blog.services.posts import PostService


def _service() -> PostService:
    return PostService(SessionLocal())


def test_title_of():
    assert _service().title_of(1) == "ChangeProof"


def test_excerpt():
    assert _service().excerpt(1) == "impact analysis"


def test_list_posts():
    assert len(_service().list()) == 1