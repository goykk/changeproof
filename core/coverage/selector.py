"""四源并集测试选择：覆盖变更行、覆盖受影响符号、静态依赖直连、高风险保底。"""

from __future__ import annotations

import fnmatch
import posixpath
from collections.abc import Mapping, Sequence

from core.schema.types import (
    ChangedSymbol,
    CoverageRecord,
    DependencyEdge,
    ImpactResult,
    SelectionReason,
    Symbol,

    TestCaseInfo,
    TestSelectionItem,
)

from .mapper import _lookup_lines, _normalize_path

_MAX_REASONS_PER_SOURCE = 5

# 依赖边参与静态选测的类型；跨文件路径长度上限（跳数）
_STATIC_EDGE_TYPES = frozenset({"imports", "depends_on", "calls", "routes_to"})
_STATIC_MAX_HOPS = 2

# 定义行在 import 时即被执行，覆盖证据对 FILE/FIELD/CLASS 无区分度，仅作报告证据
_COVERAGE_SELECTABLE_KINDS = frozenset({"function", "method", "route", "test"})


def _kind_value(kind: object) -> str:
    return str(getattr(kind, "value", kind)).lower()


def _coverage_index(coverage: Sequence[CoverageRecord]) -> dict[str, dict[str, set[int]]]:
    index: dict[str, dict[str, set[int]]] = {}
    for record in coverage:
        per_file = index.setdefault(str(record.test_node_id), {})
        per_file.setdefault(_normalize_path(record.file_path), set()).update(int(line) for line in record.lines)
    return index


def _covered_change_reasons(
    covered: Mapping[str, set[int]],
    changed: Sequence[ChangedSymbol],
) -> tuple[list[SelectionReason], list[SelectionReason]]:
    """返回 (入选理由, 覆盖证据)；定义行覆盖仅对可执行符号计入入选判定。"""
    reasons: list[SelectionReason] = []
    evidence: list[SelectionReason] = []
    for change in sorted(changed, key=lambda item: item.symbol_id):
        lines = _lookup_lines(covered, change.file_path)
        hit_ranges = [
            (int(start), int(end))
            for start, end in change.diff_ranges
            if any(int(start) <= line <= int(end) for line in lines)
        ]
        if not hit_ranges:
            continue
        span = ", ".join(f"{start}-{end}" for start, end in hit_ranges)
        if _kind_value(change.kind) in _COVERAGE_SELECTABLE_KINDS:
            reasons.append(
                SelectionReason(
                    source="covered_change",
                    detail=f"覆盖变更符号 {change.qualified_name} 的变更行 {span}",
                    ref_id=change.symbol_id,
                )
            )
        else:
            evidence.append(
                SelectionReason(
                    source="coverage_evidence",
                    detail=f"覆盖变更符号 {change.qualified_name} 的定义行 {span}（仅作证据）",
                    ref_id=change.symbol_id,
                )
            )
        if len(reasons) >= _MAX_REASONS_PER_SOURCE and len(evidence) >= _MAX_REASONS_PER_SOURCE:
            break
    return reasons, evidence


def _covered_impact_reasons(
    covered: Mapping[str, set[int]],
    impacts: Sequence[ImpactResult],
    symbols_by_id: Mapping[str, Symbol],
) -> list[SelectionReason]:
    reasons: list[SelectionReason] = []
    # 仅直连受影响符号（distance == 1）的覆盖证据可选中测试，避免多跳扩散导致并集膨胀
    direct_impacts = [impact for impact in impacts if impact.distance == 1]
    for impact in sorted(direct_impacts, key=lambda item: item.symbol_id):
        symbol = symbols_by_id.get(impact.symbol_id)
        if symbol is None:
            continue
        # 与 covered_change 共用同一 kind 白名单：定义行（FILE/FIELD/CLASS）不参与入选，
        # 否则配置模型/常量这类被全量测试 import 的文件会经覆盖源把选测集撑满。
        if _kind_value(symbol.kind) not in _COVERAGE_SELECTABLE_KINDS:
            continue
        lines = _lookup_lines(covered, symbol.file_path)
        if not any(symbol.line_start <= line <= symbol.line_end for line in lines):
            continue
        reasons.append(
            SelectionReason(
                source="covered_impact",
                detail=f"覆盖受影响符号 {symbol.qualified_name}（distance={impact.distance}）",
                ref_id=impact.symbol_id,
            )
        )
        if len(reasons) >= _MAX_REASONS_PER_SOURCE:
            break
    return reasons


def _test_stem(test_file: str) -> str:
    stem = posixpath.splitext(posixpath.basename(test_file))[0]
    if stem.startswith("test_"):
        stem = stem[len("test_") :]
    if stem.endswith("_test"):
        stem = stem[: -len("_test")]
    return stem


def _affected_files(
    changed: Sequence[ChangedSymbol],
    impacts: Sequence[ImpactResult],
    symbols_by_id: Mapping[str, Symbol],
) -> list[str]:
    files = {_normalize_path(change.file_path) for change in changed}
    for impact in impacts:
        symbol = symbols_by_id.get(impact.symbol_id)
        if symbol is not None:
            files.add(_normalize_path(symbol.file_path))
    return sorted(files)


def _is_test_path(path: str) -> bool:
    return path == "tests" or path.startswith("tests/")


def _path_mirror(test_file: str, affected_file: str) -> str | None:
    """目录镜像：`tests/api/routes/test_items.py` ↔ `app/api/routes/items.py`。

    真实仓库常用该布局；`tests/` → `app/` 镜像 + 去 `test_` 前缀后与受影响文件比较。
    """
    normalized_test = _normalize_path(test_file)
    if "/tests/" not in f"/{normalized_test}":
        return None
    mirrored = f"/{normalized_test}".replace("/tests/", "/app/", 1).lstrip("/")
    parts = mirrored.split("/")
    if parts[-1].startswith("test_"):
        parts[-1] = parts[-1][len("test_") :]
    candidate = "/".join(parts)
    if candidate == _normalize_path(affected_file):
        return f"目录镜像 {candidate}"
    return None


def _static_relation(test_file: str, affected_file: str) -> str | None:
    """三种静态依赖成立：同一文件、测试 stem 与 basename 同名、目录镜像。

    受影响文件位于 tests/ 目录（测试文件自身）时不作为选测依据。
    """
    if _is_test_path(affected_file):
        return None
    if test_file == affected_file:
        return "同一文件"
    test_stem = _test_stem(test_file)
    if test_stem and test_stem == posixpath.splitext(posixpath.basename(affected_file))[0]:
        return "同名模块"
    return _path_mirror(test_file, affected_file)


def _edge_file_pair(
    edge: DependencyEdge,
    symbols_by_id: Mapping[str, Symbol],
) -> tuple[str, str] | None:
    """把依赖边映射为 (source_file, target_file) 文件对。

    source 端优先用 symbol_id 反查文件路径，无法反查时回退边的 file_path；
    target 端无法反查文件路径时无法确定路径终点，返回 None。
    """
    source_symbol = symbols_by_id.get(edge.source_id)
    source_file = (
        _normalize_path(source_symbol.file_path)
        if source_symbol is not None
        else _normalize_path(edge.file_path)
    )
    target_symbol = symbols_by_id.get(edge.target_id)
    if not source_file or target_symbol is None:
        return None
    target_file = _normalize_path(target_symbol.file_path)
    if not target_file or target_file == source_file:
        return None
    return source_file, target_file


def _static_adjacency(
    edges: Sequence[DependencyEdge],
    symbols_by_id: Mapping[str, Symbol],
) -> dict[str, set[str]]:
    adjacency: dict[str, set[str]] = {}
    for edge in edges:
        if _kind_value(edge.edge_type) not in _STATIC_EDGE_TYPES:
            continue
        pair = _edge_file_pair(edge, symbols_by_id)
        if pair is None:
            continue
        source_file, target_file = pair
        adjacency.setdefault(source_file, set()).add(target_file)
    return adjacency


def _reachable_within_hops(
    adjacency: Mapping[str, set[str]],
    start: str,
    targets: set[str],
) -> dict[str, int]:
    """从 start 出发沿依赖边广度优先搜索 ≤ _STATIC_MAX_HOPS 跳可达的目标文件及跳数。"""
    distances: dict[str, int] = {}
    frontier = {start}
    visited = {start}
    for depth in range(1, _STATIC_MAX_HOPS + 1):
        next_frontier: set[str] = set()
        for node in frontier:
            for neighbor in adjacency.get(node, ()):
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                next_frontier.add(neighbor)
                if neighbor in targets:
                    distances.setdefault(neighbor, depth)
        frontier = next_frontier
        if not frontier:
            break
    return distances


def _static_dependency_reasons(
    test_file: str,
    affected_files: Sequence[str],
    adjacency: Mapping[str, set[str]] | None = None,
) -> list[SelectionReason]:
    """优先基于依赖边（≤2 跳）产生静态理由，未命中时回退精确同名规则。"""
    # 测试文件自身不作为静态选测依据（C-SELECT@5 第 2 条）
    targets = [
        _normalize_path(affected_file)
        for affected_file in affected_files
        if not _is_test_path(_normalize_path(affected_file))
    ]

    if adjacency is not None and test_file:
        distances = _reachable_within_hops(adjacency, test_file, set(targets))
        edge_reasons = [
            SelectionReason(
                source="static_dependency",
                detail=f"测试文件 {test_file} 经 {distances[affected_file]} 跳依赖路径触达受影响文件 {affected_file}",
                ref_id=affected_file,
            )
            for affected_file in targets
            if affected_file in distances
        ]
        if edge_reasons:
            return edge_reasons[:_MAX_REASONS_PER_SOURCE]

    reasons: list[SelectionReason] = []
    for affected_file in targets:
        relation = _static_relation(test_file, affected_file)
        if relation is None:
            continue
        reasons.append(
            SelectionReason(
                source="static_dependency",
                detail=f"测试文件 {test_file} 与受影响文件 {affected_file} 静态邻近（{relation}）",
                ref_id=affected_file,
            )
        )
        if len(reasons) >= _MAX_REASONS_PER_SOURCE:
            break
    return reasons


def _high_risk_affected_files(affected_files: Sequence[str], high_risk_globs: Sequence[str]) -> list[str]:
    """仅在实际受影响的文件里挑出高风险文件，不做全仓库扫描。"""
    modules: set[str] = set()
    for file_path in affected_files:
        normalized = _normalize_path(file_path)
        if any(fnmatch.fnmatchcase(normalized, _normalize_path(pattern)) for pattern in high_risk_globs):
            modules.add(normalized)
    return sorted(modules)


def _high_risk_reasons(
    test: TestCaseInfo,
    high_risk_modules: Sequence[str],
) -> list[SelectionReason]:
    """高风险保底仅在测试 stem 与受影响高风险文件 basename 完全相等时成立。"""
    stem = _test_stem(_normalize_path(test.file_path))
    if not stem:
        return []
    for module in high_risk_modules:
        basename = posixpath.splitext(posixpath.basename(module))[0]
        if not basename:
            continue
        if stem == basename:
            return [
                SelectionReason(
                    source="high_risk_guard",
                    detail=f"测试 {stem} 关联高风险模块 {module}",
                    ref_id=module,
                )
            ]
    return []


def _dedupe_reasons(reasons: Sequence[SelectionReason]) -> list[SelectionReason]:
    """同一测试对同一 source 只保留一条理由，detail 最短者优先。"""
    by_source: dict[str, SelectionReason] = {}
    for reason in reasons:
        existing = by_source.get(reason.source)
        if existing is None or len(reason.detail) < len(existing.detail):
            by_source[reason.source] = reason
    return list(by_source.values())


def select_tests(
    changed: Sequence[ChangedSymbol],
    impacts: Sequence[ImpactResult],
    symbols: Sequence[Symbol],
    tests: Sequence[TestCaseInfo],
    coverage: Sequence[CoverageRecord],
    high_risk_globs: Sequence[str] = ("app/core/**",),
    strategy: str = "combo",
    edges: Sequence[DependencyEdge] | None = None,
) -> list[TestSelectionItem]:
    """按四源并集选出已有 pytest 用例；同一用例合并全部命中理由。

    传入 ``edges`` 时优先用依赖图（≤2 跳路径）产生 ``static_dependency`` 理由，
    未命中或未传入时回退精确同名规则。
    """
    test_list = list(tests)
    if not test_list:
        return []

    changed_list = list(changed)
    impact_list = list(impacts)
    symbol_list = list(symbols)
    symbols_by_id = {symbol.symbol_id: symbol for symbol in symbol_list}
    changeset_id = (
        changed_list[0].changeset_id
        if changed_list
        else (impact_list[0].changeset_id if impact_list else "")
    )
    coverage_by_test = _coverage_index(coverage)
    affected_files = _affected_files(changed_list, impact_list, symbols_by_id)
    guarded_modules = _high_risk_affected_files(affected_files, high_risk_globs)
    adjacency = _static_adjacency(edges, symbols_by_id) if edges else None

    items: list[TestSelectionItem] = []
    seen_nodes: set[str] = set()
    for test in test_list:
        node_id = str(test.node_id)
        if node_id in seen_nodes:
            continue
        covered = coverage_by_test.get(node_id, {})
        test_file = _normalize_path(test.file_path)
        covered_change, coverage_evidence = _covered_change_reasons(covered, changed_list)
        reasons: list[SelectionReason] = list(covered_change)
        reasons.extend(_covered_impact_reasons(covered, impact_list, symbols_by_id))
        reasons.extend(_static_dependency_reasons(test_file, affected_files, adjacency))
        reasons.extend(_high_risk_reasons(test, guarded_modules))
        if not reasons:
            continue
        seen_nodes.add(node_id)
        items.append(
            TestSelectionItem(
                changeset_id=changeset_id,
                test_node_id=node_id,
                strategy=strategy,
                reasons=_dedupe_reasons([*reasons, *coverage_evidence]),
            )
        )

    items.sort(key=lambda item: item.test_node_id)
    return items

def fallback_full_selection(
    changeset_id: str,
    tests: Sequence[TestCaseInfo],
    detail: str = "空集保底：全量测试",
) -> list[TestSelectionItem]:
    """选择集为空且仓库存在测试时的运行时保底（C-SELECT@8）。

    仅由编排层在 `select_tests` 返回空集时调用；评测的各策略基线保持原样，
    避免保底把"纯覆盖率基线为空"的事实掩盖掉。
    """
    return [
        TestSelectionItem(
            changeset_id=changeset_id,
            test_node_id=str(test.node_id),
            strategy="fallback_full",
            reasons=[SelectionReason(source="high_risk_guard", detail=detail)],
        )
        for test in tests
    ]