"""沿反向依赖边计算直接与间接影响，并保留可核对的路径证据（R5）。"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..schema.types import (
    AnalysisSource,
    ChangedSymbol,
    Confidence,
    DependencyEdge,
    EdgeType,
    ImpactResult,
    Symbol,
    SymbolKind,
)

EDGE_WEIGHTS: dict[EdgeType, int] = {
    EdgeType.ROUTES_TO: 5,
    EdgeType.CALLS: 4,
    EdgeType.USES_FIELD: 3,
    EdgeType.INHERITS: 2,
    EdgeType.IMPORTS: 1,
}

_CONFIDENCE_ORDER: dict[Confidence, int] = {
    Confidence.SUGGESTED: 0,
    Confidence.INFERRED: 1,
    Confidence.CERTAIN: 2,
}


@dataclass
class ImpactOutput:
    items: list[ImpactResult]
    truncated: bool
    max_depth: int
    max_results: int


def _weakest_confidences(edges: Sequence[DependencyEdge]) -> Confidence:
    if not edges:
        return Confidence.CERTAIN
    return min((edge.confidence for edge in edges), key=lambda c: _CONFIDENCE_ORDER[c])


def compute_impact(
    changed: Sequence[ChangedSymbol],
    edges: Sequence[DependencyEdge],
    symbols_by_id: Mapping[str, Symbol],
    changeset_id: str,
    max_depth: int = 3,
    max_results: int = 200,
) -> ImpactOutput:
    """从变更符号出发沿依赖边反向传播，返回有序且受截断约束的影响结果。"""
    inbound: dict[str, list[DependencyEdge]] = defaultdict(list)
    for edge in edges:
        inbound[edge.target_id].append(edge)
    for group in inbound.values():
        group.sort(
            key=lambda e: (e.source_id, e.edge_type.value, e.analysis_source.value)
        )

    changed_ids: list[str] = []
    seen_changed: set[str] = set()
    for item in changed:
        if item.symbol_id in seen_changed:
            continue
        seen_changed.add(item.symbol_id)
        changed_ids.append(item.symbol_id)
        if item.kind is SymbolKind.FILE:
            # FILE 级变更等价于该文件全部符号：模块级 import/call 边的 target 往往是
            # 具体函数/字段符号，若只从 FILE 符号出发，反向传播会整条断链。
            for symbol in symbols_by_id.values():
                if symbol.file_path == item.file_path and symbol.symbol_id not in seen_changed:
                    seen_changed.add(symbol.symbol_id)
                    changed_ids.append(symbol.symbol_id)

    # BFS 层序访问即最短路径优先；先到者保留，保证同一符号只保留最短路径。
    visited: set[str] = set(changed_ids)
    queue: deque[tuple[str, tuple[str, ...], int, tuple[DependencyEdge, ...]]] = deque(
        (cid, (cid,), 0, ()) for cid in changed_ids
    )
    records: list[tuple[ImpactResult, int]] = []

    while queue:
        current_id, path, distance, path_edges = queue.popleft()
        if distance >= max_depth:
            continue
        for edge in inbound.get(current_id, ()):
            if edge.source_id in visited:
                continue
            visited.add(edge.source_id)
            next_path = path + (edge.source_id,)
            next_edges = path_edges + (edge,)
            next_distance = distance + 1
            queue.append((edge.source_id, next_path, next_distance, next_edges))
            if edge.source_id not in symbols_by_id:
                continue
            records.append(
                (
                    ImpactResult(
                        changeset_id=changeset_id,
                        symbol_id=edge.source_id,
                        path=list(next_path),
                        distance=next_distance,
                        rank=0,
                        confidence=_weakest_confidences(next_edges),
                        analysis_source=edge.analysis_source,
                        is_direct=next_distance == 1,
                    ),
                    EDGE_WEIGHTS.get(edge.edge_type, 0),
                )
            )

    records.sort(
        key=lambda record: (
            0 if record[0].is_direct else 1,
            record[0].distance,
            -record[1],
            record[0].symbol_id,
        )
    )
    selected = records[: max(max_results, 0)]
    items = [
        ImpactResult(
            changeset_id=item.changeset_id,
            symbol_id=item.symbol_id,
            path=item.path,
            distance=item.distance,
            rank=index,
            confidence=item.confidence,
            analysis_source=item.analysis_source,
            is_direct=item.is_direct,
        )
        for index, (item, _weight) in enumerate(selected, start=1)
    ]

    return ImpactOutput(
        items=items,
        truncated=len(records) > len(items),
        max_depth=max_depth,
        max_results=max_results,
    )