from fastapi.testclient import TestClient

from blog.main import app

client = TestClient(app)


def test_list_posts():
    body = client.get("/posts").json()
    assert body[0]["slug"] == "changeproof"


def test_get_post():
    body = client.get("/posts/1").json()
    assert body["title"] == "ChangeProof"
    assert body["excerpt"] == "impact analysis"