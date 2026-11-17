"""最小权限控制（C-AUTH@1）。

默认关闭，保持现有演示与门禁行为不变；`CHANGEPROOF_AUTH=token` 开启后，
除 `/api/health` 外的 `/api/*` 请求必须携带 `Authorization: Bearer <token>`。
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from fastapi import Request
from fastapi.responses import JSONResponse

VALID_ROLES = frozenset({"viewer", "operator"})
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def auth_enabled() -> bool:
    return os.environ.get("CHANGEPROOF_AUTH", "").strip().lower() == "token"


def token_roles(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """解析 `CHANGEPROOF_API_TOKENS`（`token:role,token:role`），非法条目忽略。"""
    raw = (env or os.environ).get("CHANGEPROOF_API_TOKENS", "")
    roles: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        token, role = part.split(":", 1)
        token, role = token.strip(), role.strip().lower()
        if token and role in VALID_ROLES:
            roles[token] = role
    return roles


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "detail": None},
    )


async def auth_middleware(request: Request, call_next):
    if not auth_enabled():
        return await call_next(request)
    path = request.url.path
    if not path.startswith("/api/"):
        return await call_next(request)
    # 契约只豁免 GET /api/health；POST 到健康检查同样需要鉴权。
    if path == "/api/health" and request.method.upper() in {"GET", "HEAD"}:
        return await call_next(request)

    header = request.headers.get("Authorization", "")
    token = header[len("Bearer ") :].strip() if header.startswith("Bearer ") else ""
    role = token_roles().get(token)
    if role is None:
        return _error(401, "unauthorized", "缺少或无效的访问令牌")
    if request.method.upper() not in _READ_METHODS and role != "operator":
        return _error(403, "forbidden", "该操作需要 operator 角色")
    return await call_next(request)