"""受限测试生成使用的模型适配器（C-GEN@1 / C-GEN@2）。

只有本模块访问模型后端。默认适配器完全离线且确定性；远端适配器通过
``CHANGEPROOF_LLM=ollama`` 显式开启。

``MockModelAdapter`` 按五类模板产出可判定的 pytest 正文：``existence``、
``constant_value``、``signature_contract``、``route_contract`` 与
``import_contract``。断言侧一律以 base 快照为准；target 快照源码只用于定位
"这一处到底改了什么"，避免为同文件里未变化的符号产出大量噪声测试。
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, Protocol, Sequence

if TYPE_CHECKING:  # 仅在类型检查期引用，避免与 generator 形成导入环。
    from core.generation.generator import GenerationRequest

MAX_SUGGESTIONS = 8
HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete"})
BUILTIN_CALLS = frozenset(
    {"abs", "bool", "dict", "float", "int", "len", "list", "max", "min", "round", "set", "sorted", "str", "sum", "tuple"}
)
PROBE_BY_ANNOTATION: dict[str, Any] = {"float": 1.2345, "int": 3, "str": "probe", "bool": True}
TEMPLATE_ORDER = {
    "existence": 0,
    "route_contract": 1,
    "signature_contract": 2,
    "constant_value": 3,
    "import_contract": 4,
    "env_contract": 5,
    "state_contract": 6,
    "guard_contract": 7,
}
ENV_KEY_CONST_RE = re.compile(r"(_ENV$|^ENV_|_KEY$|^KEY_)")
_UNRESOLVED = object()
LITERAL_TYPES = (int, float, str, bool)


class ModelUnavailableError(RuntimeError):
    """模型后端不可用。"""

    def __init__(self, message: str = "model backend unavailable", code: str = "model_unavailable") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ModelSuggestion:
    test_name: str
    content: str
    rationale: str


class ModelAdapter(Protocol):
    def suggest(self, request: "GenerationRequest") -> list[ModelSuggestion]: ...


def module_path(file_path: str) -> str | None:
    """把仓库相对 Python 文件路径转换成可导入模块路径。"""
    normalized = file_path.replace("\\", "/")
    if not normalized.endswith(".py"):
        return None
    parts = normalized[: -len(".py")].split("/")
    if not parts or parts[0] in {"tests", "test"} or "tests" in parts[:-1]:
        return None
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return None
    for part in parts:
        if not part or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", part):
            return None
    return ".".join(parts)


def _kind(value: Any) -> str:
    return str(getattr(value, "value", value))


def _sanitize(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_")
    return cleaned.lower() or "symbol"


def _normalize(path: str) -> str:
    cleaned = str(path).replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned


def _parse_sources(sources: Mapping[str, str] | None) -> dict[str, tuple[str, ast.Module]]:
    parsed: dict[str, tuple[str, ast.Module]] = {}
    for path, text in (sources or {}).items():
        if not isinstance(text, str):
            continue
        try:
            parsed[_normalize(path)] = (text, ast.parse(text))
        except SyntaxError:
            continue
    return parsed


def _segment(source: str, node: ast.AST) -> str:
    segment = ast.get_source_segment(source, node)
    if segment is None:
        return ""
    return re.sub(r"\s+", " ", segment).strip()


def _assignment_targets(node: ast.Assign | ast.AnnAssign) -> list[str]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    names: list[str] = []
    for target in targets:
        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, ast.Attribute):
            names.append(target.attr)
    return names


def _class_def(tree: ast.Module, name: str) -> ast.ClassDef | None:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    return None


def _find_definition(tree: ast.Module | None, kind: str, qualified_name: str) -> ast.AST | None:
    if tree is None or not qualified_name:
        return None
    parts = [part for part in qualified_name.split(".") if part]
    if kind == "class" and len(parts) == 1:
        return _class_def(tree, qualified_name)
    if kind == "function" and len(parts) == 1:
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == qualified_name:
                return node
        return None
    if kind == "method" and len(parts) >= 2:
        owner = _class_def(tree, parts[-2])
        if owner is None:
            return None
        for node in owner.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == parts[-1]:
                return node
        return None
    if kind == "field" and len(parts) == 2:
        owner = _class_def(tree, parts[0])
        if owner is None:
            return None
        for node in owner.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and parts[1] in _assignment_targets(node):
                return node
        return None
    return None


def _change_state(
    base_source: str,
    node: ast.AST | None,
    target_entry: tuple[str, ast.Module] | None,
    target_node: ast.AST | None,
) -> str:
    """``missing``/``changed``/``same``/``unknown``；``unknown`` 表示缺 base 或 target 源码。"""
    if node is None or target_entry is None:
        return "unknown"
    if target_node is None:
        return "missing"
    if _segment(base_source, node) != _segment(target_entry[0], target_node):
        return "changed"
    return "same"


def _module_bindings(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            names.update(_assignment_targets(node))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _literal_assignment(tree: ast.Module) -> dict[str, Any]:
    """模块级字面量常量（``X = 1`` / ``X: int = 1``），保持源码顺序。"""
    values: dict[str, Any] = {}
    for node in tree.body:
        name: str | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name, value = node.targets[0].id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name, value = node.target.id, node.value
        if name is None or value is None:
            continue
        try:
            values[name] = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            continue
    return values


def _imported_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _route_prefixes(tree: ast.Module) -> dict[str, str]:
    prefixes: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Name):
            continue
        if value.func.id != "APIRouter":
            continue
        prefix = ""
        for keyword in value.keywords:
            if keyword.arg == "prefix" and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                prefix = keyword.value.value
        for target in _assignment_targets(node):
            prefixes[target] = prefix
    return prefixes


def _route_table(tree: ast.Module) -> set[tuple[str, str]]:
    prefixes = _route_prefixes(tree)
    table: set[tuple[str, str]] = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            method = decorator.func.attr.lower()
            if method not in HTTP_METHODS or not decorator.args:
                continue
            first = decorator.args[0]
            if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
                continue
            router_name = decorator.func.value.id if isinstance(decorator.func.value, ast.Name) else ""
            table.add((method.upper(), prefixes.get(router_name, "") + first.value))
    return table


def _route_handler(tree: ast.Module, method: str, path: str) -> ast.AST | None:
    prefixes = _route_prefixes(tree)
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            if decorator.func.attr.lower() != method.lower() or not decorator.args:
                continue
            first = decorator.args[0]
            if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
                continue
            router_name = decorator.func.value.id if isinstance(decorator.func.value, ast.Name) else ""
            if prefixes.get(router_name, "") + first.value == path:
                return node
    return None


def _response_keys(tree: ast.Module, method: str, path: str) -> list[str]:
    handler = _route_handler(tree, method, path)
    keys: set[str] = set()
    if handler is None:
        return []
    for node in ast.walk(handler):
        if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Dict):
            continue
        for key in node.value.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                keys.add(key.value)
    return sorted(keys)


def _parameter_signature(node: ast.AST | None) -> list[tuple[str, str, bool]] | None:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    arguments = node.args
    positional = [*arguments.posonlyargs, *arguments.args]
    first_default = len(positional) - len(arguments.defaults)
    signature: list[tuple[str, str, bool]] = []
    for index, arg in enumerate(positional):
        kind = "POSITIONAL_ONLY" if index < len(arguments.posonlyargs) else "POSITIONAL_OR_KEYWORD"
        signature.append((arg.arg, kind, index >= first_default))
    if arguments.vararg is not None:
        signature.append((arguments.vararg.arg, "VAR_POSITIONAL", False))
    for arg, default in zip(arguments.kwonlyargs, arguments.kw_defaults):
        signature.append((arg.arg, "KEYWORD_ONLY", default is not None))
    if arguments.kwarg is not None:
        signature.append((arguments.kwarg.arg, "VAR_KEYWORD", False))
    return [item for item in signature if item[0] != "self"]


def _annotation_name(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.strip()
    return None


def _probe_values(node: ast.AST) -> list[Any] | None:
    """按注解为位置参数生成确定性探针值；无法确定时返回 None。"""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    arguments = node.args
    if arguments.vararg or arguments.kwarg or arguments.kwonlyargs:
        return None
    probes: list[Any] = []
    for arg in [*arguments.posonlyargs, *arguments.args]:
        value = PROBE_BY_ANNOTATION.get(_annotation_name(arg.annotation) or "")
        if value is None:
            return None
        probes.append(value)
    return probes or None


def _single_return(node: ast.AST) -> ast.Return | None:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    body = [stmt for stmt in node.body if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))]
    if len(body) == 1 and isinstance(body[0], ast.Return) and body[0].value is not None:
        return body[0]
    return None


def _literal_returns(node: ast.AST) -> list[Any]:
    values: list[Any] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Return) and isinstance(child.value, ast.Constant):
            value = child.value.value
            if isinstance(value, LITERAL_TYPES):
                values.append(value)
    unique: dict[str, Any] = {repr(value): value for value in values}
    return [unique[key] for key in sorted(unique)]


class _Unrenderable(Exception):
    pass


class _ProbeRenderer(ast.NodeTransformer):
    """把 base 返回表达式重写成"以探针值代入参数"的期望表达式。"""

    def __init__(self, probes: Mapping[str, Any], bindings: set[str]) -> None:
        self.probes = probes
        self.bindings = bindings

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if node.id in self.probes:
            return ast.Constant(value=self.probes[node.id])
        if node.id in BUILTIN_CALLS or node.id in {"True", "False", "None"}:
            return node
        if node.id in self.bindings:
            return ast.Attribute(value=ast.Name(id="module", ctx=ast.Load()), attr=node.id, ctx=ast.Load())
        raise _Unrenderable(node.id)


def _render_expected(expression: ast.AST, probes: Mapping[str, Any], bindings: set[str]) -> str | None:
    try:
        rewritten = _ProbeRenderer(probes, bindings).visit(copy.deepcopy(expression))
    except _Unrenderable:
        return None
    try:
        return ast.unparse(rewritten)
    except (ValueError, RecursionError):
        return None


def _field_default(node: ast.AST | None) -> Any | None:
    if node is None:
        return None
    if isinstance(node, ast.AnnAssign):
        value = node.value
    elif isinstance(node, ast.Assign):
        value = node.value
    else:
        return None
    if value is None:
        return None
    if isinstance(value, ast.Call):
        for keyword in value.keywords:
            if keyword.arg != "default":
                continue
            try:
                return ast.literal_eval(keyword.value)
            except (ValueError, SyntaxError):
                return None
        return None
    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return None


def _identity(module: str, qualified_name: str, suffix: str, symbol_id: str) -> tuple[str, str]:
    digest = hashlib.sha1(symbol_id.encode("utf-8")).hexdigest()[:8]
    file_name = f"tests/test_generated_{digest}.py"
    test_name = f"test_{_sanitize(module)}_{_sanitize(qualified_name)}_{suffix}"
    return f"{file_name}::{test_name}", test_name


@dataclass(frozen=True)
class _Candidate:
    template: str
    suggestion: ModelSuggestion


def _direct_members(symbols: Sequence[Any], file_path: str, class_qualname: str) -> list[str]:
    """同文件内直接挂在该类下的字段/方法名，按字典序返回以保证确定性。"""
    prefix = f"{class_qualname}."
    members: set[str] = set()
    for symbol in symbols:
        if str(getattr(symbol, "file_path", "")).replace("\\", "/") != file_path:
            continue
        if _kind(symbol.kind) not in {"field", "method"}:
            continue
        qualified_name = str(getattr(symbol, "qualified_name", ""))
        if not qualified_name.startswith(prefix):
            continue
        rest = qualified_name[len(prefix) :]
        if not rest or "." in rest:
            continue
        members.add(rest)
    return sorted(members)


def _existence_candidate(changed: Any, symbols: Sequence[Any]) -> _Candidate | None:
    module = module_path(_normalize(changed.file_path))
    if module is None:
        return None
    kind = _kind(changed.kind)
    file_path = _normalize(changed.file_path)
    qualified_name = str(getattr(changed, "qualified_name", "") or "")
    parts = [part for part in qualified_name.split(".") if part]
    if not parts:
        return None
    if kind == "class" and len(parts) == 1:
        members = _direct_members(symbols, file_path, qualified_name)
        owner_chain = [qualified_name] if members else []
        attributes = members or [qualified_name]
    elif kind in {"function", "method", "field"}:
        owner_chain = parts[:-1]
        attributes = parts[-1:]
    else:
        return None
    symbol_id = str(getattr(changed, "symbol_id", "") or f"{file_path}::{qualified_name}")
    node_id, test_name = _identity(module, qualified_name, "present", symbol_id)
    lines = [
        f"# generated for {symbol_id}",
        f"import {module} as module",
        "",
        "",
        f"def {test_name}():",
        "    target = module",
    ]
    for part in owner_chain:
        lines.append(f'    target = getattr(target, "{part}", None)')
        lines.append(f'    assert target is not None, "missing {part}"')
    for attribute in attributes:
        lines.append(f'    assert hasattr(target, "{attribute}"), "missing {attribute}"')
    rationale = (
        f"Changed symbol {qualified_name} may disappear after the change; "
        f"assert its public surface on {module} so the regression becomes observable."
    )
    return _Candidate("existence", ModelSuggestion(node_id, "\n".join(lines) + "\n", rationale))


def _constant_candidates(
    changed: Any,
    base_entry: tuple[str, ast.Module],
    target_entry: tuple[str, ast.Module] | None,
    module: str,
) -> list[_Candidate]:
    """模块级字面量常量：断言运行期值仍等于 base 值（config、shared_util）。"""
    _, base_tree = base_entry
    constants = _literal_assignment(base_tree)
    if not constants:
        return []
    if target_entry is None:
        original = constants
    else:
        target_constants = _literal_assignment(target_entry[1])
        original = {name: value for name, value in constants.items() if target_constants.get(name) != value}
    if not original:
        return []
    qualified_name = str(getattr(changed, "qualified_name", "") or "")
    symbol_id = str(getattr(changed, "symbol_id", "") or qualified_name)
    node_id, test_name = _identity(module, qualified_name, "constants", symbol_id)
    lines = [f"# generated for {symbol_id}", f"import {module} as module", "", "", f"def {test_name}():"]
    for name, value in original.items():
        lines.append(f'    assert module.{name} == {value!r}, "constant {name} changed"')
    rationale = (
        "Configuration and shared-utility literals are part of the module contract; "
        "assert the base snapshot values so an incidental edit is detectable."
    )
    return [_Candidate("constant_value", ModelSuggestion(node_id, "\n".join(lines) + "\n", rationale))]


def _field_default_candidate(
    changed: Any,
    base_node: ast.AST | None,
    target_node: ast.AST | None,
    module: str,
) -> _Candidate | None:
    expected = _field_default(base_node)
    if not isinstance(expected, LITERAL_TYPES):
        return None
    if _field_default(target_node) == expected:
        return None
    qualified_name = str(getattr(changed, "qualified_name", "") or "")
    parts = [part for part in qualified_name.split(".") if part]
    if len(parts) != 2:
        return None
    symbol_id = str(getattr(changed, "symbol_id", "") or qualified_name)
    node_id, test_name = _identity(module, qualified_name, "default", symbol_id)
    content = "\n".join(
        [
            f"# generated for {symbol_id}",
            f"import {module} as module",
            "",
            "",
            f"def {test_name}():",
            f'    column = module.{parts[0]}.__table__.columns["{parts[1]}"]',
            f'    assert getattr(column.default, "arg", None) == {expected!r}, "column default changed"',
        ]
    ) + "\n"
    rationale = (
        f"Field {qualified_name} declares a literal default ({expected!r}) in the base snapshot; "
        "assert the runtime column default so a silent default change is caught."
    )
    return _Candidate("constant_value", ModelSuggestion(node_id, content, rationale))


def _function_constant_candidates(
    changed: Any,
    base_entry: tuple[str, ast.Module],
    base_node: ast.AST | None,
    module: str,
) -> list[_Candidate]:
    qualified_name = str(getattr(changed, "qualified_name", "") or "")
    if "." in qualified_name or not isinstance(base_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return []
    _, base_tree = base_entry
    probes = _probe_values(base_node)
    if probes is None:
        return []
    symbol_id = str(getattr(changed, "symbol_id", "") or qualified_name)
    argument_names = [arg.arg for arg in [*base_node.args.posonlyargs, *base_node.args.args]]
    arguments = ", ".join(repr(value) for value in probes)
    results: list[_Candidate] = []

    single = _single_return(base_node)
    if single is not None:
        expected = _render_expected(single.value, dict(zip(argument_names, probes)), _module_bindings(base_tree))
        if expected is not None:
            node_id, test_name = _identity(module, qualified_name, "value", symbol_id)
            content = "\n".join(
                [
                    f"# generated for {symbol_id}",
                    f"import {module} as module",
                    "",
                    "",
                    f"def {test_name}():",
                    f'    assert module.{qualified_name}({arguments}) == {expected}, "behaviour changed"',
                ]
            ) + "\n"
            rationale = (
                f"{qualified_name} returns {expected} for the base snapshot probe; "
                "assert the runtime value so a changed constant inside the function is caught."
            )
            results.append(_Candidate("constant_value", ModelSuggestion(node_id, content, rationale)))
            return results

    literals = _literal_returns(base_node)
    if not literals:
        return []
    calls = [list(probes)]
    numeric_positions = [
        index for index, value in enumerate(probes) if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    for literal in literals[:2]:
        if not isinstance(literal, (int, float)) or isinstance(literal, bool) or not numeric_positions:
            continue
        variant = list(probes)
        variant[numeric_positions[-1]] = literal
        if variant != calls[0]:
            calls.append(variant)
    node_id, test_name = _identity(module, qualified_name, "literals", symbol_id)
    allowed = "{" + ", ".join(repr(value) for value in literals) + "}"
    lines = [f"# generated for {symbol_id}", f"import {module} as module", "", "", f"def {test_name}():"]
    for call in calls:
        arguments = ", ".join(repr(value) for value in call)
        lines.append(f'    assert module.{qualified_name}({arguments}) in {allowed}, "unexpected literal"')
    rationale = (
        f"{qualified_name} only returns {allowed} in the base snapshot; "
        "probe the function so an edited literal becomes observable."
    )
    results.append(_Candidate("constant_value", ModelSuggestion(node_id, "\n".join(lines) + "\n", rationale)))
    return results


def _signature_candidate(
    changed: Any,
    base_node: ast.AST | None,
    target_node: ast.AST | None,
    module: str,
) -> _Candidate | None:
    expected = _parameter_signature(base_node)
    actual = _parameter_signature(target_node)
    if expected is None or actual is None or expected == actual:
        return None
    qualified_name = str(getattr(changed, "qualified_name", "") or "")
    symbol_id = str(getattr(changed, "symbol_id", "") or qualified_name)
    node_id, test_name = _identity(module, qualified_name, "signature", symbol_id)
    content = "\n".join(
        [
            f"# generated for {symbol_id}",
            "import inspect",
            f"import {module} as module",
            "",
            "",
            f"def {test_name}():",
            f"    expected = {expected!r}",
            f"    signature = inspect.signature(module.{qualified_name})",
            "    actual = [",
            "        (parameter.name, parameter.kind.name, parameter.default is not inspect.Parameter.empty)",
            "        for name, parameter in signature.parameters.items()",
            '        if name != "self"',
            "    ]",
            '    assert actual == expected, "public signature changed"',
        ]
    ) + "\n"
    rationale = (
        f"Callers rely on the base parameter list of {qualified_name}; "
        "assert the signature contract so an added, removed or re-kind-ed parameter is caught."
    )
    return _Candidate("signature_contract", ModelSuggestion(node_id, content, rationale))


def _route_candidate(
    changed: Any,
    base_entry: tuple[str, ast.Module],
    target_entry: tuple[str, ast.Module] | None,
    module: str,
) -> _Candidate | None:
    method, _, path = str(getattr(changed, "qualified_name", "") or "").partition(" ")
    method = method.strip().upper()
    path = path.strip()
    if method not in {value.upper() for value in HTTP_METHODS} or not path:
        return None
    base_source, base_tree = base_entry
    if (method, path) not in _route_table(base_tree):
        return None
    handler = _route_handler(base_tree, method, path)
    if handler is None:
        return None
    if target_entry is not None and (method, path) in _route_table(target_entry[1]):
        target_handler = _route_handler(target_entry[1], method, path)
        if target_handler is not None and _segment(base_source, handler) == _segment(target_entry[0], target_handler):
            return None
    prefixes = _route_prefixes(base_tree)
    router_name = next(iter(prefixes), "")
    if not router_name:
        return None
    keys = _response_keys(base_tree, method, path)
    qualified_name = f"{method} {path}"
    symbol_id = str(getattr(changed, "symbol_id", "") or qualified_name)
    node_id, test_name = _identity(module, qualified_name, "route", symbol_id)
    request_path = re.sub(r"\{[^}]+\}", "1", path)
    lines = [
        f"# generated for {symbol_id}",
        f"import {module} as module",
        "from fastapi import FastAPI",
        "from fastapi.testclient import TestClient",
        "",
        "",
        f"def {test_name}():",
        "    app = FastAPI()",
        f"    app.include_router(module.{router_name})",
        "    client = TestClient(app)",
        "    routes = {",
        "        (route.path, verb)",
        f"        for route in module.{router_name}.routes",
        "        for verb in getattr(route, 'methods', ()) or ()",
        "    }",
        f'    assert ("{path}", "{method}") in routes, "{method} {path} is no longer routed"',
    ]
    if method == "GET":
        lines.append(f'    response = client.get("{request_path}")')
        if "{" not in path:
            lines.append('    assert response.status_code == 200, "route no longer returns 200"')
        if keys:
            allowed = "{" + ", ".join(repr(key) for key in keys) + "}"
            lines.extend(
                [
                    "    if response.status_code == 200:",
                    "        payload = response.json()",
                    "        if isinstance(payload, dict):",
                    f'            assert {allowed} <= set(payload), "response fields changed"',
                ]
            )
    rationale = (
        f"Route {method} {path} is part of the base HTTP contract; "
        "assert it is still routed and still answers with the original response fields."
    )
    return _Candidate("route_contract", ModelSuggestion(node_id, "\n".join(lines) + "\n", rationale))


def _import_candidate(
    changed: Any,
    base_entry: tuple[str, ast.Module],
    target_entry: tuple[str, ast.Module] | None,
    module: str,
) -> _Candidate | None:
    if target_entry is None:
        return None
    _, base_tree = base_entry
    removed = sorted(_imported_names(base_tree) - _imported_names(target_entry[1]))
    if not removed:
        return None
    qualified_name = str(getattr(changed, "qualified_name", "") or module)
    symbol_id = str(getattr(changed, "symbol_id", "") or qualified_name)
    node_id, test_name = _identity(module, qualified_name, "imports", symbol_id)
    lines = [f"# generated for {symbol_id}", f"import {module} as module", "", "", f"def {test_name}():"]
    for name in removed:
        lines.append(f'    assert hasattr(module, "{name}"), "missing import {name}"')
    rationale = (
        f"{module} re-exports {', '.join(removed)} in the base snapshot; "
        "assert the ambient module surface so a dropped cross-module import is caught."
    )
    return _Candidate("import_contract", ModelSuggestion(node_id, "\n".join(lines) + "\n", rationale))


def _dotted(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _env_keys(node: ast.AST | None) -> list[str]:
    """提取 ``os.getenv("K")`` / ``os.environ["K"]`` 的键名字面量，保持出现顺序。"""
    if node is None:
        return []
    keys: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            if _dotted(child.func) in {"os.getenv", "os.environ.get"} and child.args:
                first = child.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    keys.append(first.value)
        elif isinstance(child, ast.Subscript) and isinstance(child.value, ast.Attribute):
            if _dotted(child.value) == "os.environ" and isinstance(child.slice, ast.Constant):
                if isinstance(child.slice.value, str):
                    keys.append(child.slice.value)
    return keys


def _env_constant(base_tree: ast.Module, keys: Sequence[str]) -> str | None:
    """若模块存在与 env 键同值的 ``*_ENV``/``ENV_*``/``*_KEY`` 常量则返回其名。"""
    constants = _literal_assignment(base_tree)
    for name, value in constants.items():
        if value in keys and ENV_KEY_CONST_RE.search(name):
            return name
    return None


def _env_candidate(
    changed: Any,
    base_tree: ast.Module,
    base_node: ast.AST | None,
    target_node: ast.AST | None,
    module: str,
) -> _Candidate | None:
    """``env_contract``：断言 base 的 env 键名仍可观测；无法弱断言时不生成。"""
    base_keys = _env_keys(base_node)
    if not base_keys:
        return None
    target_keys = _env_keys(target_node)
    changed_keys = [key for key in base_keys if key not in target_keys]
    if not changed_keys:
        return None
    qualified_name = str(getattr(changed, "qualified_name", "") or module)
    symbol_id = str(getattr(changed, "symbol_id", "") or qualified_name)
    node_id, test_name = _identity(module, qualified_name, "env", symbol_id)
    constant = _env_constant(base_tree, changed_keys)
    lines = [f"# generated for {symbol_id}", "import inspect", f"import {module} as module", "", "", f"def {test_name}():"]
    if constant is not None:
        lines.append(f'    assert module.{constant} == {changed_keys[0]!r}, "env key constant changed"')
        rationale = (
            f"Module constant {constant} pins the base env key {changed_keys[0]!r}; "
            "assert it so an env-key edit becomes observable."
        )
    else:
        lines.append("    source = inspect.getsource(module)")
        for key in changed_keys:
            lines.append(f'    assert {key!r} in source, "env key {key} disappeared"')
        rationale = (
            f"weak:source-scan: the base snapshot resolves an env key ({', '.join(changed_keys)}) that is gone "
            "after the change; scan the module source so the regression is observable."
        )
    return _Candidate("env_contract", ModelSuggestion(node_id, "\n".join(lines) + "\n", rationale))


def _operand_value(node: ast.AST, param: str, value: Any) -> Any:
    if isinstance(node, ast.Name) and node.id == param:
        return value
    if isinstance(node, ast.Constant) and isinstance(node.value, LITERAL_TYPES):
        return node.value
    return _UNRESOLVED


def _isinstance_value(type_node: ast.AST, value: Any) -> bool | None:
    name = _annotation_name(type_node)
    if name == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "float":
        return isinstance(value, float)
    if name == "str":
        return isinstance(value, str)
    if name == "bool":
        return isinstance(value, bool)
    return None


_CMP = {
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
}


def _eval_condition(node: ast.AST, param: str, value: Any) -> bool | None:
    """对守卫条件做保守求值；无法判定时返回 None（调用方据此放弃生成）。"""
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        left = _operand_value(node.left, param, value)
        right = _operand_value(node.comparators[0], param, value)
        if left is _UNRESOLVED or right is _UNRESOLVED:
            return None
        handler = _CMP.get(type(node.ops[0]))
        return None if handler is None else bool(handler(left, right))
    if isinstance(node, ast.BoolOp):
        results = [_eval_condition(item, param, value) for item in node.values]
        if any(result is None for result in results):
            return None
        return all(results) if isinstance(node.op, ast.And) else any(results)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        inner = _eval_condition(node.operand, param, value)
        return None if inner is None else not inner
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "isinstance":
        if len(node.args) >= 2 and isinstance(node.args[0], ast.Name) and node.args[0].id == param:
            return _isinstance_value(node.args[1], value)
    return None


def _guard_raises(node: ast.AST, param: str, value: Any) -> bool | None:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    unknown = False
    for stmt in node.body:
        if not isinstance(stmt, ast.If):
            continue
        raises = any(isinstance(item, ast.Raise) for item in stmt.body)
        result = _eval_condition(stmt.test, param, value)
        if result is None:
            unknown = True
        elif result and raises:
            return True
    return None if unknown else False


def _comparison_constants(node: ast.AST, param: str) -> list[tuple[type, float]]:
    found: list[tuple[type, float]] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Compare) or len(child.ops) != 1:
            continue
        op = type(child.ops[0])
        left, right = child.left, child.comparators[0]
        if isinstance(left, ast.Name) and left.id == param and isinstance(right, ast.Constant):
            if isinstance(right.value, (int, float)) and not isinstance(right.value, bool):
                found.append((op, right.value))
        elif isinstance(right, ast.Name) and right.id == param and isinstance(left, ast.Constant):
            if isinstance(left.value, (int, float)) and not isinstance(left.value, bool):
                flip = {ast.Gt: ast.Lt, ast.GtE: ast.LtE, ast.Lt: ast.Gt, ast.LtE: ast.GtE}
                found.append((flip.get(type(op), op), left.value))
    return found


def _violating_value(op: type, threshold: float) -> int | float:
    if op is ast.Gt:
        return threshold + 1
    if op is ast.GtE:
        return threshold - 1
    if op is ast.Lt:
        return threshold - 1
    if op is ast.LtE:
        return threshold + 1
    return threshold


def _guard_candidate(
    changed: Any,
    base_node: ast.AST | None,
    target_node: ast.AST | None,
    module: str,
) -> _Candidate | None:
    """``guard_contract``：围绕 base/target 比较常量的边界断言 base 语义。"""
    if not isinstance(base_node, ast.FunctionDef) or not isinstance(target_node, ast.FunctionDef):
        return None
    params = _parameter_list(base_node)
    if len(params) != 1:
        return None
    param, annotation, has_default, _ = params[0]
    if has_default or (_annotation_name(annotation) not in {"int", "float"}):
        return None
    base_consts = {(op, value) for op, value in _comparison_constants(base_node, param)}
    new_consts = [
        (op, value)
        for op, value in _comparison_constants(target_node, param)
        if (op, value) not in base_consts
    ]
    if not new_consts:
        return None
    assertions: list[tuple[Any, Any]] = []
    for op, threshold in new_consts:
        value = _violating_value(op, threshold)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        raises = _guard_raises(base_node, param, value)
        if raises is None or raises:  # base 必然抛错或无法判定时不生成
            continue
        returns_param = _returns_param(base_node, param)
        expected = value if returns_param else None
        if expected is None:
            continue
        assertions.append((value, expected))
    if not assertions:
        return None
    qualified_name = str(getattr(changed, "qualified_name", "") or module)
    symbol_id = str(getattr(changed, "symbol_id", "") or qualified_name)
    node_id, test_name = _identity(module, qualified_name, "guard", symbol_id)
    lines = [f"# generated for {symbol_id}", f"import {module} as module", "", "", f"def {test_name}():"]
    for value, expected in assertions:
        lines.append(f'    assert module.{qualified_name}({value!r}) == {expected!r}, "guard boundary changed"')
    rationale = (
        f"{qualified_name} widens a comparison bound after the change; assert the base snapshot behaviour at the "
        "boundary probe so the tightened/loosened guard is caught."
    )
    return _Candidate("guard_contract", ModelSuggestion(node_id, "\n".join(lines) + "\n", rationale))


def _parameter_list(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[tuple[str, ast.AST | None, bool, ast.AST | None]]:
    """``(name, annotation, has_default, default_expr)``，按源码顺序，去掉 self。"""
    arguments = node.args
    positional = [*arguments.posonlyargs, *arguments.args]
    defaults = [None] * (len(positional) - len(arguments.defaults)) + list(arguments.defaults)
    params: list[tuple[str, ast.AST | None, bool, ast.AST | None]] = []
    for arg, default in zip(positional, defaults):
        params.append((arg.arg, arg.annotation, default is not None, default))
    for arg, default in zip(arguments.kwonlyargs, arguments.kw_defaults):
        params.append((arg.arg, arg.annotation, default is not None, default))
    return [item for item in params if item[0] != "self"]


def _returns_param(node: ast.AST, param: str) -> bool:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    body = [stmt for stmt in node.body if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))]
    if not body or not isinstance(body[-1], ast.Return) or not isinstance(body[-1].value, ast.Name):
        return False
    return body[-1].value.id == param


def _class_symbol(request: "GenerationRequest", name: str) -> Any:
    for symbol in request.symbols:
        if _kind(getattr(symbol, "kind", "")) == "class" and str(getattr(symbol, "qualified_name", "")) == name:
            return symbol
    return None


def _model_fields(request: "GenerationRequest", name: str) -> list[str]:
    prefix = f"{name}."
    fields: list[str] = []
    for symbol in request.symbols:
        if _kind(getattr(symbol, "kind", "")) != "field":
            continue
        qualified = str(getattr(symbol, "qualified_name", ""))
        if qualified.startswith(prefix) and "." not in qualified[len(prefix) :]:
            fields.append(qualified[len(prefix) :])
    return sorted(fields)


def _annotation_model(annotation: ast.AST | None) -> tuple[bool, str] | None:
    """返回 ``(is_list, class_name)``；非模型注解返回 None。"""
    if isinstance(annotation, ast.Name):
        return (False, annotation.id)
    if isinstance(annotation, ast.Subscript) and isinstance(annotation.value, ast.Name):
        if annotation.value.id in {"list", "List", "Sequence"} and isinstance(annotation.slice, ast.Name):
            return (True, annotation.slice.id)
    return None


def _model_instance_expr(request: "GenerationRequest", class_name: str) -> tuple[str, str] | None:
    symbol = _class_symbol(request, class_name)
    if symbol is None:
        return None
    module = module_path(_normalize(str(getattr(symbol, "file_path", ""))))
    if module is None:
        return None
    fields = _model_fields(request, class_name)
    args = ", ".join(f"{field}=1" for field in fields)
    return module, f"{class_name}({args})"


def _owner_constructible(base_tree: ast.Module, owner: str) -> bool:
    class_def = _class_def(base_tree, owner)
    if class_def is None:
        return False
    for node in class_def.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "__init__":
            return all(has_default or name == "self" for name, _, has_default, _ in _parameter_list(node))
    return True


def _merged_constants(request: "GenerationRequest") -> dict[str, Any]:
    """合并 base 各变更文件里的模块级字面量常量，供解析导入别名默认值。"""
    merged: dict[str, Any] = {}
    for _, tree in _parse_sources(getattr(request, "base_sources", None)).values():
        for name, value in _literal_assignment(tree).items():
            merged.setdefault(name, value)
    return merged


def _replay_candidate(
    changed: Any,
    base_entry: tuple[str, ast.Module],
    base_node: ast.AST | None,
    target_node: ast.AST | None,
    module: str,
    request: "GenerationRequest",
) -> _Candidate | None:
    """``state_contract``：按 base 方法体在运行期重算期望值，对比 target 的实际返回值。

    该方法体以静态转写方式内联进生成的 pytest（不使用 ``exec``），因此既能覆盖
    返回字典/数值/属性的实例方法，也不依赖对 target 行为的事后硬编码。
    """
    if not isinstance(base_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    if _env_keys(base_node) != _env_keys(target_node):
        return None  # env 键名变更交由 env_contract，避免生成无法判定的重放
    _, base_tree = base_entry
    constants = _merged_constants(request)
    kind = _kind(changed.kind)
    qualified_name = str(getattr(changed, "qualified_name", "") or "")
    setup: list[str] = []
    call_args: list[str] = []
    imports: dict[str, set[str]] = {}
    owner = qualified_name.split(".")[-2] if kind == "method" and "." in qualified_name else None
    if owner is not None:
        if not _owner_constructible(base_tree, owner):
            return None
        setup.append(f"service = module.{owner}()")
    for name, annotation, has_default, default in _parameter_list(base_node):
        model = _annotation_model(annotation)
        instance = _model_instance_expr(request, model[1]) if model is not None else None
        if model is not None and instance is not None:
            is_list, class_name = model
            model_module, expr = instance
            imports.setdefault(model_module, set()).add(class_name)
            value = f"[{expr}]" if is_list else expr
        elif has_default:
            value = _literal_default(default, constants)
            if value is _UNRESOLVED:
                probe = PROBE_BY_ANNOTATION.get(_annotation_name(annotation) or "")
                if probe is None:
                    return None
                value = probe
            value = repr(value)
        else:
            probe = PROBE_BY_ANNOTATION.get(_annotation_name(annotation) or "")
            if probe is None and isinstance(annotation, ast.BinOp):
                probe = "probe"
            if probe is None:
                return None
            value = repr(probe)
        setup.append(f"{name} = {value}")
        call_args.append(name)

    transformed = _SelfToInstance(owner is not None).visit(copy.deepcopy(base_node))
    body = [
        stmt
        for stmt in transformed.body
        if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))
    ]
    try:
        replay_source = ast.unparse(ast.Module(body=body, type_ignores=[]))
    except (ValueError, RecursionError):
        return None
    if not replay_source.strip():
        return None

    symbol_id = str(getattr(changed, "symbol_id", "") or qualified_name or module)
    node_id, test_name = _identity(module, qualified_name or module, "state", symbol_id)
    lines = [f"# generated for {symbol_id}", f"import {module} as module", f"from {module} import *"]
    for model_module in sorted(imports):
        lines.append(f"from {model_module} import {', '.join(sorted(imports[model_module]))}")
    lines.extend(["", "", f"def {test_name}():"])
    for line in setup:
        lines.append(f"    {line}")
    lines.append("    def _replay():")
    for line in replay_source.splitlines():
        lines.append(f"        {line}" if line.strip() else "")
    lines.append("    expected = _replay()")
    call = f"module.{qualified_name}({', '.join(call_args)})" if owner is None else f"service.{qualified_name.split('.')[-1]}({', '.join(call_args)})"
    lines.append(f"    actual = {call}")
    lines.append('    assert actual == expected, "behaviour changed"')
    rationale = (
        f"Replay the base snapshot body of {qualified_name} against the live instance and assert the target returns "
        "the same value, so a changed branch/constant in an otherwise stable-looking method is caught."
    )
    return _Candidate("state_contract", ModelSuggestion(node_id, "\n".join(lines) + "\n", rationale))


def _literal_default(node: ast.AST | None, constants: Mapping[str, Any]) -> Any:
    if node is None:
        return _UNRESOLVED
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        pass
    if isinstance(node, ast.Name) and node.id in constants:
        return constants[node.id]
    return _UNRESOLVED


class _SelfToInstance(ast.NodeTransformer):
    """把方法体里的 ``self`` 替换成生成测试中的 ``service`` 实例。"""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if self.enabled and node.id == "self":
            return ast.copy_location(ast.Name(id="service", ctx=node.ctx), node)
        return node


def _boundary_templates(
    changed: Any,
    request: "GenerationRequest",
    base_entry: tuple[str, ast.Module],
    target_entry: tuple[str, ast.Module] | None,
    base_node: ast.AST | None,
    target_node: ast.AST | None,
    module: str,
) -> list[_Candidate]:
    """C-GEN@3 的三类边界模板；仅在既有模板无产物时启用。"""
    _, base_tree = base_entry
    candidates: list[_Candidate] = []
    env = _env_candidate(changed, base_tree, base_node, target_node, module)
    if env is not None:
        candidates.append(env)
    state = _replay_candidate(changed, base_entry, base_node, target_node, module, request)
    if state is not None:
        candidates.append(state)
    guard = _guard_candidate(changed, base_node, target_node, module)
    if guard is not None:
        candidates.append(guard)
    return candidates


def _templates_for(
    changed: Any,
    request: "GenerationRequest",
    base_sources: dict[str, tuple[str, ast.Module]],
    target_sources: dict[str, tuple[str, ast.Module]],
) -> list[_Candidate]:
    file_path = _normalize(str(getattr(changed, "file_path", "")))
    module = module_path(file_path)
    if module is None:
        return []
    kind = _kind(changed.kind)
    qualified_name = str(getattr(changed, "qualified_name", "") or "")
    if not qualified_name:
        return []
    base_entry = base_sources.get(file_path)
    if base_entry is None:
        return []
    base_source, base_tree = base_entry
    target_entry = target_sources.get(file_path)
    node = _find_definition(base_tree, kind, qualified_name)
    target_node = _find_definition(target_entry[1], kind, qualified_name) if target_entry is not None else None
    state = _change_state(base_source, node, target_entry, target_node)

    if kind in {"class", "function", "method", "field"} and state == "missing":
        candidate = _existence_candidate(changed, request.symbols)
        return [candidate] if candidate is not None else []
    if kind == "route":
        candidate = _route_candidate(changed, base_entry, target_entry, module)
        return [candidate] if candidate is not None else []
    if kind in {"function", "method"} and state == "changed":
        results: list[_Candidate] = []
        signature = _signature_candidate(changed, node, target_node, module)
        if signature is not None:
            results.append(signature)
        else:
            results.extend(_function_constant_candidates(changed, base_entry, node, module))
        if not results:
            results.extend(_boundary_templates(changed, request, base_entry, target_entry, node, target_node, module))
        return results
    if kind == "field" and state == "changed":
        candidate = _field_default_candidate(changed, node, target_node, module)
        return [candidate] if candidate is not None else []
    if kind == "file":
        results = _constant_candidates(changed, base_entry, target_entry, module)
        imported = _import_candidate(changed, base_entry, target_entry, module)
        if imported is not None:
            results.append(imported)
        return results
    return []


class MockModelAdapter:
    """离线确定性适配器：同样的请求始终得到同样的建议。"""

    name = "mock"

    def suggest(self, request: "GenerationRequest") -> list[ModelSuggestion]:
        base_sources = _parse_sources(getattr(request, "base_sources", None))
        target_sources = _parse_sources(getattr(request, "target_sources", None))
        candidates: list[_Candidate] = []
        seen: set[str] = set()
        for changed in sorted(request.changed, key=lambda item: item.symbol_id):
            symbol_id = str(getattr(changed, "symbol_id", ""))
            if symbol_id in seen:
                continue
            seen.add(symbol_id)
            if not base_sources:
                # 没有源码上下文时退回 C-GEN@1 的存在性断言，保持向后兼容。
                fallback = _existence_candidate(changed, request.symbols)
                if fallback is not None:
                    candidates.append(fallback)
                continue
            candidates.extend(_templates_for(changed, request, base_sources, target_sources))
        candidates.sort(key=lambda item: (TEMPLATE_ORDER.get(item.template, len(TEMPLATE_ORDER)), item.suggestion.test_name))
        return [item.suggestion for item in candidates[:MAX_SUGGESTIONS]]


class OllamaModelAdapter:
    """可选适配器：调用本地 Ollama ``/api/generate``。"""

    name = "ollama"

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen2.5-coder:7b",
        timeout_s: int = 60,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s

    def suggest(self, request: "GenerationRequest") -> list[ModelSuggestion]:
        payload = json.dumps(
            {"model": self.model, "prompt": _prompt(request), "stream": False, "format": "json"}
        ).encode("utf-8")
        http_request = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_request, timeout=self.timeout_s) as response:
                body = response.read()
        except (urllib.error.URLError, OSError) as exc:
            raise ModelUnavailableError(f"ollama request failed: {exc}") from exc
        try:
            response_payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelUnavailableError(f"ollama returned an unreadable body: {exc}") from exc
        raw = response_payload.get("response") if isinstance(response_payload, dict) else None
        if not isinstance(raw, str):
            raise ModelUnavailableError("ollama response has no text payload")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ModelUnavailableError(f"ollama response is not JSON: {exc}") from exc
        return _suggestions_from_payload(parsed)


def _request_payload(request: "GenerationRequest") -> dict[str, Any]:
    return {
        "changed": [
            {
                "symbol_id": item.symbol_id,
                "kind": _kind(item.kind),
                "change_type": _kind(item.change_type),
                "qualified_name": item.qualified_name,
                "file_path": item.file_path,
            }
            for item in request.changed
        ],
        "symbols": [
            {
                "symbol_id": item.symbol_id,
                "kind": _kind(item.kind),
                "qualified_name": item.qualified_name,
                "file_path": item.file_path,
            }
            for item in request.symbols[:200]
        ],
        "existing_tests": [item.node_id for item in request.existing_tests[:50]],
        "style_samples": list(request.style_samples[:2]),
    }


def _prompt(request: "GenerationRequest") -> str:
    return (
        "You generate pytest tests for a restricted regression-verification workflow.\n"
        'Only return JSON of the form {"tests": [{"test_name": str, "content": str, "rationale": str}]}.\n'
        "test_name must be a pytest node id like tests/test_x.py::test_y.\n"
        "Tests must not use network, subprocesses or filesystem writes, and must assert the "
        "behaviour of the given changed symbols.\n"
        f"Context:\n{json.dumps(_request_payload(request), ensure_ascii=False, sort_keys=True)}"
    )


def _suggestions_from_payload(payload: Any) -> list[ModelSuggestion]:
    if not isinstance(payload, dict):
        raise ModelUnavailableError("ollama response is not a JSON object")
    raw_tests = payload.get("tests")
    if not isinstance(raw_tests, list):
        return []
    suggestions: list[ModelSuggestion] = []
    for entry in raw_tests:
        if not isinstance(entry, dict):
            continue
        content = entry.get("content")
        test_name = entry.get("test_name")
        if not isinstance(content, str) or not content.strip():
            continue
        if not isinstance(test_name, str) or "::" not in test_name:
            continue
        file_path = test_name.split("::", 1)[0].replace("\\", "/")
        if not file_path.startswith("tests/"):
            continue
        suggestions.append(
            ModelSuggestion(
                test_name=test_name,
                content=content,
                rationale=str(entry.get("rationale") or "model suggestion"),
            )
        )
    return suggestions


def default_model_adapter() -> ModelAdapter:
    backend = os.environ.get("CHANGEPROOF_LLM", "mock").strip().lower()
    if backend == "ollama":
        return OllamaModelAdapter(
            base_url=os.environ.get("CHANGEPROOF_OLLAMA_URL", "http://localhost:11434"),
            model=os.environ.get("CHANGEPROOF_OLLAMA_MODEL", "qwen2.5-coder:7b"),
        )
    return MockModelAdapter()