from blog.db import SessionLocal
from blog.models import Post


def test_post_title():
    db = SessionLocal()
    assert db.get(Post, 1).title == "ChangeProof"


def test_post_author_relationship():
    db = SessionLocal()
    assert db.get(Post, 1).author.name == "Ada"