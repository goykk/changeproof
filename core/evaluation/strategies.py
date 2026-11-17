"""评测期五种测试选择策略（full / neighbor / static / coverage / combo）。

生产选择器 `core.coverage.selector.select_tests` 只会输出四源并集；评测需要单独观察
静态邻近、覆盖率与全量基线各自的收益，因此这里在它的上游做「策略可见范围」的收窄，
不复制其选择规则。`full`、`neighbor` 是仅评测使用的策略，理由来源分别记为
`full`、`static_dependency`（detail 前缀标明策略名）。
"""

from __future__ import annotations

import posixpath
from collections.abc import Mapping, Sequence

from core.coverage.selector import select_tests
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

STRATEGIES: tuple[str, ...] = ("full", "neighbor", "static", "coverage", "combo")

_COVERAGE_SOURCES = frozenset({"covered_change", "covered_impact"})
_STATIC_SOURCES = frozenset({"static_dependency"})


def _normalize_path(path: str) -> str:
    normalized = str(path).replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.strip("/")


def _test_stem(test_file: str) -> str:
    stem = posixpath.splitext(posixpath.basename(test_file))[0]
    if stem.startswith("test_"):
        stem = stem[len("test_") :]
    if stem.endswith("_test"):
        stem = stem[: -len("_test")]
    return stem


def _changeset_id(
    changed: Sequence[ChangedSymbol],
    impacts: Sequence[ImpactResult],
) -> str:
    if changed:
        return changed[0].changeset_id
    if impacts:
        return impacts[0].changeset_id
    return ""


def affected_files(
    changed: Sequence[ChangedSymbol],
    impacts: Sequence[ImpactResult],
    symbols: Sequence[Symbol],
) -> list[str]:
    """变更文件与受影响符号文件的并集（按被引用的顺序去重）。"""
    symbols_by_id: Mapping[str, Symbol] = {symbol.symbol_id: symbol for symbol in symbols}
    files: list[str] = []
    seen: set[str] = set()
    for change in changed:
        path = _normalize_path(change.file_path)
        if path and path not in seen:
            seen.add(path)
            files.append(path)
    for impact in sorted(impacts, key=lambda item: (item.distance, item.rank, item.symbol_id)):
        symbol = symbols_by_id.get(impact.symbol_id)
        if symbol is None:
            continue
        path = _normalize_path(symbol.file_path)
        if path and path not in seen:
            seen.add(path)
            files.append(path)
    return files


def rank_impact_files(
    changed: Sequence[ChangedSymbol],
    impacts: Sequence[ImpactResult],
    symbols: Sequence[Symbol],
) -> list[str]:
    """影响分析的文件排名：变更文件优先，其次按影响 distance/rank 排序。"""
    return affected_files(changed, impacts, symbols)


def _neighbor_reasons(test_file: str, files: Sequence[str]) -> list[SelectionReason]:
    test_dir = posixpath.dirname(test_file)
    test_stem = _test_stem(test_file)
    reasons: list[SelectionReason] = []
    for affected in files:
        affected_dir = posixpath.dirname(affected)
        relation: str | None = None
        if test_dir == affected_dir:
            relation = "同目录"
        elif test_stem and test_stem == posixpath.splitext(posixpath.basename(affected))[0]:
            relation = "同名模块"
        if relation is None:
            continue
        reasons.append(
            SelectionReason(
                source="static_dependency",
                detail=f"neighbor: 测试文件 {test_file} 与受影响文件 {affected} 邻近（{relation}）",
                ref_id=affected,
            )
        )
        if len(reasons) >= 5:
            break
    return reasons


def _item(
    changeset_id: str,
    strategy: str,
    test_node_id: str,
    reasons: Sequence[SelectionReason],
) -> TestSelectionItem:
    return TestSelectionItem(
        changeset_id=changeset_id,
        test_node_id=test_node_id,
        strategy=strategy,
        reasons=list(reasons),
    )


def apply_strategy(
    strategy: str,
    changed: Sequence[ChangedSymbol],
    impacts: Sequence[ImpactResult],
    symbols: Sequence[Symbol],
    tests: Sequence[TestCaseInfo],
    coverage: Sequence[CoverageRecord] = (),
    high_risk_globs: Sequence[str] = ("app/core/**",),
    edges: Sequence[DependencyEdge] = (),
) -> list[TestSelectionItem]:
    """按指定策略返回被选中的已有测试；测试为空时返回 `[]`。"""
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy: {strategy}")

    test_list = list(tests)
    if not test_list:
        return []

    changeset_id = _changeset_id(changed, impacts)
    if strategy == "full":
        items = [
            _item(
                changeset_id,
                strategy,
                str(test.node_id),
                [
                    SelectionReason(
                        source="full",
                        detail="full 策略：执行全部已索引测试作为上界对照",
                    )
                ],
            )
            for test in test_list
        ]
        items.sort(key=lambda entry: entry.test_node_id)
        return items

    if strategy == "neighbor":
        files = affected_files(changed, impacts, symbols)
        items = []
        seen: set[str] = set()
        for test in test_list:
            node_id = str(test.node_id)
            if node_id in seen:
                continue
            reasons = _neighbor_reasons(_normalize_path(test.file_path), files)
            if not reasons:
                continue
            seen.add(node_id)
            items.append(_item(changeset_id, strategy, node_id, reasons))
        items.sort(key=lambda entry: entry.test_node_id)
        return items

    coverage_input = () if strategy == "static" else tuple(coverage)
    globs = tuple(high_risk_globs) if strategy == "combo" else ()
    selected = select_tests(
        list(changed),
        list(impacts),
        list(symbols),
        test_list,
        coverage_input,
        high_risk_globs=globs,
        strategy=strategy,
        edges=tuple(edges),
    )

    if strategy == "combo":
        allowed: frozenset[str] | None = None
    elif strategy == "static":
        allowed = _STATIC_SOURCES
    else:
        allowed = _COVERAGE_SOURCES
    filtered: list[TestSelectionItem] = []
    for item in selected:
        reasons = list(item.reasons)
        if allowed is not None:
            reasons = [reason for reason in reasons if reason.source in allowed]
        if not reasons:
            continue
        filtered.append(_item(changeset_id, strategy, item.test_node_id, reasons))
    return filtered


__all__ = [
    "STRATEGIES",
    "affected_files",
    "apply_strategy",
    "rank_impact_files",
]