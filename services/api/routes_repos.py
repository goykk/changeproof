"""仓库导入与提交列表端点。"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Response

from core.repo import NotAGitRepositoryError, list_commits, validate_repository
from core.schema import CommitRecord, RepositoryRecord
from core.store import SqliteStore
from services.api.deps import ApiError, get_store
from services.api.schemas import (
    CommitListOut,
    CommitOut,
    RepositoryIn,
    RepositoryListOut,
    RepositoryOut,
)

logger = logging.getLogger("changeproof.api.repos")

router = APIRouter(prefix="/api", tags=["repos"])


@router.post("/repos", response_model=RepositoryOut, status_code=201)
def create_repository(
    payload: RepositoryIn,
    response: Response,
    store: SqliteStore = Depends(get_store),
) -> RepositoryOut:
    # 落库使用的是 resolve() 后的绝对路径，查重也必须用同一形态，否则相对路径二次导入会撞唯一键。
    normalized_path = str(Path(payload.local_path).expanduser().resolve())
    existing = next(
        (r for r in store.repositories.list_repositories() if r.local_path == normalized_path),
        None,
    )
    if existing is not None:
        response.status_code = 200
        return RepositoryOut.model_validate(existing)

    try:
        info = validate_repository(payload.local_path)
    except NotAGitRepositoryError as exc:
        raise ApiError(422, "not_a_git_repository", str(exc), {"local_path": payload.local_path}) from exc

    record = RepositoryRecord(
        repo_id=uuid.uuid4().hex,
        name=payload.name,
        local_path=info.local_path,
        default_branch=info.default_branch,
        status="ready",
    )
    saved = store.repositories.upsert_repository(record)

    commits = _git_commits(saved.repo_id, saved.local_path)
    if commits:
        store.commits.upsert_commits(commits)
    return RepositoryOut.model_validate(saved)


@router.get("/repos", response_model=RepositoryListOut)
def list_repositories(store: SqliteStore = Depends(get_store)) -> RepositoryListOut:
    return RepositoryListOut(
        items=[RepositoryOut.model_validate(record) for record in store.repositories.list_repositories()]
    )


@router.get("/repos/{repo_id}/commits", response_model=CommitListOut)
def list_repository_commits(
    repo_id: str,
    limit: int = Query(50, ge=1, le=500),
    store: SqliteStore = Depends(get_store),
) -> CommitListOut:
    repository = store.repositories.get_repository(repo_id)
    if repository is None:
        raise ApiError(404, "not_found", f"仓库不存在: {repo_id}")

    records = store.commits.list_commits(repo_id, limit=limit)
    if not records:
        records = _git_commits(repo_id, repository.local_path, limit=limit)
    return CommitListOut(items=[CommitOut.model_validate(record) for record in records])


def _git_commits(repo_id: str, local_path: str, limit: int = 50) -> list[CommitRecord]:
    try:
        raw = list_commits(local_path, limit=limit)
    except Exception:
        logger.warning("读取提交失败 local_path=%s", local_path, exc_info=True)
        return []
    return [
        CommitRecord(
            repo_id=repo_id,
            sha=item.sha,
            short_sha=item.short_sha,
            message=item.message,
            author=item.author,
            committed_at=item.committed_at,
        )
        for item in raw
    ]