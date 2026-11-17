"""变更集创建、分析触发与影响/图/选择/执行结果只读端点。"""

from __future__ import annotations


import uuid
from dataclasses import asdict, is_dataclass
from typing import Any, Iterator, Mapping

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response

from core.schema import ChangesetRecord
from core.store import SqliteStore
from services.api.deps import (
    ApiError,
    build_analyze_options,
    get_store,
    schedule_analysis,
)
from services.api.schemas import (
    AnalyzeIn,
    ChangedSymbolOut,
    ChangesetIn,
    ChangesetOut,
    GraphEdgeOut,
    GraphNodeOut,
    GraphOut,
    ImpactItemOut,
    ImpactOut,
    RunListOut,
    RunOut,
    SelectionListOut,
    SelectionOut,
    SelectionReasonOut,
    TaskOut,
)


router = APIRouter(prefix="/api", tags=["changesets"])


@router.post("/changesets", response_model=ChangesetOut, status_code=201)
def create_changeset(
    payload: ChangesetIn,
    response: Response,
    store: SqliteStore = Depends(get_store),
) -> ChangesetOut:
    repository = store.repositories.get_repository(payload.repo_id)
    if repository is None:
        raise ApiError(404, "not_found", f"仓库不存在: {payload.repo_id}")

    candidate = ChangesetRecord(
        changeset_id=uuid.uuid4().hex,
        repo_id=payload.repo_id,
        base_sha=payload.base_sha,
        target_sha=payload.target_sha,
        status="created",
    )
    saved = store.changesets.get_or_create_changeset(candidate)
    if saved.changeset_id != candidate.changeset_id:
        response.status_code = 200
    return ChangesetOut.model_validate(saved)


@router.post("/changesets/{changeset_id}/analyze", response_model=TaskOut, status_code=202)
def analyze_changeset(
    changeset_id: str,
    payload: AnalyzeIn,
    response: Response,
    background: BackgroundTasks,
    store: SqliteStore = Depends(get_store),
) -> TaskOut:
    changeset = _require_changeset(store, changeset_id)
    existing = store.tasks.active_task_for_changeset(changeset_id)
    if existing is not None:
        response.status_code = 200
        return TaskOut.from_record(existing)

    repository = store.repositories.get_repository(changeset.repo_id)
    if repository is None:
        raise ApiError(404, "not_found", f"仓库不存在: {changeset.repo_id}")

    task = store.tasks.create_task(changeset_id)
    store.tasks.append_event(
        task.task_id,
        from_status=None,
        to_status=_enum(task.status),
        stage=_enum(task.stage),
        message="分析任务已创建",
    )
    schedule_analysis(
        background,
        changeset_id,
        task.task_id,
        build_analyze_options(changeset, repository, payload.generate_tests),
    )
    return TaskOut.from_record(task)


@router.get("/changesets/{changeset_id}/impact", response_model=ImpactOut)
def get_impact(changeset_id: str, store: SqliteStore = Depends(get_store)) -> ImpactOut:
    _require_changeset(store, changeset_id)
    changed = [_as_dict(item) for item in store.changed_symbols.list_changed_symbols(changeset_id)]
    impacts = [_as_dict(item) for item in store.impacts.list_impacts(changeset_id)]

    complete = bool(changed or impacts)
    impact_out: list[ImpactItemOut] = []
    for raw in impacts:
        symbol = _symbol_for(store, str(raw.get("symbol_id") or ""))
        if symbol is None:
            complete = False
        impact_out.append(_impact_out(raw, symbol))

    return ImpactOut(
        changed_symbols=[_changed_out(raw) for raw in changed],
        impacts=impact_out,
        truncated=_truncated(store, changeset_id),
        complete=complete,
    )


@router.get("/changesets/{changeset_id}/graph", response_model=GraphOut)
def get_graph(
    changeset_id: str,
    max_depth: int = Query(3, ge=1, le=10),
    store: SqliteStore = Depends(get_store),
) -> GraphOut:
    changeset = _require_changeset(store, changeset_id)
    changed = [_as_dict(item) for item in store.changed_symbols.list_changed_symbols(changeset_id)]
    impacts = [
        raw
        for raw in (_as_dict(item) for item in store.impacts.list_impacts(changeset_id))
        if int(raw.get("distance", 0)) <= max_depth
    ]

    nodes: dict[str, GraphNodeOut] = {}
    for raw in changed:
        node = _graph_node(store, raw, analysis_source="git_history")
        nodes[node.id] = node
    for raw in sorted(impacts, key=lambda entry: int(entry.get("rank", 0))):
        node = _graph_node(store, raw, analysis_source=_enum(raw.get("analysis_source")))
        nodes[node.id] = node

    edges: list[GraphEdgeOut] = []
    if nodes:
        for edge in store.edges.edges_for_commit(changeset.repo_id, changeset.target_sha):
            if edge.source_id not in nodes or edge.target_id not in nodes:
                continue
            edges.append(
                GraphEdgeOut(
                    source=edge.source_id,
                    target=edge.target_id,
                    edge_type=_enum(edge.edge_type),
                    analysis_source=_enum(edge.analysis_source),
                    confidence=_enum(edge.confidence),
                    file_path=edge.file_path,
                    line_start=edge.line_start,
                )
            )
    return GraphOut(nodes=list(nodes.values()), edges=edges)


@router.get("/changesets/{changeset_id}/selections", response_model=SelectionListOut)
def get_selections(changeset_id: str, store: SqliteStore = Depends(get_store)) -> SelectionListOut:
    _require_changeset(store, changeset_id)
    items = [_selection_out(record) for record in store.selections.list_selections(changeset_id)]
    return SelectionListOut(items=items)


@router.get("/changesets/{changeset_id}/runs", response_model=RunListOut)
def get_runs(changeset_id: str, store: SqliteStore = Depends(get_store)) -> RunListOut:
    _require_changeset(store, changeset_id)
    items = [_run_out(version_type, data) for version_type, data in _run_entries(store, changeset_id)]
    return RunListOut(items=items)


def _require_changeset(store: SqliteStore, changeset_id: str) -> ChangesetRecord:
    changeset = store.changesets.get_changeset(changeset_id)
    if changeset is None:
        raise ApiError(404, "not_found", f"变更集不存在: {changeset_id}")
    return changeset


def _symbol_for(store: SqliteStore, symbol_id: str) -> Any | None:
    return store.symbols.get_symbol(symbol_id) if symbol_id else None


def _truncated(store: SqliteStore, changeset_id: str) -> bool:
    report = store.reports.get_report(changeset_id) or {}
    static_facts = report.get("static_facts") or {}
    return bool(static_facts.get("truncated", False))


def _graph_node(store: SqliteStore, raw: Mapping[str, Any], analysis_source: str) -> GraphNodeOut:
    symbol_id = str(raw.get("symbol_id") or "")
    symbol = _symbol_for(store, symbol_id)
    return GraphNodeOut(
        id=symbol_id,
        label=raw.get("qualified_name") or (symbol.qualified_name if symbol else ""),
        kind=_enum(raw.get("kind") or (symbol.kind if symbol else "")) or "file",
        file_path=raw.get("file_path") or (symbol.file_path if symbol else ""),
        line_start=symbol.line_start if symbol else 0,
        analysis_source=analysis_source,
    )


def _changed_out(raw: Mapping[str, Any]) -> ChangedSymbolOut:
    ranges: list[list[int]] = []
    for pair in raw.get("diff_ranges") or []:
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            ranges.append([int(pair[0]), int(pair[1])])
    return ChangedSymbolOut(
        symbol_id=str(raw.get("symbol_id") or ""),
        kind=_enum(raw.get("kind")),
        qualified_name=raw.get("qualified_name") or "",
        file_path=raw.get("file_path") or "",
        change_type=_enum(raw.get("change_type")),
        diff_ranges=ranges,
        confidence=_enum(raw.get("confidence")),
    )


def _impact_out(raw: Mapping[str, Any], symbol: Any | None) -> ImpactItemOut:
    return ImpactItemOut(
        symbol_id=str(raw.get("symbol_id") or ""),
        kind=_enum(symbol.kind) if symbol is not None else "file",
        qualified_name=symbol.qualified_name if symbol is not None else "",
        file_path=symbol.file_path if symbol is not None else "",
        distance=int(raw.get("distance") or 0),
        rank=int(raw.get("rank") or 0),
        confidence=_enum(raw.get("confidence")),
        analysis_source=_enum(raw.get("analysis_source")),
        is_direct=bool(raw.get("is_direct", False)),
        path=[str(item) for item in raw.get("path") or []],
    )


def _selection_out(record: Any) -> SelectionOut:
    data = _as_dict(record)
    reasons = []
    for reason in data.get("reasons") or []:
        reason_data = _as_dict(reason)
        reasons.append(
            SelectionReasonOut(
                source=_enum(reason_data.get("source")),
                detail=str(reason_data.get("detail") or ""),
                ref_id=reason_data.get("ref_id"),
            )
        )
    return SelectionOut(
        test_node_id=str(data.get("test_node_id") or ""),
        strategy=str(data.get("strategy") or ""),
        reasons=reasons,
    )


def _run_entries(store: SqliteStore, changeset_id: str) -> Iterator[tuple[str, dict[str, Any]]]:
    for item in store.runs.list_runs(changeset_id):
        if isinstance(item, tuple) and len(item) == 2:
            yield str(item[0]), _as_dict(item[1])
            continue
        data = _as_dict(item)
        version_type = str(data.pop("version_type", ""))
        yield version_type, data


def _run_out(version_type: str, data: Mapping[str, Any]) -> RunOut:
    return RunOut(
        version_type=version_type,
        status=_enum(data.get("status")),
        exit_code=int(data.get("exit_code") or 0),
        duration_ms=int(data.get("duration_ms") or 0),
        selected_count=int(data.get("selected_count") or 0),
        passed_count=int(data.get("passed_count") or 0),
        failed_count=int(data.get("failed_count") or 0),
        error_count=int(data.get("error_count") or 0),
        skipped_count=int(data.get("skipped_count") or 0),
        isolation_mode=str(data.get("isolation_mode") or ""),
        log_path=str(data.get("log_path") or ""),
        coverage_path=data.get("coverage_path"),
    )


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if hasattr(value, "__dict__"):
        return {name: getattr(value, name) for name in vars(value)}
    return {}


def _enum(value: Any) -> str:
    if value is None:
        return ""
    return value.value if hasattr(value, "value") else str(value)