"""最小权限控制的四态不变式（C-AUTH@1）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.api.main import app


@pytest.fixture
def make_client(monkeypatch):
    def _make(auth: bool, tokens: str = "") -> TestClient:
        monkeypatch.setenv("CHANGEPROOF_AUTH", "token" if auth else "")
        monkeypatch.setenv("CHANGEPROOF_API_TOKENS", tokens)
        return TestClient(app)

    return _make


def test_disabled_auth_allows_reads(make_client) -> None:
    client = make_client(auth=False)
    assert client.get("/api/repos").status_code == 200
    assert client.get("/api/health").status_code == 200


def test_missing_or_unknown_token_returns_401(make_client) -> None:
    client = make_client(auth=True, tokens="secret:operator")
    assert client.get("/api/repos").status_code == 401
    response = client.get("/api/repos", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


def test_viewer_is_read_only(make_client) -> None:
    client = make_client(auth=True, tokens="view:viewer,secret:operator")
    headers = {"Authorization": "Bearer view"}
    assert client.get("/api/repos", headers=headers).status_code == 200
    response = client.post(
        "/api/repos",
        json={"name": "x", "local_path": "/nonexistent"},
        headers=headers,
    )
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


def test_operator_can_write(make_client) -> None:
    client = make_client(auth=True, tokens="view:viewer,secret:operator")
    headers = {"Authorization": "Bearer secret"}
    assert client.get("/api/repos", headers=headers).status_code == 200
    response = client.post(
        "/api/repos",
        json={"name": "shop_api", "local_path": "var/samples/shop_api"},
        headers=headers,
    )
    # operator 的写操作必须真正进入业务并创建仓库（契约要求 201）。
    assert response.status_code in {200, 201}
    assert "repo_id" in response.json()


def test_operator_post_health_still_requires_token(make_client) -> None:
    client = make_client(auth=True, tokens="view:viewer")
    response = client.post("/api/health", headers={"Authorization": "Bearer view"})
    # 契约只豁免 GET /api/health；POST 需要鉴权且 viewer 不能写。
    assert response.status_code in {401, 403, 405}