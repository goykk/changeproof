"""把 Git 行级变更映射为变更符号（R4）。

行区间约定：``FileDiff.hunks`` 与 ``ChangedSymbol.diff_ranges`` 均为新文件侧的
闭区间 ``(start, end)``。
"""

from __future__ import annotations

import ast
import textwrap
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from ..schema.ids import file_symbol_id
from ..schema.types import (
    ChangeType,
    ChangedSymbol,
    Confidence,
    FileDiff,
    Symbol,
    SymbolKind,
)

_FILE_STATUS_CHANGE_TYPE = {
    "added": ChangeType.ADD,
    "deleted": ChangeType.DELETE,
    "renamed": ChangeType.RENAME,
    "modified": ChangeType.MODIFY,
}


def _normalize_ranges(hunks: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for start, end in hunks:
        lo, hi = (start, end) if start <= end else (end, start)
        if (lo, hi) not in ranges:
            ranges.append((lo, hi))
    ranges.sort()
    return ranges


def _intersect(
    ranges: Sequence[tuple[int, int]], line_start: int, line_end: int
) -> list[tuple[int, int]]:
    hits: list[tuple[int, int]] = []
    for start, end in ranges:
        lo = max(start, line_start)
        hi = min(end, line_end)
        if lo <= hi:
            hits.append((lo, hi))
    return hits


def _uncovered_ranges(
    hunks: Sequence[tuple[int, int]], spans: Sequence[tuple[int, int]]
) -> list[tuple[int, int]]:
    """返回 hunk 中未被任何符号行区间覆盖的部分（即模块级代码所在行）。"""
    ordered = sorted(spans)
    orphans: list[tuple[int, int]] = []
    for start, end in hunks:
        cursor = start
        for span_start, span_end in ordered:
            if span_end < cursor:
                continue
            if span_start > end:
                break
            if span_start > cursor:
                orphans.append((cursor, min(end, span_start - 1)))
            cursor = max(cursor, span_end + 1)
            if cursor > end:
                break
        if cursor <= end:
            orphans.append((cursor, end))
    return orphans


def _index_by_file(
    symbols: Sequence[Symbol],
) -> dict[str, list[Symbol]]:
    """按文件路径索引符号；FILE 级符号不参与符号映射，避免吞并具体符号。"""
    index: dict[str, list[Symbol]] = defaultdict(list)
    for symbol in symbols:
        if symbol.kind is SymbolKind.FILE:
            continue
        index[symbol.file_path].append(symbol)
    for group in index.values():
        group.sort(key=lambda s: (s.line_start, s.line_end, s.symbol_id))
    return index


def _classify_target_symbol(target: Symbol, base_symbols: Sequence[Symbol]) -> ChangeType:
    """在 target 侧命中的符号上判定变更类型（修改 / 重命名 / 新增）。"""
    for base in base_symbols:
        if base.symbol_id == target.symbol_id:
            if base.qualified_name != target.qualified_name:
                return ChangeType.RENAME
            return ChangeType.MODIFY
    overlapping = [
        base
        for base in base_symbols
        if base.line_start <= target.line_end and base.line_end >= target.line_start
    ]
    if overlapping:
        if all(base.qualified_name != target.qualified_name for base in overlapping):
            return ChangeType.RENAME
        return ChangeType.MODIFY
    return ChangeType.ADD


def load_sources(snapshot_dir: str, file_paths: Iterable[str]) -> dict[str, str]:
    """读取快照目录下指定文件的源码；缺失的文件跳过（不影响映射，只是不做语义过滤）。"""
    root = Path(snapshot_dir)
    sources: dict[str, str] = {}
    for rel_path in file_paths:
        try:
            sources[str(rel_path)] = (root / rel_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return sources


def _slice(text: str | None, start: int, end: int) -> str | None:
    if not text:
        return None
    lines = text.splitlines()
    if start < 1 or end > len(lines) or start > end:
        return None
    return "\n".join(lines[start - 1 : end])


def _normalized_ast(source: str) -> str:
    """归一化 AST：删除 docstring 语句并忽略位置信息。

    删除（而非替换内容）才能同时覆盖"修改/新增/删除 docstring"三种情形，
    否则纯注释变更会被误判为真实语义变更。
    """
    tree = ast.parse(textwrap.dedent(source))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    del body[0]
    return ast.dump(tree, include_attributes=False)


def _semantics_equal(left: str | None, right: str | None) -> bool | None:
    """返回 True/False 表示可判定，None 表示无法判定（按保守策略仍视为变更）。"""
    if left is None or right is None:
        return None
    try:
        return _normalized_ast(left) == _normalized_ast(right)
    except (SyntaxError, ValueError, RecursionError):
        return None


def _symbol_semantics_equal(
    file_path: str,
    base_path: str,
    symbol: Symbol,
    base_candidates: Sequence[Symbol],
    base_sources: Mapping[str, str] | None,
    target_sources: Mapping[str, str] | None,
) -> bool:
    if not base_sources or not target_sources:
        return False
    base_symbol = next((item for item in base_candidates if item.qualified_name == symbol.qualified_name), None)
    if base_symbol is None:
        return False
    left = _slice(base_sources.get(base_path), base_symbol.line_start, base_symbol.line_end)
    right = _slice(target_sources.get(file_path), symbol.line_start, symbol.line_end)
    return _semantics_equal(left, right) is True


def _file_semantics_equal(
    file_path: str,
    base_path: str,
    base_sources: Mapping[str, str] | None,
    target_sources: Mapping[str, str] | None,
) -> bool:
    if not base_sources or not target_sources:
        return False
    return _semantics_equal(base_sources.get(base_path), target_sources.get(file_path)) is True


def map_changes(
    file_diffs: Sequence[FileDiff],
    base_symbols: Sequence[Symbol],
    target_symbols: Sequence[Symbol],
    changeset_id: str,
    base_sources: Mapping[str, str] | None = None,
    target_sources: Mapping[str, str] | None = None,
) -> list[ChangedSymbol]:
    """把行级 diff 映射为 ``ChangedSymbol``。

    传入 ``base_sources``/``target_sources``（``{相对路径: 源码}``）时按 AST 等价性过滤：
    纯注释、空白与格式变化不产生符号级变更，也不产生文件级兜底（C-IMPACT@1）。
    """
    base_index = _index_by_file(base_symbols)
    target_index = _index_by_file(target_symbols)

    merged: dict[str, ChangedSymbol] = {}

    def _record(symbol: Symbol, change_type: ChangeType, ranges: list[tuple[int, int]], confidence: Confidence) -> None:
        existing = merged.get(symbol.symbol_id)
        if existing is None:
            merged[symbol.symbol_id] = ChangedSymbol(
                changeset_id=changeset_id,
                symbol_id=symbol.symbol_id,
                kind=symbol.kind,
                change_type=change_type,
                diff_ranges=sorted(set(ranges)),
                qualified_name=symbol.qualified_name,
                file_path=symbol.file_path,
                confidence=confidence,
            )
            return
        combined = sorted(set(existing.diff_ranges) | set(ranges))
        if combined != existing.diff_ranges:
            merged[symbol.symbol_id] = ChangedSymbol(
                changeset_id=existing.changeset_id,
                symbol_id=existing.symbol_id,
                kind=existing.kind,
                change_type=existing.change_type,
                diff_ranges=combined,
                qualified_name=existing.qualified_name,
                file_path=existing.file_path,
                confidence=existing.confidence,
            )

    for diff in file_diffs:
        status = diff.status
        hunks = _normalize_ranges(diff.hunks)
        base_path = diff.old_path or diff.file_path
        candidates = target_index.get(diff.file_path, [])
        base_candidates = base_index.get(base_path, [])
        produced = False

        if status == "deleted":
            for symbol in base_candidates:
                _record(symbol, ChangeType.DELETE, [(symbol.line_start, symbol.line_end)], Confidence.CERTAIN)
                produced = True
        elif status == "added":
            for symbol in candidates:
                _record(symbol, ChangeType.ADD, [(symbol.line_start, symbol.line_end)], Confidence.CERTAIN)
                produced = True
        else:
            if _file_semantics_equal(diff.file_path, base_path, base_sources, target_sources):
                # 整文件 AST 等价（纯注释/空白/格式）→ 不产生任何符号级或文件级变更。
                continue
            for symbol in candidates:
                hits = _intersect(hunks, symbol.line_start, symbol.line_end)
                if not hits:
                    continue
                if _symbol_semantics_equal(
                    diff.file_path, base_path, symbol, base_candidates, base_sources, target_sources
                ):
                    continue
                _record(
                    symbol,
                    _classify_target_symbol(symbol, base_candidates),
                    hits,
                    Confidence.CERTAIN,
                )
                produced = True
            # target 侧已消失的 base 符号属于删除。
            target_qualnames = {symbol.qualified_name for symbol in candidates}
            for symbol in base_candidates:
                if symbol.qualified_name in target_qualnames:
                    continue
                if not _intersect(hunks, symbol.line_start, symbol.line_end):
                    continue
                _record(symbol, ChangeType.DELETE, [(symbol.line_start, symbol.line_end)], Confidence.CERTAIN)
                produced = True

            if produced:
                # 模块级代码（常量、模块级调用等）没有独立符号，落到所属 FILE 符号上，
                # 使导入该模块的依赖方可以沿依赖边被影响传播触达。
                orphans = _uncovered_ranges(
                    hunks,
                    [(symbol.line_start, symbol.line_end) for symbol in candidates],
                )
                if orphans:
                    module_symbol = Symbol(
                        symbol_id=file_symbol_id(diff.file_path),
                        repo_id="",
                        commit_sha="",
                        kind=SymbolKind.FILE,
                        qualified_name=diff.file_path,
                        file_path=diff.file_path,
                        line_start=orphans[0][0],
                        line_end=orphans[-1][1],
                    )
                    _record(
                        module_symbol,
                        _FILE_STATUS_CHANGE_TYPE.get(status, ChangeType.MODIFY),
                        orphans,
                        Confidence.INFERRED,
                    )

        if produced:
            continue
        if _file_semantics_equal(diff.file_path, base_path, base_sources, target_sources):
            continue

        file_symbol = Symbol(
            symbol_id=file_symbol_id(base_path if status == "deleted" else diff.file_path),
            repo_id="",
            commit_sha="",
            kind=SymbolKind.FILE,
            qualified_name=diff.file_path,
            file_path=base_path if status == "deleted" else diff.file_path,
            line_start=hunks[0][0] if hunks else 0,
            line_end=hunks[-1][1] if hunks else 0,
        )
        _record(
            file_symbol,
            _FILE_STATUS_CHANGE_TYPE.get(status, ChangeType.MODIFY),
            hunks,
            Confidence.INFERRED,
        )

    return [merged[key] for key in sorted(merged)]