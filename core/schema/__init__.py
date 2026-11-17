"""共享契约层公开出口（C-SCHEMA@1 / C-STATE@1）。

其他模块只通过本包访问共享类型，避免各处重复定义枚举值导致落库/API 值漂移。
"""

from __future__ import annotations

from .ids import (
    field_symbol_id,
    file_symbol_id,
    make_symbol_id,
    qualname_symbol_id,
    route_symbol_id,
    test_symbol_id,
)
from .protocols import (
    ChangesetRecord,
    CommitRecord,
    CoverageRepository,
    EdgeRepository,
    RepositoryRecord,
    SymbolRepository,
    TaskRecord,
    TaskRepository,
    TestRepository,
)
from .state import (
    ALLOWED_TRANSITIONS,
    MAX_RETRIES,
    TERMINAL_STATUSES,
    InvalidTransitionError,
    TaskStatus,
    assert_transition,
)
from .types import (
    AnalysisSource,
    ChangedSymbol,
    ChangeType,
    Confidence,
    CoverageRecord,
    DependencyEdge,
    EdgeType,
    FileDiff,
    ImpactResult,
    RunResult,
    RunStatus,
    SelectionReason,
    Symbol,
    SymbolKind,
    TestCaseInfo,
    TestSelectionItem,
)

__all__ = [
    # 枚举与数据类
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
    # 记录类型与仓储协议
    "TaskRecord",
    "RepositoryRecord",
    "CommitRecord",
    "ChangesetRecord",
    "SymbolRepository",
    "EdgeRepository",
    "CoverageRepository",
    "TestRepository",
    "TaskRepository",
    # ID 规则
    "make_symbol_id",
    "file_symbol_id",
    "qualname_symbol_id",
    "route_symbol_id",
    "field_symbol_id",
    "test_symbol_id",
    # 状态机
    "TaskStatus",
    "ALLOWED_TRANSITIONS",
    "MAX_RETRIES",
    "TERMINAL_STATUSES",
    "assert_transition",
    "InvalidTransitionError",
]