"""TypeScript 索引级支持（P2 多语言）：调用 `scripts/ts_index.cjs` 解析 TS/TSX。

范围声明（C-MULTILANG@1）：只产出符号与导入/定义边，不执行 TS 测试；
TS 符号与 Python 符号共用 `<file>::<name>` 的 ID 规则。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from core.schema.ids import file_symbol_id, qualname_symbol_id
from core.schema.types import (
    AnalysisSource,
    Confidence,
    DependencyEdge,
    Symbol,
    SymbolKind,
    TestCaseInfo,
)

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ts_index.cjs"
_KIND_MAP = {
    "file": SymbolKind.FILE,
    "function": SymbolKind.FUNCTION,
    "class": SymbolKind.CLASS,
    "test": SymbolKind.TEST,
}


def typescript_available() -> bool:
    node = shutil.which("node")
    typescript = _SCRIPT.parents[0].parents[0] / "apps" / "web" / "node_modules" / "typescript"
    return node is not None and _SCRIPT.exists() and typescript.exists()


def index_typescript(
    root: str,
    repo_id: str,
    commit_sha: str,
) -> tuple[list[Symbol], list[DependencyEdge], list[TestCaseInfo], list[str]]:
    """返回 (symbols, edges, tests, warnings)；不可用时返回空并给出 warning。"""
    node = shutil.which("node")
    if node is None or not _SCRIPT.exists():
        return [], [], [], ["typescript_unavailable: node 或 ts_index.cjs 缺失"]
    if not (_SCRIPT.parents[0].parents[0] / "apps" / "web" / "node_modules" / "typescript").exists():
        return [], [], [], ["typescript_unavailable: apps/web/node_modules/typescript 缺失"]

    completed = subprocess.run(
        [node, str(_SCRIPT), str(root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        return [], [], [], [f"typescript_failed: {completed.stderr[-200:]}"]

    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return [], [], [], ["typescript_failed: 输出不是合法 JSON"]

    symbols: list[Symbol] = []
    edges: list[DependencyEdge] = []
    tests: list[TestCaseInfo] = []
    for item in payload.get("symbols", []):
        kind = _KIND_MAP.get(str(item.get("kind")))
        if kind is None:
            continue
        file_path = str(item.get("file"))
        name = str(item.get("name"))
        symbol_id = file_symbol_id(file_path) if kind is SymbolKind.FILE else qualname_symbol_id(file_path, name)
        line = int(item.get("line") or 1)
        symbols.append(
            Symbol(
                symbol_id=symbol_id,
                repo_id=repo_id,
                commit_sha=commit_sha,
                kind=kind,
                qualified_name=name,
                file_path=file_path,
                line_start=line,
                line_end=line,
                metadata={"language": "typescript"},
            )
        )
        if kind is SymbolKind.TEST:
            tests.append(
                TestCaseInfo(
                    repo_id=repo_id,
                    commit_sha=commit_sha,
                    node_id=symbol_id,
                    file_path=file_path,
                    name=name,
                )
            )

    for item in payload.get("edges", []):
        edge_type = str(item.get("type"))
        if edge_type == "defines":
            from core.schema.types import EdgeType

            resolved = EdgeType.DEFINES
        elif edge_type == "imports":
            from core.schema.types import EdgeType

            resolved = EdgeType.IMPORTS
        else:
            continue
        source = str(item.get("source"))
        target = str(item.get("target"))
        edges.append(
            DependencyEdge(
                repo_id=repo_id,
                commit_sha=commit_sha,
                source_id=file_symbol_id(source),
                target_id=target if "::" in target else file_symbol_id(target),
                edge_type=resolved,
                analysis_source=AnalysisSource.AST,
                confidence=Confidence.CERTAIN,
                file_path=source,
                line_start=int(item.get("line") or 1),
                line_end=int(item.get("line") or 1),
            )
        )
    return symbols, edges, tests, []