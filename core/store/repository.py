"""幂等仓储实现：所有写方法均按唯一键 upsert，重复调用不产生重复行。"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from sqlalchemy import delete, insert, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from core.schema import (
    AnalysisSource,
    ChangeType,
    ChangedSymbol,
    ChangesetRecord,
    CommitRecord,
    Confidence,
    CoverageRecord,
    DependencyEdge,
    EdgeType,
    ImpactResult,
    RepositoryRecord,
    RunResult,
    RunStatus,
    SelectionReason,
    Symbol,
    SymbolKind,
    TaskRecord,
    TestCaseInfo,
    TestSelectionItem,
)
from core.schema.state import TERMINAL_STATUSES

from . import models
from .db import create_engine_and_session, init_schema

_SHORT_SHA_LEN = 8
_INSERT_CHUNK = 200
_ARTIFACTS_ROOT = Path("var/artifacts")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ev(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _chunked(rows: list[dict[str, Any]], size: int = _INSERT_CHUNK) -> list[list[dict[str, Any]]]:
    return [rows[i : i + size] for i in range(0, len(rows), size)]


def _dedupe(rows: list[dict[str, Any]], keys: Sequence[str]) -> list[dict[str, Any]]:
    """同一批次内可能重复出现相同唯一键，先收敛后再写，避免同语句内二次命中冲突。"""
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        unique[tuple(row[key] for key in keys)] = row
    return list(unique.values())


def _insert_many(session: Session, table: Any, rows: list[dict[str, Any]]) -> None:
    for chunk in _chunked(rows):
        session.execute(insert(table), chunk)


def _upsert_many(
    session: Session,
    table: Any,
    rows: list[dict[str, Any]],
    conflict_columns: Sequence[str],
    update_columns: Sequence[str],
) -> None:
    for row in rows:
        stmt = sqlite_insert(table).values(row)
        stmt = stmt.on_conflict_do_update(
            index_elements=list(conflict_columns),
            set_={column: getattr(stmt.excluded, column) for column in update_columns},
        )
        session.execute(stmt)


class _BaseStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory


class RepositoryStore(_BaseStore):
    def upsert_repository(self, record: RepositoryRecord) -> RepositoryRecord:
        row = {
            "repo_id": record.repo_id,
            "name": record.name,
            "local_path": record.local_path,
            "default_branch": record.default_branch,
            "status": record.status,
            "created_at": _now(),
        }
        stmt = sqlite_insert(models.RepositoryRow.__table__).values(row)
        stmt = stmt.on_conflict_do_update(
            index_elements=["repo_id"],
            set_={column: getattr(stmt.excluded, column) for column in ("name", "local_path", "default_branch", "status")},
        )
        with self._session_factory() as session, session.begin():
            session.execute(stmt)
        return record

    def get_repository(self, repo_id: str) -> RepositoryRecord | None:
        with self._session_factory() as session:
            row = session.get(models.RepositoryRow, repo_id)
            return _to_repository(row) if row is not None else None

    def list_repositories(self) -> list[RepositoryRecord]:
        with self._session_factory() as session:
            rows = session.execute(select(models.RepositoryRow).order_by(models.RepositoryRow.created_at)).scalars().all()
            return [_to_repository(row) for row in rows]


class CommitStore(_BaseStore):
    def upsert_commits(self, commits: Sequence[CommitRecord]) -> int:
        rows = _dedupe(
            [
                {
                    "repo_id": commit.repo_id,
                    "sha": commit.sha,
                    "message": commit.message,
                    "author": commit.author,
                    "committed_at": commit.committed_at,
                }
                for commit in commits
            ],
            ("repo_id", "sha"),
        )
        if not rows:
            return 0
        with self._session_factory() as session, session.begin():
            _upsert_many(
                session,
                models.CommitRow.__table__,
                rows,
                ("repo_id", "sha"),
                ("message", "author", "committed_at"),
            )
        return len(rows)

    def list_commits(self, repo_id: str, limit: int = 50) -> list[CommitRecord]:
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(models.CommitRow)
                    .where(models.CommitRow.repo_id == repo_id)
                    .order_by(models.CommitRow.committed_at.desc(), models.CommitRow.id.desc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            return [_to_commit(row) for row in rows]


class ChangesetStore(_BaseStore):
    def get_or_create_changeset(self, record: ChangesetRecord) -> ChangesetRecord:
        stmt = (
            sqlite_insert(models.ChangesetRow.__table__)
            .values(
                changeset_id=record.changeset_id,
                repo_id=record.repo_id,
                base_sha=record.base_sha,
                target_sha=record.target_sha,
                status=record.status,
                created_at=_now(),
            )
            .on_conflict_do_nothing(index_elements=["repo_id", "base_sha", "target_sha"])
        )
        with self._session_factory() as session, session.begin():
            session.execute(stmt)
            row = (
                session.execute(
                    select(models.ChangesetRow).where(
                        models.ChangesetRow.repo_id == record.repo_id,
                        models.ChangesetRow.base_sha == record.base_sha,
                        models.ChangesetRow.target_sha == record.target_sha,
                    )
                )
                .scalars()
                .one()
            )
        return _to_changeset(row)

    def get_changeset(self, changeset_id: str) -> ChangesetRecord | None:
        with self._session_factory() as session:
            row = session.get(models.ChangesetRow, changeset_id)
            return _to_changeset(row) if row is not None else None


class SymbolStore(_BaseStore):
    def upsert_symbols(self, symbols: Sequence[Symbol]) -> int:
        rows = _dedupe(
            [
                {
                    "symbol_id": symbol.symbol_id,
                    "repo_id": symbol.repo_id,
                    "commit_sha": symbol.commit_sha,
                    "kind": _ev(symbol.kind),
                    "qualified_name": symbol.qualified_name,
                    "file_path": symbol.file_path,
                    "line_start": symbol.line_start,
                    "line_end": symbol.line_end,
                    "parent_id": symbol.parent_id,
                    "metadata_json": json.dumps(symbol.metadata, ensure_ascii=False, sort_keys=True, default=str),
                }
                for symbol in symbols
            ],
            ("repo_id", "commit_sha", "symbol_id"),
        )
        if not rows:
            return 0
        with self._session_factory() as session, session.begin():
            _upsert_many(
                session,
                models.SymbolRow.__table__,
                rows,
                ("repo_id", "commit_sha", "symbol_id"),
                ("kind", "qualified_name", "file_path", "line_start", "line_end", "parent_id", "metadata_json"),
            )
        return len(rows)

    def list_symbols(
        self,
        repo_id: str,
        commit_sha: str,
        kinds: Sequence[SymbolKind] | None = None,
    ) -> list[Symbol]:
        stmt = select(models.SymbolRow).where(
            models.SymbolRow.repo_id == repo_id,
            models.SymbolRow.commit_sha == commit_sha,
        )
        if kinds:
            stmt = stmt.where(models.SymbolRow.kind.in_([_ev(kind) for kind in kinds]))
        stmt = stmt.order_by(models.SymbolRow.file_path, models.SymbolRow.line_start, models.SymbolRow.symbol_id)
        with self._session_factory() as session:
            return [_to_symbol(row) for row in session.execute(stmt).scalars().all()]

    def get_symbol(self, symbol_id: str) -> Symbol | None:
        stmt = (
            select(models.SymbolRow)
            .where(models.SymbolRow.symbol_id == symbol_id)
            .order_by(models.SymbolRow.commit_sha, models.SymbolRow.id)
            .limit(1)
        )
        with self._session_factory() as session:
            row = session.execute(stmt).scalars().first()
            return _to_symbol(row) if row is not None else None

    def find_by_qualified_name(self, repo_id: str, commit_sha: str, qualified_name: str) -> Symbol | None:
        stmt = select(models.SymbolRow).where(
            models.SymbolRow.repo_id == repo_id,
            models.SymbolRow.commit_sha == commit_sha,
            models.SymbolRow.qualified_name == qualified_name,
        )
        with self._session_factory() as session:
            row = session.execute(stmt).scalars().first()
            return _to_symbol(row) if row is not None else None


class EdgeStore(_BaseStore):
    def upsert_edges(self, edges: Sequence[DependencyEdge]) -> int:
        rows = _dedupe(
            [
                {
                    "repo_id": edge.repo_id,
                    "commit_sha": edge.commit_sha,
                    "source_id": edge.source_id,
                    "target_id": edge.target_id,
                    "edge_type": _ev(edge.edge_type),
                    "analysis_source": _ev(edge.analysis_source),
                    "confidence": _ev(edge.confidence),
                    "file_path": edge.file_path,
                    "line_start": edge.line_start,
                    "line_end": edge.line_end,
                }
                for edge in edges
            ],
            ("repo_id", "commit_sha", "source_id", "target_id", "edge_type"),
        )
        if not rows:
            return 0
        with self._session_factory() as session, session.begin():
            _upsert_many(
                session,
                models.DependencyEdgeRow.__table__,
                rows,
                ("repo_id", "commit_sha", "source_id", "target_id", "edge_type"),
                ("analysis_source", "confidence", "file_path", "line_start", "line_end"),
            )
        return len(rows)

    def parents(self, symbol_id: str, edge_types: Sequence[EdgeType] | None = None) -> list[DependencyEdge]:
        return self._edges_by_column(models.DependencyEdgeRow.target_id, symbol_id, edge_types)

    def children(self, symbol_id: str, edge_types: Sequence[EdgeType] | None = None) -> list[DependencyEdge]:
        return self._edges_by_column(models.DependencyEdgeRow.source_id, symbol_id, edge_types)

    def edges_for_commit(self, repo_id: str, commit_sha: str) -> list[DependencyEdge]:
        stmt = select(models.DependencyEdgeRow).where(
            models.DependencyEdgeRow.repo_id == repo_id,
            models.DependencyEdgeRow.commit_sha == commit_sha,
        )
        with self._session_factory() as session:
            return [_to_edge(row) for row in session.execute(stmt).scalars().all()]

    def _edges_by_column(self, column: Any, symbol_id: str, edge_types: Sequence[EdgeType] | None) -> list[DependencyEdge]:
        stmt = select(models.DependencyEdgeRow).where(column == symbol_id)
        if edge_types:
            stmt = stmt.where(models.DependencyEdgeRow.edge_type.in_([_ev(edge_type) for edge_type in edge_types]))
        stmt = stmt.order_by(models.DependencyEdgeRow.id)
        with self._session_factory() as session:
            return [_to_edge(row) for row in session.execute(stmt).scalars().all()]


class CoverageStore(_BaseStore):
    def replace_coverage(self, commit_sha: str, records: Sequence[CoverageRecord]) -> int:
        rows = [
            {
                "commit_sha": commit_sha,
                "test_node_id": record.test_node_id,
                "file_path": record.file_path,
                "lines_json": json.dumps(sorted({int(line) for line in record.lines})),
            }
            for record in records
        ]
        with self._session_factory() as session, session.begin():
            session.execute(delete(models.CoverageRow).where(models.CoverageRow.commit_sha == commit_sha))
            if rows:
                _insert_many(session, models.CoverageRow.__table__, rows)
        return len(rows)

    def tests_covering_lines(self, commit_sha: str, file_path: str, lines: Sequence[int]) -> list[str]:
        wanted = {int(line) for line in lines}
        if not wanted:
            return []
        stmt = select(models.CoverageRow.test_node_id, models.CoverageRow.lines_json).where(
            models.CoverageRow.commit_sha == commit_sha,
            models.CoverageRow.file_path == file_path,
        )
        covered: set[str] = set()
        with self._session_factory() as session:
            for test_node_id, lines_json in session.execute(stmt):
                if not test_node_id:
                    continue
                if wanted.intersection(json.loads(lines_json or "[]")):
                    covered.add(test_node_id)
        return sorted(covered)

    def tests_covering_symbol(self, commit_sha: str, symbol: Symbol) -> list[str]:
        return self.tests_covering_lines(commit_sha, symbol.file_path, range(symbol.line_start, symbol.line_end + 1))


class TestStore(_BaseStore):
    def upsert_tests(self, tests: Sequence[TestCaseInfo]) -> int:
        rows = _dedupe(
            [
                {
                    "repo_id": test.repo_id,
                    "commit_sha": test.commit_sha,
                    "node_id": test.node_id,
                    "file_path": test.file_path,
                    "name": test.name,
                    "markers_json": json.dumps(list(test.markers), ensure_ascii=False),
                }
                for test in tests
            ],
            ("repo_id", "commit_sha", "node_id"),
        )
        if not rows:
            return 0
        with self._session_factory() as session, session.begin():
            _upsert_many(
                session,
                models.TestCaseRow.__table__,
                rows,
                ("repo_id", "commit_sha", "node_id"),
                ("file_path", "name", "markers_json"),
            )
        return len(rows)

    def list_tests(self, repo_id: str, commit_sha: str) -> list[TestCaseInfo]:
        stmt = (
            select(models.TestCaseRow)
            .where(models.TestCaseRow.repo_id == repo_id, models.TestCaseRow.commit_sha == commit_sha)
            .order_by(models.TestCaseRow.file_path, models.TestCaseRow.node_id)
        )
        with self._session_factory() as session:
            return [_to_test(row) for row in session.execute(stmt).scalars().all()]


class TaskStore(_BaseStore):
    _UPDATABLE_FIELDS = frozenset(
        {"status", "stage", "resume_status", "retry_count", "error_code", "error_message", "report_path"}
    )

    def create_task(self, changeset_id: str) -> TaskRecord:
        now = _now()
        row = models.AnalysisTaskRow(
            task_id=uuid.uuid4().hex,
            changeset_id=changeset_id,
            status="created",
            stage="created",
            resume_status=None,
            retry_count=0,
            error_code=None,
            error_message=None,
            report_path=None,
            created_at=now,
            updated_at=now,
        )
        with self._session_factory() as session, session.begin():
            session.add(row)
        return _to_task(row)

    def get_task(self, task_id: str) -> TaskRecord | None:
        with self._session_factory() as session:
            row = session.get(models.AnalysisTaskRow, task_id)
            return _to_task(row) if row is not None else None

    def list_tasks(self, changeset_id: str) -> list[TaskRecord]:
        stmt = (
            select(models.AnalysisTaskRow)
            .where(models.AnalysisTaskRow.changeset_id == changeset_id)
            .order_by(models.AnalysisTaskRow.created_at, models.AnalysisTaskRow.task_id)
        )
        with self._session_factory() as session:
            return [_to_task(row) for row in session.execute(stmt).scalars().all()]

    def update_task(self, task_id: str, **fields: Any) -> TaskRecord:
        unknown = set(fields) - self._UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"unsupported task fields: {sorted(unknown)}")
        with self._session_factory() as session, session.begin():
            row = session.get(models.AnalysisTaskRow, task_id)
            if row is None:
                return None  # 契约要求记录缺失时返回 None 而非抛异常
            for key, value in fields.items():
                setattr(row, key, value)
            row.updated_at = _now()
        return _to_task(row)

    def append_event(
        self,
        task_id: str,
        from_status: str | None,
        to_status: str,
        stage: str,
        message: str,
    ) -> None:
        row = models.TaskEventRow(
            task_id=task_id,
            from_status=from_status,
            to_status=to_status,
            stage=stage,
            message=message,
            created_at=_now(),
        )
        with self._session_factory() as session, session.begin():
            session.add(row)

    def active_task_for_changeset(self, changeset_id: str) -> TaskRecord | None:
        stmt = (
            select(models.AnalysisTaskRow)
            .where(
                models.AnalysisTaskRow.changeset_id == changeset_id,
                models.AnalysisTaskRow.status.not_in(sorted(TERMINAL_STATUSES)),
            )
            .order_by(models.AnalysisTaskRow.updated_at.desc(), models.AnalysisTaskRow.task_id)
            .limit(1)
        )
        with self._session_factory() as session:
            row = session.execute(stmt).scalars().first()
            return _to_task(row) if row is not None else None


class SelectionStore(_BaseStore):
    def replace_selections(self, changeset_id: str, items: Sequence[TestSelectionItem]) -> int:
        rows = _dedupe(
            [
                {
                    "changeset_id": changeset_id,
                    "test_node_id": item.test_node_id,
                    "strategy": item.strategy,
                    "reasons_json": json.dumps(
                        [
                            {"source": reason.source, "detail": reason.detail, "ref_id": reason.ref_id}
                            for reason in item.reasons
                        ],
                        ensure_ascii=False,
                    ),
                }
                for item in items
            ],
            ("changeset_id", "test_node_id"),
        )
        with self._session_factory() as session, session.begin():
            session.execute(delete(models.TestSelectionRow).where(models.TestSelectionRow.changeset_id == changeset_id))
            if rows:
                _insert_many(session, models.TestSelectionRow.__table__, rows)
        return len(rows)

    def list_selections(self, changeset_id: str) -> list[TestSelectionItem]:
        stmt = (
            select(models.TestSelectionRow)
            .where(models.TestSelectionRow.changeset_id == changeset_id)
            .order_by(models.TestSelectionRow.id)
        )
        with self._session_factory() as session:
            return [_to_selection(row) for row in session.execute(stmt).scalars().all()]


class ChangedSymbolStore(_BaseStore):
    def replace_changed_symbols(self, changeset_id: str, items: Sequence[ChangedSymbol]) -> int:
        rows = _dedupe(
            [
                {
                    "changeset_id": changeset_id,
                    "symbol_id": item.symbol_id,
                    "kind": _ev(item.kind),
                    "change_type": _ev(item.change_type),
                    "diff_ranges_json": json.dumps([list(diff_range) for diff_range in item.diff_ranges]),
                    "qualified_name": item.qualified_name,
                    "file_path": item.file_path,
                    "confidence": _ev(item.confidence),
                }
                for item in items
            ],
            ("changeset_id", "symbol_id"),
        )
        with self._session_factory() as session, session.begin():
            session.execute(delete(models.ChangedSymbolRow).where(models.ChangedSymbolRow.changeset_id == changeset_id))
            if rows:
                _insert_many(session, models.ChangedSymbolRow.__table__, rows)
        return len(rows)

    def list_changed_symbols(self, changeset_id: str) -> list[ChangedSymbol]:
        stmt = (
            select(models.ChangedSymbolRow)
            .where(models.ChangedSymbolRow.changeset_id == changeset_id)
            .order_by(models.ChangedSymbolRow.file_path, models.ChangedSymbolRow.symbol_id)
        )
        with self._session_factory() as session:
            return [_to_changed_symbol(row) for row in session.execute(stmt).scalars().all()]


class ImpactStore(_BaseStore):
    def replace_impacts(self, changeset_id: str, items: Sequence[ImpactResult]) -> int:
        rows = _dedupe(
            [
                {
                    "changeset_id": changeset_id,
                    "symbol_id": item.symbol_id,
                    "path_json": json.dumps(list(item.path)),
                    "distance": item.distance,
                    "rank": item.rank,
                    "confidence": _ev(item.confidence),
                    "analysis_source": _ev(item.analysis_source),
                    "is_direct": 1 if item.is_direct else 0,
                }
                for item in items
            ],
            ("changeset_id", "symbol_id"),
        )
        with self._session_factory() as session, session.begin():
            session.execute(delete(models.ImpactResultRow).where(models.ImpactResultRow.changeset_id == changeset_id))
            if rows:
                _insert_many(session, models.ImpactResultRow.__table__, rows)
        return len(rows)

    def list_impacts(self, changeset_id: str) -> list[ImpactResult]:
        stmt = (
            select(models.ImpactResultRow)
            .where(models.ImpactResultRow.changeset_id == changeset_id)
            .order_by(models.ImpactResultRow.rank, models.ImpactResultRow.id)
        )
        with self._session_factory() as session:
            return [_to_impact(row) for row in session.execute(stmt).scalars().all()]


class RunStore(_BaseStore):
    def upsert_run(self, changeset_id: str, version_type: str, result: RunResult) -> None:
        row = {
            "changeset_id": changeset_id,
            "version_type": version_type,
            "status": _ev(result.status),
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
            "log_path": result.log_path,
            "isolation_mode": result.isolation_mode,
            "selected_count": result.selected_count,
            "passed_count": result.passed_count,
            "failed_count": result.failed_count,
            "error_count": result.error_count,
            "skipped_count": result.skipped_count,
            "timed_out": 1 if result.timed_out else 0,
            "coverage_path": result.coverage_path,
        }
        stmt = sqlite_insert(models.TestRunRow.__table__).values(row)
        stmt = stmt.on_conflict_do_update(
            index_elements=["changeset_id", "version_type"],
            set_={
                column: getattr(stmt.excluded, column)
                for column in (
                    "status",
                    "exit_code",
                    "duration_ms",
                    "log_path",
                    "isolation_mode",
                    "selected_count",
                    "passed_count",
                    "failed_count",
                    "error_count",
                    "skipped_count",
                    "timed_out",
                    "coverage_path",
                )
            },
        )
        with self._session_factory() as session, session.begin():
            session.execute(stmt)

    def list_runs(self, changeset_id: str) -> list[tuple[str, RunResult]]:
        stmt = (
            select(models.TestRunRow)
            .where(models.TestRunRow.changeset_id == changeset_id)
            .order_by(models.TestRunRow.version_type, models.TestRunRow.id)
        )
        with self._session_factory() as session:
            return [_to_run(row) for row in session.execute(stmt).scalars().all()]


class ReportStore(_BaseStore):
    def __init__(self, session_factory: sessionmaker[Session], artifacts_root: Path) -> None:
        super().__init__(session_factory)
        self._artifacts_root = artifacts_root

    def save_report(self, task_id: str, changeset_id: str, payload: dict) -> str:
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        target_dir = self._artifacts_root / task_id
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / "report.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, default=str), encoding="utf-8")

        stmt = sqlite_insert(models.ReportRow.__table__).values(
            task_id=task_id,
            changeset_id=changeset_id,
            payload_json=payload_json,
            created_at=_now(),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["task_id"],
            set_={
                "changeset_id": stmt.excluded.changeset_id,
                "payload_json": stmt.excluded.payload_json,
                "created_at": stmt.excluded.created_at,
            },
        )
        with self._session_factory() as session, session.begin():
            session.execute(stmt)
        return str(path.resolve())

    def get_report(self, changeset_id: str) -> dict | None:
        stmt = (
            select(models.ReportRow)
            .where(models.ReportRow.changeset_id == changeset_id)
            .order_by(models.ReportRow.created_at.desc(), models.ReportRow.id.desc())
            .limit(1)
        )
        with self._session_factory() as session:
            row = session.execute(stmt).scalars().first()
            return json.loads(row.payload_json) if row is not None else None


class SqliteStore:
    def __init__(self, db_url: str, artifacts_root: str | Path = _ARTIFACTS_ROOT) -> None:
        self.db_url = db_url
        self.engine, self.session_factory = create_engine_and_session(db_url)
        init_schema(self.engine)
        self.repositories = RepositoryStore(self.session_factory)
        self.commits = CommitStore(self.session_factory)
        self.changesets = ChangesetStore(self.session_factory)
        self.symbols = SymbolStore(self.session_factory)
        self.edges = EdgeStore(self.session_factory)
        self.coverage = CoverageStore(self.session_factory)
        self.tests = TestStore(self.session_factory)
        self.tasks = TaskStore(self.session_factory)
        self.changed_symbols = ChangedSymbolStore(self.session_factory)
        self.impacts = ImpactStore(self.session_factory)
        self.selections = SelectionStore(self.session_factory)
        self.runs = RunStore(self.session_factory)
        self.reports = ReportStore(self.session_factory, Path(artifacts_root))

    def close(self) -> None:
        self.engine.dispose()


def _to_repository(row: models.RepositoryRow) -> RepositoryRecord:
    return RepositoryRecord(
        repo_id=row.repo_id,
        name=row.name,
        local_path=row.local_path,
        default_branch=row.default_branch,
        status=row.status,
    )


def _to_commit(row: models.CommitRow) -> CommitRecord:
    return CommitRecord(
        repo_id=row.repo_id,
        sha=row.sha,
        short_sha=row.sha[:_SHORT_SHA_LEN],
        message=row.message,
        author=row.author,
        committed_at=row.committed_at,
    )


def _to_changeset(row: models.ChangesetRow) -> ChangesetRecord:
    return ChangesetRecord(
        changeset_id=row.changeset_id,
        repo_id=row.repo_id,
        base_sha=row.base_sha,
        target_sha=row.target_sha,
        status=row.status,
    )


def _to_symbol(row: models.SymbolRow) -> Symbol:
    return Symbol(
        symbol_id=row.symbol_id,
        repo_id=row.repo_id,
        commit_sha=row.commit_sha,
        kind=SymbolKind(row.kind),
        qualified_name=row.qualified_name,
        file_path=row.file_path,
        line_start=row.line_start,
        line_end=row.line_end,
        parent_id=row.parent_id,
        metadata=json.loads(row.metadata_json or "{}"),
    )


def _to_edge(row: models.DependencyEdgeRow) -> DependencyEdge:
    return DependencyEdge(
        repo_id=row.repo_id,
        commit_sha=row.commit_sha,
        source_id=row.source_id,
        target_id=row.target_id,
        edge_type=EdgeType(row.edge_type),
        analysis_source=row.analysis_source,
        confidence=row.confidence,
        file_path=row.file_path,
        line_start=row.line_start,
        line_end=row.line_end,
    )


def _to_test(row: models.TestCaseRow) -> TestCaseInfo:
    return TestCaseInfo(
        repo_id=row.repo_id,
        commit_sha=row.commit_sha,
        node_id=row.node_id,
        file_path=row.file_path,
        name=row.name,
        markers=list(json.loads(row.markers_json or "[]")),
    )


def _to_selection(row: models.TestSelectionRow) -> TestSelectionItem:
    reasons = [
        SelectionReason(source=item.get("source"), detail=item.get("detail", ""), ref_id=item.get("ref_id"))
        for item in json.loads(row.reasons_json or "[]")
    ]
    return TestSelectionItem(
        changeset_id=row.changeset_id,
        test_node_id=row.test_node_id,
        strategy=row.strategy,
        reasons=reasons,
    )


def _to_changed_symbol(row: models.ChangedSymbolRow) -> ChangedSymbol:
    return ChangedSymbol(
        changeset_id=row.changeset_id,
        symbol_id=row.symbol_id,
        kind=SymbolKind(row.kind),
        change_type=ChangeType(row.change_type),
        diff_ranges=[(int(start), int(end)) for start, end in json.loads(row.diff_ranges_json or "[]")],
        qualified_name=row.qualified_name,
        file_path=row.file_path,
        confidence=Confidence(row.confidence),
    )


def _to_impact(row: models.ImpactResultRow) -> ImpactResult:
    return ImpactResult(
        changeset_id=row.changeset_id,
        symbol_id=row.symbol_id,
        path=list(json.loads(row.path_json or "[]")),
        distance=row.distance,
        rank=row.rank,
        confidence=Confidence(row.confidence),
        analysis_source=AnalysisSource(row.analysis_source),
        is_direct=bool(row.is_direct),
    )


def _to_run(row: models.TestRunRow) -> tuple[str, RunResult]:
    return (
        row.version_type,
        RunResult(
            exit_code=row.exit_code,
            status=RunStatus(row.status),
            duration_ms=row.duration_ms,
            log_path=row.log_path,
            isolation_mode=row.isolation_mode,
            selected_count=row.selected_count,
            passed_count=row.passed_count,
            failed_count=row.failed_count,
            error_count=row.error_count,
            skipped_count=row.skipped_count,
            timed_out=bool(row.timed_out),
            coverage_path=row.coverage_path,
        ),
    )


def _to_task(row: models.AnalysisTaskRow) -> TaskRecord:
    return TaskRecord(
        task_id=row.task_id,
        changeset_id=row.changeset_id,
        status=row.status,
        stage=row.stage,
        resume_status=row.resume_status,
        retry_count=row.retry_count,
        error_code=row.error_code,
        error_message=row.error_message,
        report_path=row.report_path,
    )