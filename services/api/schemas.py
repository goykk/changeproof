"""HTTP 出参模型：字段一律 snake_case，与 C-API@1 一一对应。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from core.schema import TaskRecord

_ORM = ConfigDict(from_attributes=True)


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


class RepositoryIn(BaseModel):
    name: str = Field(min_length=1)
    local_path: str = Field(min_length=1)


class RepositoryOut(BaseModel):
    model_config = _ORM

    repo_id: str
    name: str
    local_path: str
    default_branch: str
    status: str


class RepositoryListOut(BaseModel):
    items: list[RepositoryOut]


class CommitOut(BaseModel):
    model_config = _ORM

    repo_id: str
    sha: str
    short_sha: str
    message: str
    author: str
    committed_at: str


class CommitListOut(BaseModel):
    items: list[CommitOut]


class ChangesetIn(BaseModel):
    repo_id: str = Field(min_length=1)
    base_sha: str = Field(min_length=1)
    target_sha: str = Field(min_length=1)


class ChangesetOut(BaseModel):
    model_config = _ORM

    changeset_id: str
    repo_id: str
    base_sha: str
    target_sha: str
    status: str


class AnalyzeIn(BaseModel):
    generate_tests: bool = False


class TaskOut(BaseModel):
    task_id: str
    changeset_id: str
    status: str
    stage: str
    retry_count: int
    error_code: str | None = None
    error_message: str | None = None

    @classmethod
    def from_record(cls, record: TaskRecord) -> "TaskOut":
        return cls(
            task_id=record.task_id,
            changeset_id=record.changeset_id,
            status=_enum_value(record.status),
            stage=_enum_value(record.stage),
            retry_count=record.retry_count,
            error_code=record.error_code,
            error_message=record.error_message,
        )


class TaskDetailOut(TaskOut):
    report_available: bool = False


class ChangedSymbolOut(BaseModel):
    symbol_id: str
    kind: str
    qualified_name: str
    file_path: str
    change_type: str
    diff_ranges: list[list[int]] = Field(default_factory=list)
    confidence: str


class ImpactItemOut(BaseModel):
    symbol_id: str
    kind: str
    qualified_name: str
    file_path: str
    distance: int
    rank: int
    confidence: str
    analysis_source: str
    is_direct: bool
    path: list[str] = Field(default_factory=list)


class ImpactOut(BaseModel):
    changed_symbols: list[ChangedSymbolOut] = Field(default_factory=list)
    impacts: list[ImpactItemOut] = Field(default_factory=list)
    truncated: bool = False
    complete: bool = False


class GraphNodeOut(BaseModel):
    id: str
    label: str
    kind: str
    file_path: str
    line_start: int
    analysis_source: str


class GraphEdgeOut(BaseModel):
    source: str
    target: str
    edge_type: str
    analysis_source: str
    confidence: str
    file_path: str
    line_start: int


class GraphOut(BaseModel):
    nodes: list[GraphNodeOut] = Field(default_factory=list)
    edges: list[GraphEdgeOut] = Field(default_factory=list)


class SelectionReasonOut(BaseModel):
    model_config = _ORM

    source: str
    detail: str
    ref_id: str | None = None


class SelectionOut(BaseModel):
    model_config = _ORM

    test_node_id: str
    strategy: str
    reasons: list[SelectionReasonOut] = Field(default_factory=list)


class SelectionListOut(BaseModel):
    items: list[SelectionOut] = Field(default_factory=list)


class RunOut(BaseModel):
    version_type: str
    status: str
    exit_code: int
    duration_ms: int
    selected_count: int
    passed_count: int
    failed_count: int
    error_count: int
    skipped_count: int
    isolation_mode: str
    log_path: str
    coverage_path: str | None = None


class RunListOut(BaseModel):
    items: list[RunOut] = Field(default_factory=list)


class ReportSummaryOut(BaseModel):
    changed_symbols: int = 0
    impacted_symbols: int = 0
    selected_tests: int = 0
    regression_detected: bool = False


class ReportDynamicFactsOut(BaseModel):
    coverage_missing: bool = False
    runs: list[dict[str, Any]] = Field(default_factory=list)


class ReportModelSuggestionOut(BaseModel):
    test_node_id: str = ""
    status: str = ""
    rationale: str = ""
    analysis_source: str = "model"


class ReportResidualRiskOut(BaseModel):
    risk: str = ""
    reason: str = ""
    source: str = ""


class ReportOut(BaseModel):
    task_id: str = ""
    changeset_id: str = ""
    generated_at: str = ""
    summary: ReportSummaryOut = Field(default_factory=ReportSummaryOut)
    static_facts: dict[str, Any] = Field(default_factory=dict)
    dynamic_facts: ReportDynamicFactsOut = Field(default_factory=ReportDynamicFactsOut)
    model_suggestions: list[ReportModelSuggestionOut] = Field(default_factory=list)
    residual_risks: list[ReportResidualRiskOut] = Field(default_factory=list)
    degraded_steps: list[str] = Field(default_factory=list)
    truncated: bool = False