"""FastAPI 应用装配：路由、错误体映射与存储初始化。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from services.api import deps
from services.api.auth import auth_middleware
from services.api.routes_changesets import router as changesets_router
from services.api.routes_repos import router as repos_router
from services.api.routes_report import router as report_router
from services.api.routes_tasks import router as tasks_router
from services.api.settings import get_settings

logger = logging.getLogger("changeproof.api")

_HTTP_CODES = {404: "not_found", 422: "invalid_request", 405: "method_not_allowed"}


def _error_body(code: str, message: str, detail: Any | None = None) -> dict[str, Any]:
    return {"code": code, "message": message, "detail": detail}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    deps.init_storage()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="ChangeProof API", version="1.0.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.middleware("http")(auth_middleware)

    app.include_router(repos_router)
    app.include_router(changesets_router)
    app.include_router(tasks_router)
    app.include_router(report_router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        try:
            isolation_mode = deps.get_executor().mode
        except Exception:
            logger.warning("执行器不可用，健康检查回退为 local", exc_info=True)
            isolation_mode = "local"
        return {
            "status": "ok",
            "storage": "sqlite",
            "isolation_mode": isolation_mode,
            "llm": settings.llm,
        }

    _register_error_handlers(app)
    return app


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(deps.ApiError)
    async def api_error_handler(request: Request, exc: deps.ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.code, exc.message, exc.detail),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_error_body("invalid_request", "请求参数校验失败", jsonable_encoder(exc.errors())),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        message = exc.detail if isinstance(exc.detail, str) else "请求失败"
        return JSONResponse(status_code=exc.status_code, content=_error_body(code, message))

    @app.exception_handler(Exception)
    async def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("未处理异常 path=%s", request.url.path)
        return JSONResponse(status_code=500, content=_error_body("internal_error", "内部服务错误"))


app = create_app()