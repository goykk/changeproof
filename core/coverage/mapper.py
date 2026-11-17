"""coverage.json 解析、覆盖记录构建与覆盖率召回计算（纯函数，无 IO 除读取覆盖率文件）。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from core.schema.types import CoverageRecord, Symbol


def _normalize_path(path: str) -> str:
    """统一为不带首尾斜杠的 posix 相对形态，便于与鸭子类型的覆盖率路径做后缀匹配。"""
    normalized = str(path).replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.strip("/")


def _lookup_lines(index: Mapping[str, set[int]], file_path: str) -> set[int]:
    """按精确路径查找覆盖行，退回后缀匹配以兼容绝对路径与相对路径混用。"""
    key = _normalize_path(file_path)
    exact = index.get(key)
    if exact is not None:
        return exact
    matched: set[int] = set()
    for candidate, lines in index.items():
        if candidate == key or candidate.endswith("/" + key) or key.endswith("/" + candidate):
            matched |= lines
    return matched


def parse_coverage_json(path: str) -> dict[str, dict[str, list[int]]]:
    """解析 `--cov-context=test` 产出的 coverage.json，返回 `{file_path: {test_node_id: lines}}`。"""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    files = payload.get("files")
    if not isinstance(files, Mapping):
        return {}

    parsed: dict[str, dict[str, set[int]]] = {}
    for raw_path, entry in files.items():
        if not isinstance(entry, Mapping):
            continue
        per_test = parsed.setdefault(_normalize_path(raw_path), {})
        contexts = entry.get("contexts")
        matched = False
        if isinstance(contexts, Mapping) and contexts:
            # coverage.py 的 JSON 格式为 {line: [context, ...]}；同时兼容 {context: [line, ...]}。
            keys_are_lines = all(str(key).isdigit() for key in contexts)
            for key, value in contexts.items():
                if keys_are_lines:
                    for context in (value or ()):
                        if context:
                            per_test.setdefault(context.rsplit("|", 1)[0], set()).add(int(key))
                            matched = True
                else:
                    per_test.setdefault(str(key).rsplit("|", 1)[0], set()).update(int(line) for line in (value or ()))
                    matched = True
        if not matched:
            per_test.setdefault("", set()).update(int(line) for line in (entry.get("executed_lines") or ()))

    return {
        file_path: {test_node_id: sorted(lines) for test_node_id, lines in per_test.items()}
        for file_path, per_test in sorted(parsed.items())
    }


def build_coverage_records(
    parsed: Mapping[str, Mapping[str, Sequence[int]]],
    commit_sha: str,
) -> list[CoverageRecord]:
    """把解析结果展平为可落库的 CoverageRecord 列表，丢弃无行信息的条目。"""
    records: list[CoverageRecord] = []
    for file_path in sorted(parsed):
        for test_node_id in sorted(parsed[file_path]):
            lines = sorted({int(line) for line in parsed[file_path][test_node_id]})
            if not lines:
                continue
            records.append(
                CoverageRecord(
                    commit_sha=commit_sha,
                    test_node_id=str(test_node_id),
                    file_path=str(file_path),
                    lines=lines,
                )
            )
    return records


def coverage_recall(parsed: Mapping[str, Mapping[str, Sequence[int]]], target_symbols: Sequence[Symbol]) -> float:
    """被任一测试覆盖的变更符号比例；分母为 0 时返回 0.0。"""
    symbols = list(target_symbols)
    if not symbols:
        return 0.0

    covered_by_file: dict[str, set[int]] = {}
    for file_path, per_test in parsed.items():
        lines = covered_by_file.setdefault(_normalize_path(file_path), set())
        for test_lines in per_test.values():
            lines.update(int(line) for line in test_lines)

    hits = 0
    for symbol in symbols:
        covered = _lookup_lines(covered_by_file, symbol.file_path)
        if any(symbol.line_start <= line <= symbol.line_end for line in covered):
            hits += 1
    return hits / len(symbols)