"""仓储协议与持久化记录类型（C-SCHEMA@1）。

协议只描述签名，不约束实现细节；具体实现在 `core.store`。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from .types import (
    CoverageRecord,
    DependencyEdge,
    EdgeType,
    Symbol,
    SymbolKind,
    TestCaseInfo,
)


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    changeset_id: str
    status: str
    stage: str
    resume_status: str | None
    retry_count: int
    error_code: str | None
    error_message: str | None
    report_path: str | None


@dataclass(frozen=True)
class RepositoryRecord:
    repo_id: str
    name: str
    local_path: str
    default_branch: str
    status: str


@dataclass(frozen=True)
class CommitRecord:
    repo_id: str
    sha: str
    short_sha: str
    message: str
    author: str
    # ISO8601
    committed_at: str


@dataclass(frozen=True)
class ChangesetRecord:
    changeset_id: str
    repo_id: str
    base_sha: str
    target_sha: str
    status: str


class SymbolRepository(Protocol):
    def upsert_symbols(self, symbols: Sequence[Symbol]) -> int: ...

    def list_symbols(
        self,
        repo_id: str,
        commit_sha: str,
        kinds: Sequence[SymbolKind] | None = None,
    ) -> list[Symbol]: ...

    def get_symbol(self, symbol_id: str) -> Symbol | None: ...

    def find_by_qualified_name(
        self,
        repo_id: str,
        commit_sha: str,
        qualified_name: str,
    ) -> Symbol | None: ...


class EdgeRepository(Protocol):
    def upsert_edges(self, edges: Sequence[DependencyEdge]) -> int: ...

    def parents(
        self,
        symbol_id: str,
        edge_types: Sequence[EdgeType] | None = None,
    ) -> list[DependencyEdge]:
        """反向查询：谁依赖我。"""
        ...

    def children(
        self,
        symbol_id: str,
        edge_types: Sequence[EdgeType] | None = None,
    ) -> list[DependencyEdge]:
        """正向查询：我依赖谁。"""
        ...

    def edges_for_commit(self, repo_id: str, commit_sha: str) -> list[DependencyEdge]: ...


class CoverageRepository(Protocol):
    def replace_coverage(self, commit_sha: str, records: Sequence[CoverageRecord]) -> int: ...

    def tests_covering_lines(
        self,
        commit_sha: str,
        file_path: str,
        lines: Sequence[int],
    ) -> list[str]: ...

    def tests_covering_symbol(self, commit_sha: str, symbol: Symbol) -> list[str]: ...


class TestRepository(Protocol):
    def upsert_tests(self, tests: Sequence[TestCaseInfo]) -> int: ...

    def list_tests(self, repo_id: str, commit_sha: str) -> list[TestCaseInfo]: ...


class TaskRepository(Protocol):
    def create_task(self, changeset_id: str) -> TaskRecord: ...

    def get_task(self, task_id: str) -> TaskRecord | None: ...

    def list_tasks(self, changeset_id: str) -> list[TaskRecord]: ...

    def update_task(self, task_id: str, **fields: Any) -> TaskRecord: ...

    def append_event(
        self,
        task_id: str,
        from_status: str | None,
        to_status: str,
        stage: str,
        message: str,
    ) -> None: ...

    def active_task_for_changeset(self, changeset_id: str) -> TaskRecord | None: ...


__all__ = [
    "TaskRecord",
    "RepositoryRecord",
    "CommitRecord",
    "ChangesetRecord",
    "SymbolRepository",
    "EdgeRepository",
    "CoverageRepository",
    "TestRepository",
    "TaskRepository",
]