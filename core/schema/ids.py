"""符号 ID 构造规则（C-SCHEMA@1）：纯函数，无 IO。

ID 在系统中是跨阶段唯一的引用键，因此构造规则必须稳定且可复现；
非法输入（空片段、路由非两段）抛 `ValueError`，不做静默修正。
"""

from __future__ import annotations

from .types import SymbolKind


def _require_parts(parts: tuple[str, ...], expected: int | None = None) -> None:
    if not parts:
        raise ValueError("symbol id 需要至少一个组成部分")
    if any(not isinstance(part, str) or not part.strip() for part in parts):
        raise ValueError(f"symbol id 组成部分不能为空: {parts!r}")
    if expected is not None and len(parts) != expected:
        raise ValueError(f"symbol id 需要 {expected} 个组成部分，实际 {len(parts)}: {parts!r}")


def make_symbol_id(kind: SymbolKind, *parts: str) -> str:
    """按 kind 拼接符号 ID。

    - `ROUTE`：`f"{method.upper()} {path}"`
    - 其余 kind：`"::".join(parts)`（`FILE` 为单段即文件路径）
    """
    kind = SymbolKind(kind)
    if kind is SymbolKind.ROUTE:
        _require_parts(parts, expected=2)
        method, path = parts
        return f"{method.upper()} {path}"
    _require_parts(parts)
    return "::".join(parts)


def file_symbol_id(file_path: str) -> str:
    return make_symbol_id(SymbolKind.FILE, file_path)


def qualname_symbol_id(file_path: str, qualname: str) -> str:
    return make_symbol_id(SymbolKind.FUNCTION, file_path, qualname)


def route_symbol_id(method: str, path: str) -> str:
    return make_symbol_id(SymbolKind.ROUTE, method, path)


def field_symbol_id(file_path: str, model: str, field_name: str) -> str:
    _require_parts((model, field_name))
    return make_symbol_id(SymbolKind.FIELD, file_path, f"{model}.{field_name}")


def test_symbol_id(file_path: str, test_name: str) -> str:
    return make_symbol_id(SymbolKind.TEST, file_path, test_name)


__all__ = [
    "make_symbol_id",
    "file_symbol_id",
    "qualname_symbol_id",
    "route_symbol_id",
    "field_symbol_id",
    "test_symbol_id",
]