"""依赖装配：存储、执行器、编排触发与统一 API 异常。"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks

from core.schema import ChangesetRecord, RepositoryRecord
from core.store import SqliteStore, create_engine_and_session, init_schema
from services.api.settings import Settings, get_settings
from services.runner.executor import DockerExecutor, LocalProcessExecutor, RunnerUnavailableError
from services.worker.orchestrator import AnalyzeOptions, analyze_changeset

logger = logging.getLogger("changeproof.api")


class ApiError(Exception):
    """携带 HTTP 状态与契约错误码的业务异常。"""

    def __init__(self, status_code: int, code: str, message: str, detail: Any | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.detail = detail


@lru_cache(maxsize=1)
def get_store() -> SqliteStore:
    return SqliteStore(get_settings().db_url)


def fresh_store() -> SqliteStore:
    """后台任务使用独立实例，避免与请求处理共享状态。"""
    return SqliteStore(get_settings().db_url)


@lru_cache(maxsize=1)
def get_executor() -> Any:
    settings = get_settings()
    if settings.isolation_mode == "docker":
        try:
            return DockerExecutor()
        except RunnerUnavailableError:
            logger.warning("Docker 执行器不可用，降级为本地进程执行器")
    return LocalProcessExecutor(max_concurrency=1)


def init_storage() -> None:
    settings = get_settings()
    db_path = _sqlite_file_path(settings.db_url)
    if db_path is not None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    Path(settings.artifacts_root).mkdir(parents=True, exist_ok=True)
    engine, _ = create_engine_and_session(settings.db_url)
    try:
        init_schema(engine)
    finally:
        engine.dispose()


def _sqlite_file_path(db_url: str) -> Path | None:
    prefix = "sqlite:///"
    if not db_url.startswith(prefix):
        return None
    raw = db_url[len(prefix) :]
    if not raw or raw == ":memory:":
        return None
    return Path(raw)


def build_analyze_options(
    changeset: ChangesetRecord,
    repository: RepositoryRecord,
    generate_tests: bool,
) -> AnalyzeOptions:
    settings: Settings = get_settings()
    return AnalyzeOptions(
        repo_local_path=repository.local_path,
        base_sha=changeset.base_sha,
        target_sha=changeset.target_sha,
        generate_tests=generate_tests,
        artifacts_root=settings.artifacts_root,
    )


def schedule_analysis(
    background: BackgroundTasks,
    changeset_id: str,
    task_id: str,
    options: AnalyzeOptions,
) -> None:
    background.add_task(_run_analysis, changeset_id, task_id, options)


def _run_analysis(changeset_id: str, task_id: str, options: AnalyzeOptions) -> None:
    try:
        analyze_changeset(fresh_store(), get_executor(), changeset_id, options, task_id=task_id)
    except Exception:
        logger.exception("分析任务执行失败 changeset_id=%s task_id=%s", changeset_id, task_id)