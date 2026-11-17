"""覆盖来源的 kind 口径必须一致（C-SELECT@7 的不变式防护）。"""

from __future__ import annotations

from core.coverage.selector import select_tests
from core.schema.ids import file_symbol_id, qualname_symbol_id
from core.schema.types import (
    ChangeType,
    ChangedSymbol,
    Confidence,
    CoverageRecord,
    EdgeType,
    ImpactResult,
    SelectionReason,
    Symbol,
    SymbolKind,
    TestCaseInfo,
)


def _symbol(kind: SymbolKind, name: str, file_path: str = "app/config.py") -> Symbol:
    symbol_id = file_path if kind is SymbolKind.FILE else qualname_symbol_id(file_path, name)
    return Symbol(
        symbol_id=symbol_id,
        repo_id="r",
        commit_sha="c",
        kind=kind,
        qualified_name=name,
        file_path=file_path,
        line_start=1,
        line_end=20,
    )


def _changed(symbol: Symbol) -> ChangedSymbol:
    return ChangedSymbol(
        changeset_id="cs",
        symbol_id=symbol.symbol_id,
        kind=symbol.kind,
        change_type=ChangeType.MODIFY,
        diff_ranges=[(2, 3)],
        qualified_name=symbol.qualified_name,
        file_path=symbol.file_path,
        confidence=Confidence.CERTAIN,
    )


def _impact(symbol: Symbol) -> ImpactResult:
    return ImpactResult(
        changeset_id="cs",
        symbol_id=symbol.symbol_id,
        path=[symbol.symbol_id],
        distance=1,
        rank=1,
        confidence=Confidence.CERTAIN,
        analysis_source="ast",
        is_direct=True,
    )


def _tests() -> list[TestCaseInfo]:
    return [
        TestCaseInfo(
            repo_id="r",
            commit_sha="c",
            node_id="tests/test_config.py::test_load",
            file_path="tests/test_config.py",
            name="test_load",
        )
    ]


def _coverage(file_path: str = "app/config.py") -> list[CoverageRecord]:
    return [
        CoverageRecord(
            commit_sha="c",
            test_node_id="tests/test_config.py::test_load",
            file_path=file_path,
            lines=[2, 3, 4, 5],
        )
    ]


def test_definition_symbols_do_not_select_via_coverage() -> None:
    file_symbol = _symbol(SymbolKind.FILE, "app/config.py")
    field_symbol = _symbol(SymbolKind.FIELD, "Settings.tax_rate")
    symbols = [file_symbol, field_symbol]
    items = select_tests(
        [_changed(file_symbol), _changed(field_symbol)],
        [_impact(file_symbol), _impact(field_symbol)],
        symbols,
        _tests(),
        _coverage(),
        high_risk_globs=(),
        edges=(),
    )
    # FILE/FIELD 只能产生静态/保底来源，绝不能靠 covered_change / covered_impact 入选。
    for item in items:
        for reason in item.reasons:
            assert reason.source not in {"covered_change", "covered_impact"}


def test_executable_symbol_can_be_selected_by_coverage() -> None:
    func_symbol = _symbol(SymbolKind.FUNCTION, "load_settings")
    items = select_tests(
        [_changed(func_symbol)],
        [_impact(func_symbol)],
        [func_symbol],
        _tests(),
        _coverage(),
        high_risk_globs=(),
        edges=(),
    )
    assert items
    sources = {reason.source for item in items for reason in item.reasons}
    assert "covered_change" in sources or "covered_impact" in sources


def test_fallback_sources_still_non_empty_when_coverage_missing() -> None:
    func_symbol = _symbol(SymbolKind.FUNCTION, "load_settings")
    items = select_tests(
        [_changed(func_symbol)],
        [_impact(func_symbol)],
        [func_symbol],
        _tests(),
        [],
        high_risk_globs=("app/**",),
        edges=(),
    )
    # 覆盖率缺失时仍由静态依赖或高风险保底产出，不得返回空集。
    assert items
    assert all(item.reasons for item in items)