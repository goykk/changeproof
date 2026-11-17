"""FastAPI 与 SQLAlchemy 约定的轻量静态解析。

本模块只产出语法层面的候选信息，符号与边的判定留在 ``core.indexer.indexer``，
以免框架细节扩散到索引主流程。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

ROUTE_METHODS = frozenset({"get", "post", "put", "delete"})
_HTTP_CALL_METHODS = frozenset({"get", "post", "put", "delete", "patch"})
_MOUNT_CALLS = frozenset({"APIRouter", "FastAPI"})
_FIELD_CALLS = frozenset({"Column", "mapped_column"})
_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


@dataclass(frozen=True)
class RouterSpec:
    """可挂载路由的对象：APIRouter 变量或 FastAPI 应用变量。"""

    rel_path: str
    var_name: str
    prefix: str
    is_app: bool
    line_start: int
    line_end: int


@dataclass(frozen=True)
class RouteSpec:
    rel_path: str
    router_var: str
    method: str
    path: str
    handler_qualname: str
    line_start: int
    line_end: int


@dataclass(frozen=True)
class IncludeSpec:
    rel_path: str
    parent_ref: str
    child_ref: str
    prefix: str
    line_start: int
    line_end: int


@dataclass(frozen=True)
class TestCallSpec:
    """测试代码里以字面量路径发起的 HTTP 客户端调用。"""

    rel_path: str
    handler_qualname: str
    method: str
    path: str
    line_start: int
    line_end: int


@dataclass(frozen=True)
class FieldSpec:
    rel_path: str
    model_name: str
    field_name: str
    line_start: int
    line_end: int


@dataclass(frozen=True)
class AttributeAccess:
    rel_path: str
    scope: str | None
    class_name: str | None
    base: str | None
    field_name: str
    line_start: int
    line_end: int


def callee_name(node: ast.AST) -> str | None:
    """返回调用表达式的末级名称；非 Name/Attribute 调用返回 None。"""
    func = node.func if isinstance(node, ast.Call) else node
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def clean_route_prefix(value: str) -> str:
    """把 ``/api/`` 规范为 ``/api``，空值保持为空串。"""
    stripped = value.strip("/")
    return f"/{stripped}" if stripped else ""


def join_route_path(*parts: str) -> str:
    segments = [part.strip("/") for part in parts if part and part.strip("/")]
    return "/" + "/".join(segments)


def parse_router_declarations(rel_path: str, tree: ast.Module) -> list[RouterSpec]:
    specs: list[RouterSpec] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            value = node.value
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            value = node.value
            targets = [node.target.id] if isinstance(node.target, ast.Name) else []
        else:
            continue
        if not isinstance(value, ast.Call):
            continue
        callee = callee_name(value)
        if callee not in _MOUNT_CALLS:
            continue
        prefix = clean_route_prefix(_string_kwarg(value, "prefix") or "")
        for name in targets:
            specs.append(
                RouterSpec(
                    rel_path=rel_path,
                    var_name=name,
                    prefix=prefix,
                    is_app=callee == "FastAPI",
                    line_start=node.lineno,
                    line_end=node.end_lineno or node.lineno,
                )
            )
    return specs


def parse_route_decorators(rel_path: str, tree: ast.Module) -> list[RouteSpec]:
    specs: list[RouteSpec] = []
    for handler, qualname in _iter_route_handlers(tree):
        for decorator in handler.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            callee = decorator.func
            if not isinstance(callee, ast.Attribute) or not isinstance(callee.value, ast.Name):
                continue
            method = callee.attr.lower()
            if method not in ROUTE_METHODS:
                continue
            path = _string_arg(decorator) or _string_kwarg(decorator, "path") or ""
            start, end = _decorated_span(handler)
            specs.append(
                RouteSpec(
                    rel_path=rel_path,
                    router_var=callee.value.id,
                    method=method.upper(),
                    path=path,
                    handler_qualname=qualname,
                    line_start=start,
                    line_end=end,
                )
            )
    return specs


def parse_http_client_calls(rel_path: str, tree: ast.Module) -> list[TestCallSpec]:
    """收集测试中以字面量路径发起的 HTTP 调用，用于把测试静态关联到路由。"""
    specs: list[TestCallSpec] = []
    for handler, qualname in _iter_route_handlers(tree):
        for node in ast.walk(handler):
            if not isinstance(node, ast.Call):
                continue
            method = callee_name(node)
            if method is None or method.lower() not in _HTTP_CALL_METHODS:
                continue
            path = _string_arg(node)
            if not path or not path.startswith("/"):
                continue
            specs.append(
                TestCallSpec(
                    rel_path=rel_path,
                    handler_qualname=qualname,
                    method=method.upper(),
                    path=path,
                    line_start=node.lineno,
                    line_end=node.end_lineno or node.lineno,
                )
            )
    return specs


def route_path_matches(pattern: str, call_path: str) -> bool:
    """判断字面量调用路径是否落在路由模板上（``{param}`` 段匹配任意单段）。"""
    literal = [segment for segment in call_path.split("?", 1)[0].split("/") if segment]
    template = [segment for segment in pattern.split("/") if segment]
    if len(literal) != len(template):
        return False
    for expected, actual in zip(template, literal):
        if expected.startswith("{") and expected.endswith("}"):
            continue
        if expected != actual:
            return False
    return True


def parse_include_router(rel_path: str, tree: ast.Module) -> list[IncludeSpec]:
    specs: list[IncludeSpec] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        if not isinstance(callee, ast.Attribute) or callee.attr != "include_router":
            continue
        if not isinstance(callee.value, ast.Name):
            continue
        child_ref = _reference_text(node.args[0]) if node.args else _keyword_text(node, "router")
        if child_ref is None:
            continue
        prefix = _string_kwarg(node, "prefix") or _string_arg(node, 1) or ""
        specs.append(
            IncludeSpec(
                rel_path=rel_path,
                parent_ref=callee.value.id,
                child_ref=child_ref,
                prefix=clean_route_prefix(prefix),
                line_start=node.lineno,
                line_end=node.end_lineno or node.lineno,
            )
        )
    return specs


def parse_model_fields(rel_path: str, tree: ast.Module) -> list[FieldSpec]:
    specs: list[FieldSpec] = []
    for cls in tree.body:
        if not isinstance(cls, ast.ClassDef):
            continue
        for stmt in cls.body:
            if isinstance(stmt, ast.Assign):
                value = stmt.value
                names = [t.id for t in stmt.targets if isinstance(t, ast.Name)]
            elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                value = stmt.value
                names = [stmt.target.id] if isinstance(stmt.target, ast.Name) else []
            else:
                continue
            if not isinstance(value, ast.Call) or callee_name(value) not in _FIELD_CALLS:
                continue
            for name in names:
                specs.append(
                    FieldSpec(
                        rel_path=rel_path,
                        model_name=cls.name,
                        field_name=name,
                        line_start=stmt.lineno,
                        line_end=stmt.end_lineno or stmt.lineno,
                    )
                )
    return specs


def collect_attribute_accesses(rel_path: str, tree: ast.Module) -> list[AttributeAccess]:
    """收集属性访问；嵌套函数内的访问归属其外层作用域。"""
    accesses: list[AttributeAccess] = []
    _scan_scope(rel_path, tree.body, None, None, accesses)
    return accesses


def collect_local_types(rel_path: str, tree: ast.Module) -> dict[str, dict[str, str]]:
    """返回 ``{作用域限定名: {局部变量名: 类名}}``，仅记录可静态判定的注解与构造赋值。"""
    result: dict[str, dict[str, str]] = {}
    _collect_scope_types(tree.body, None, result)
    return result


def _scan_scope(
    rel_path: str,
    body: list[ast.stmt],
    class_name: str | None,
    scope: str | None,
    out: list[AttributeAccess],
) -> None:
    for stmt in body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualname = f"{class_name}.{stmt.name}" if class_name else stmt.name
            _scan_scope(rel_path, stmt.body, class_name, qualname, out)
        elif isinstance(stmt, ast.ClassDef):
            _scan_scope(rel_path, stmt.body, stmt.name, None, out)
        else:
            for attr in _iter_attributes(stmt):
                out.append(
                    AttributeAccess(
                        rel_path=rel_path,
                        scope=scope,
                        class_name=class_name,
                        base=_base_name(attr.value),
                        field_name=attr.attr,
                        line_start=attr.lineno,
                        line_end=attr.end_lineno or attr.lineno,
                    )
                )


def _iter_attributes(node: ast.AST):
    for child in ast.iter_child_nodes(node):
        if isinstance(child, _SCOPE_NODES):
            continue
        if isinstance(child, ast.Attribute):
            yield child
        yield from _iter_attributes(child)


def _base_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _collect_scope_types(
    body: list[ast.stmt],
    class_name: str | None,
    out: dict[str, dict[str, str]],
) -> None:
    for stmt in body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualname = f"{class_name}.{stmt.name}" if class_name else stmt.name
            types: dict[str, str] = {}
            args = stmt.args
            for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]:
                if arg is None:
                    continue
                annotation = _annotation_class(arg.annotation)
                if annotation:
                    types[arg.arg] = annotation
            for child in stmt.body:
                if isinstance(child, ast.Assign) and isinstance(child.value, ast.Call):
                    cls_name = callee_name(child.value)
                    if cls_name:
                        for target in child.targets:
                            if isinstance(target, ast.Name):
                                types[target.id] = cls_name
                elif isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                    annotation = _annotation_class(child.annotation)
                    if annotation:
                        types[child.target.id] = annotation
                    elif isinstance(child.value, ast.Call):
                        cls_name = callee_name(child.value)
                        if cls_name:
                            types[child.target.id] = cls_name
            out[qualname] = types
        elif isinstance(stmt, ast.ClassDef):
            _collect_scope_types(stmt.body, stmt.name, out)


def _annotation_class(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _annotation_class(node.slice)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.strip("\"'") or None
    return None


def _iter_route_handlers(tree: ast.Module):
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node, node.name
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield child, f"{node.name}.{child.name}"


def _decorated_span(node: ast.AST) -> tuple[int, int]:
    """路由符号行区间：从首个装饰器行到处理函数 ``def`` 行。"""
    starts = [node.lineno]
    starts.extend(dec.lineno for dec in getattr(node, "decorator_list", ()))
    return min(starts), node.lineno


def _string_arg(call: ast.Call, index: int = 0) -> str | None:
    if len(call.args) <= index:
        return None
    arg = call.args[index]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    return None


def _string_kwarg(call: ast.Call, name: str) -> str | None:
    for keyword in call.keywords:
        if keyword.arg != name:
            continue
        if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
            return keyword.value.value
    return None


def _keyword_text(call: ast.Call, name: str) -> str | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return _reference_text(keyword.value)
    return None


def _reference_text(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = _reference_text(node.value)
        return f"{head}.{node.attr}" if head else None
    return None