"""分析任务状态机（C-STATE@1）。

状态流转是分析流程的唯一控制面：任何阶段推进都必须经过 `assert_transition`，
从而让非法迁移在写库前失败，而不是产生不可恢复的任务状态。
"""

from __future__ import annotations

from enum import Enum


class TaskStatus(str, Enum):
    CREATED = "created"
    BASELINE_PREPARING = "baseline_preparing"
    INDEXING = "indexing"
    ANALYZING = "analyzing"
    TEST_SELECTING = "test_selecting"
    TEST_RUNNING = "test_running"
    TEST_GENERATING = "test_generating"
    VALIDATING = "validating"
    REPORTED = "reported"
    FAILED = "failed"


ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    TaskStatus.CREATED.value: frozenset(
        {TaskStatus.BASELINE_PREPARING.value, TaskStatus.FAILED.value}
    ),
    TaskStatus.BASELINE_PREPARING.value: frozenset(
        {TaskStatus.INDEXING.value, TaskStatus.FAILED.value}
    ),
    TaskStatus.INDEXING.value: frozenset(
        {TaskStatus.ANALYZING.value, TaskStatus.FAILED.value}
    ),
    TaskStatus.ANALYZING.value: frozenset(
        {TaskStatus.TEST_SELECTING.value, TaskStatus.FAILED.value}
    ),
    TaskStatus.TEST_SELECTING.value: frozenset(
        {TaskStatus.TEST_RUNNING.value, TaskStatus.FAILED.value}
    ),
    TaskStatus.TEST_RUNNING.value: frozenset(
        {
            TaskStatus.TEST_GENERATING.value,
            TaskStatus.REPORTED.value,
            TaskStatus.FAILED.value,
        }
    ),
    TaskStatus.TEST_GENERATING.value: frozenset(
        {
            TaskStatus.VALIDATING.value,
            TaskStatus.REPORTED.value,
            TaskStatus.FAILED.value,
        }
    ),
    TaskStatus.VALIDATING.value: frozenset(
        {TaskStatus.REPORTED.value, TaskStatus.FAILED.value}
    ),
    TaskStatus.REPORTED.value: frozenset(),
    TaskStatus.FAILED.value: frozenset(
        {
            TaskStatus.CREATED.value,
            TaskStatus.BASELINE_PREPARING.value,
            TaskStatus.INDEXING.value,
            TaskStatus.ANALYZING.value,
            TaskStatus.TEST_SELECTING.value,
            TaskStatus.TEST_RUNNING.value,
            TaskStatus.TEST_GENERATING.value,
            TaskStatus.VALIDATING.value,
        }
    ),
}

MAX_RETRIES: int = 3

TERMINAL_STATUSES: frozenset[str] = frozenset({TaskStatus.REPORTED.value})


class InvalidTransitionError(ValueError):
    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(f"非法的任务状态迁移: {current!r} -> {target!r}")


def assert_transition(current: str, target: str) -> None:
    allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise InvalidTransitionError(current, target)


__all__ = [
    "TaskStatus",
    "ALLOWED_TRANSITIONS",
    "MAX_RETRIES",
    "TERMINAL_STATUSES",
    "assert_transition",
    "InvalidTransitionError",
]