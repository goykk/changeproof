"""任务状态查询与失败重试端点。"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends

from core.schema.state import MAX_RETRIES, InvalidTransitionError, TaskStatus, assert_transition
from core.store import SqliteStore
from services.api.deps import (
    ApiError,
    build_analyze_options,
    get_store,
    schedule_analysis,
)
from services.api.schemas import TaskDetailOut, TaskOut

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("/{task_id}", response_model=TaskDetailOut)
def get_task(task_id: str, store: SqliteStore = Depends(get_store)) -> TaskDetailOut:
    task = store.tasks.get_task(task_id)
    if task is None:
        raise ApiError(404, "not_found", f"任务不存在: {task_id}")
    base = TaskOut.from_record(task)
    return TaskDetailOut(**base.model_dump(), report_available=task.report_path is not None)


@router.post("/{task_id}/retry", response_model=TaskOut)
def retry_task(
    task_id: str,
    background: BackgroundTasks,
    store: SqliteStore = Depends(get_store),
) -> TaskOut:
    task = store.tasks.get_task(task_id)
    if task is None:
        raise ApiError(404, "not_found", f"任务不存在: {task_id}")
    if task.status != TaskStatus.FAILED.value:
        raise ApiError(409, "invalid_transition", "仅失败任务可重试", {"status": task.status})
    if task.retry_count >= MAX_RETRIES:
        raise ApiError(
            409,
            "retry_exhausted",
            f"重试次数已用尽（上限 {MAX_RETRIES}）",
            {"retry_count": task.retry_count, "max_retries": MAX_RETRIES},
        )

    changeset = store.changesets.get_changeset(task.changeset_id)
    if changeset is None:
        raise ApiError(404, "not_found", f"变更集不存在: {task.changeset_id}")
    repository = store.repositories.get_repository(changeset.repo_id)
    if repository is None:
        raise ApiError(404, "not_found", f"仓库不存在: {changeset.repo_id}")

    resume_status = task.resume_status or TaskStatus.BASELINE_PREPARING.value

    # 首版重试策略：保留失败阶段用于展示，状态置回 created 并从头重新执行全部阶段
    # （各阶段产物目录按阶段覆盖写，重复执行结果确定）。
    updated = store.tasks.update_task(
        task_id,
        status=TaskStatus.CREATED.value,
        stage=TaskStatus.CREATED.value,
        resume_status=resume_status,
        retry_count=task.retry_count + 1,
        error_code=None,
        error_message=None,
    )
    store.tasks.append_event(
        task_id,
        from_status=TaskStatus.FAILED.value,
        to_status=TaskStatus.CREATED.value,
        stage=TaskStatus.CREATED.value,
        message=f"重试请求已受理，从头重新执行（{updated.retry_count}/{MAX_RETRIES}）",
    )
    schedule_analysis(background, task.changeset_id, task_id, build_analyze_options(changeset, repository, False))
    return TaskOut.from_record(updated)