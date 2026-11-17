"""共享枚举与不可变数据类（C-SCHEMA@1）。

枚举字符串值即落库值与 API 出参值，不做任何兜底转换。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SymbolKind(str, Enum):
    FILE = "file"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    ROUTE = "route"
    FIELD = "field"
    TEST = "test"
    IMPORT = "import"


class EdgeType(str, Enum):
    CALLS = "calls"
    IMPORTS = "imports"
    INHERITS = "inherits"
    DEFINES = "defines"
    USES_FIELD = "uses_field"
    ROUTES_TO = "routes_to"
    DEPENDS_ON = "depends_on"
    USES_GLOBAL = "uses_global"


class AnalysisSource(str, Enum):
    AST = "ast"
    FRAMEWORK = "framework"
    COVERAGE = "coverage"
    GIT_HISTORY = "git_history"
    MODEL = "model"
    EXECUTION = "execution"


class Confidence(str, Enum):
    CERTAIN = "certain"
    INFERRED = "inferred"
    SUGGESTED = "suggested"


class ChangeType(str, Enum):
    ADD = "add"
    MODIFY = "modify"
    DELETE = "delete"
    RENAME = "rename"


class RunStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class Symbol:
    symbol_id: str
    repo_id: str
    commit_sha: str
    kind: SymbolKind
    qualified_name: str
    file_path: str
    line_start: int
    line_end: int
    parent_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DependencyEdge:
    repo_id: str
    commit_sha: str
    source_id: str
    target_id: str
    edge_type: EdgeType
    analysis_source: AnalysisSource
    confidence: Confidence
    file_path: str
    line_start: int
    line_end: int


@dataclass(frozen=True)
class FileDiff:
    file_path: str
    # added | modified | deleted | renamed
    status: str
    old_path: str | None
    # 新文件侧行区间 (start, end)，闭区间
    hunks: list[tuple[int, int]]


@dataclass(frozen=True)
class ChangedSymbol:
    changeset_id: str
    symbol_id: str
    kind: SymbolKind
    change_type: ChangeType
    diff_ranges: list[tuple[int, int]]
    qualified_name: str
    file_path: str
    confidence: Confidence


@dataclass(frozen=True)
class ImpactResult:
    changeset_id: str
    symbol_id: str
    # 变更符号 → … → 受影响符号；path[0] 为变更符号
    path: list[str]
    distance: int
    rank: int
    confidence: Confidence
    analysis_source: AnalysisSource
    is_direct: bool


@dataclass(frozen=True)
class TestCaseInfo:
    repo_id: str
    commit_sha: str
    node_id: str
    file_path: str
    name: str
    markers: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CoverageRecord:
    commit_sha: str
    test_node_id: str
    file_path: str
    lines: list[int]


@dataclass(frozen=True)
class SelectionReason:
    # covered_change | covered_impact | static_dependency | high_risk_guard
    source: str
    detail: str
    ref_id: str | None = None


@dataclass(frozen=True)
class TestSelectionItem:
    changeset_id: str
    test_node_id: str
    strategy: str
    reasons: list[SelectionReason]


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    status: RunStatus
    duration_ms: int
    log_path: str
    # local | docker
    isolation_mode: str
    selected_count: int
    passed_count: int
    failed_count: int
    error_count: int
    skipped_count: int
    timed_out: bool
    coverage_path: str | None


__all__ = [
    "SymbolKind",
    "EdgeType",
    "AnalysisSource",
    "Confidence",
    "ChangeType",
    "RunStatus",
    "Symbol",
    "DependencyEdge",
    "FileDiff",
    "ChangedSymbol",
    "ImpactResult",
    "TestCaseInfo",
    "CoverageRecord",
    "SelectionReason",
    "TestSelectionItem",
    "RunResult",
]