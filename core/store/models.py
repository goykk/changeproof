"""SQLAlchemy ORM 行定义：表名与列名严格对应 C-STORE@1。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RepositoryRow(Base):
    __tablename__ = "repositories"

    repo_id = Column(String(128), primary_key=True)
    name = Column(String(255), nullable=False)
    local_path = Column(String(1024), nullable=False, unique=True)
    default_branch = Column(String(128), nullable=False)
    status = Column(String(32), nullable=False)
    created_at = Column(String(64), nullable=False, default=utcnow_iso)


class CommitRow(Base):
    __tablename__ = "commits"
    __table_args__ = (UniqueConstraint("repo_id", "sha", name="uq_commits_repo_sha"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    repo_id = Column(String(128), nullable=False)
    sha = Column(String(64), nullable=False)
    message = Column(Text, nullable=False, default="")
    author = Column(String(255), nullable=False, default="")
    committed_at = Column(String(64), nullable=False)


class ChangesetRow(Base):
    __tablename__ = "changesets"
    __table_args__ = (
        UniqueConstraint("repo_id", "base_sha", "target_sha", name="uq_changesets_repo_shas"),
    )

    changeset_id = Column(String(128), primary_key=True)
    repo_id = Column(String(128), nullable=False)
    base_sha = Column(String(64), nullable=False)
    target_sha = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False)
    created_at = Column(String(64), nullable=False, default=utcnow_iso)


class SymbolRow(Base):
    __tablename__ = "symbols"
    __table_args__ = (
        UniqueConstraint("repo_id", "commit_sha", "symbol_id", name="uq_symbols_repo_commit_symbol"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol_id = Column(String(1024), nullable=False)
    repo_id = Column(String(128), nullable=False)
    commit_sha = Column(String(64), nullable=False)
    kind = Column(String(32), nullable=False)
    qualified_name = Column(String(1024), nullable=False)
    file_path = Column(String(1024), nullable=False)
    line_start = Column(Integer, nullable=False)
    line_end = Column(Integer, nullable=False)
    parent_id = Column(String(1024), nullable=True)
    metadata_json = Column(Text, nullable=False, default="{}")


class DependencyEdgeRow(Base):
    __tablename__ = "dependency_edges"
    __table_args__ = (
        UniqueConstraint(
            "repo_id",
            "commit_sha",
            "source_id",
            "target_id",
            "edge_type",
            name="uq_edges_repo_commit_triple",
        ),
        Index("ix_edges_target", "target_id"),
        Index("ix_edges_source", "source_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    repo_id = Column(String(128), nullable=False)
    commit_sha = Column(String(64), nullable=False)
    source_id = Column(String(1024), nullable=False)
    target_id = Column(String(1024), nullable=False)
    edge_type = Column(String(32), nullable=False)
    analysis_source = Column(String(32), nullable=False)
    confidence = Column(String(32), nullable=False)
    file_path = Column(String(1024), nullable=False)
    line_start = Column(Integer, nullable=False)
    line_end = Column(Integer, nullable=False)


class TestCaseRow(Base):
    __tablename__ = "test_cases"
    __table_args__ = (UniqueConstraint("repo_id", "commit_sha", "node_id", name="uq_tests_repo_commit_node"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    repo_id = Column(String(128), nullable=False)
    commit_sha = Column(String(64), nullable=False)
    node_id = Column(String(1024), nullable=False)
    file_path = Column(String(1024), nullable=False)
    name = Column(String(512), nullable=False)
    markers_json = Column(Text, nullable=False, default="[]")


class CoverageRow(Base):
    __tablename__ = "coverage_records"
    __table_args__ = (
        UniqueConstraint("commit_sha", "test_node_id", "file_path", name="uq_coverage_commit_test_file"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    commit_sha = Column(String(64), nullable=False)
    test_node_id = Column(String(1024), nullable=False)
    file_path = Column(String(1024), nullable=False)
    lines_json = Column(Text, nullable=False, default="[]")


class TestSelectionRow(Base):
    __tablename__ = "test_selections"
    __table_args__ = (
        UniqueConstraint("changeset_id", "test_node_id", name="uq_selections_changeset_test"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    changeset_id = Column(String(128), nullable=False)
    test_node_id = Column(String(1024), nullable=False)
    strategy = Column(String(32), nullable=False)
    reasons_json = Column(Text, nullable=False, default="[]")


class TestRunRow(Base):
    __tablename__ = "test_runs"
    __table_args__ = (UniqueConstraint("changeset_id", "version_type", name="uq_runs_changeset_version"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    changeset_id = Column(String(128), nullable=False)
    version_type = Column(String(16), nullable=False)
    status = Column(String(16), nullable=False)
    exit_code = Column(Integer, nullable=False)
    duration_ms = Column(Integer, nullable=False)
    log_path = Column(String(1024), nullable=False)
    isolation_mode = Column(String(16), nullable=False)
    selected_count = Column(Integer, nullable=False)
    passed_count = Column(Integer, nullable=False)
    failed_count = Column(Integer, nullable=False)
    error_count = Column(Integer, nullable=False)
    skipped_count = Column(Integer, nullable=False)
    timed_out = Column(Integer, nullable=False, default=0)
    coverage_path = Column(String(1024), nullable=True)


class AnalysisTaskRow(Base):
    __tablename__ = "analysis_tasks"

    task_id = Column(String(64), primary_key=True)
    changeset_id = Column(String(128), nullable=False, index=True)
    status = Column(String(32), nullable=False)
    stage = Column(String(32), nullable=False)
    resume_status = Column(String(32), nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    report_path = Column(String(1024), nullable=True)
    created_at = Column(String(64), nullable=False, default=utcnow_iso)
    updated_at = Column(String(64), nullable=False, default=utcnow_iso)


class TaskEventRow(Base):
    __tablename__ = "task_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(64), nullable=False, index=True)
    from_status = Column(String(32), nullable=True)
    to_status = Column(String(32), nullable=False)
    stage = Column(String(32), nullable=False)
    message = Column(Text, nullable=False, default="")
    created_at = Column(String(64), nullable=False, default=utcnow_iso)


class ChangedSymbolRow(Base):
    __tablename__ = "changed_symbols"
    __table_args__ = (
        UniqueConstraint("changeset_id", "symbol_id", name="uq_changed_symbols_changeset_symbol"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    changeset_id = Column(String(128), nullable=False)
    symbol_id = Column(String(1024), nullable=False)
    kind = Column(String(32), nullable=False)
    change_type = Column(String(32), nullable=False)
    diff_ranges_json = Column(Text, nullable=False, default="[]")
    qualified_name = Column(String(1024), nullable=False)
    file_path = Column(String(1024), nullable=False)
    confidence = Column(String(32), nullable=False)


class ImpactResultRow(Base):
    __tablename__ = "impact_results"
    __table_args__ = (
        UniqueConstraint("changeset_id", "symbol_id", name="uq_impact_results_changeset_symbol"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    changeset_id = Column(String(128), nullable=False)
    symbol_id = Column(String(1024), nullable=False)
    path_json = Column(Text, nullable=False, default="[]")
    distance = Column(Integer, nullable=False)
    rank = Column(Integer, nullable=False)
    confidence = Column(String(32), nullable=False)
    analysis_source = Column(String(32), nullable=False)
    is_direct = Column(Integer, nullable=False)


class ReportRow(Base):
    __tablename__ = "reports"
    __table_args__ = (UniqueConstraint("task_id", name="uq_reports_task"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(64), nullable=False)
    changeset_id = Column(String(128), nullable=False, index=True)
    payload_json = Column(Text, nullable=False)
    created_at = Column(String(64), nullable=False, default=utcnow_iso)