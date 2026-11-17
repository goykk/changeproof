"""受限测试生成：请求构建、静态安全检查与生成物封装（C-GEN@1 / C-GEN@2）。

生成物一律带 ``analysis_source="model"``，只进入报告的 ``model_suggestions``
字段，不得混入静态事实。请求可携带 base/target 两版源码快照，供模板按
``existence``/``constant_value``/``signature_contract``/``route_contract``/
``import_contract`` 产出可判定的断言。
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from core.schema.types import ChangedSymbol, Symbol, TestCaseInfo

PENDING_REVIEW = "pending_review"
VERIFIED = "verified"
REJECTED = "rejected"

# 静态白名单：只允许断言类导入与目标模块导入；其余标准库一律拒绝。
ALLOWED_MODULES = frozenset({"pytest", "inspect", "json", "fastapi", "importlib"})
FORBIDDEN_MODULES = frozenset(
    {
        "subprocess",
        "socket",
        "requests",
        "urllib",
        "httpx",
        "http",
        "pathlib",
        "shutil",
        "tempfile",
        "io",
        "multiprocessing",
        "threading",
        "ctypes",
        "ftplib",
    }
)
FORBIDDEN_CALL_NAMES = frozenset({"exec", "eval", "compile", "open", "__import__", "input", "breakpoint"})
FORBIDDEN_ATTRIBUTE_CALLS = frozenset(
    {"os.system", "os.popen", "os.remove", "os.unlink", "os.rename", "builtins.open", "io.open"}
)


@dataclass(frozen=True)
class GenerationRequest:
    """生成输入：变更符号、符号上下文、既有测试、风格样例与两版源码。"""

    changed: tuple[ChangedSymbol, ...]
    symbols: tuple[Symbol, ...]
    existing_tests: tuple[TestCaseInfo, ...]
    style_samples: tuple[str, ...]
    base_sources: dict[str, str] = field(default_factory=dict)
    target_sources: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class GeneratedTest:
    test_node_id: str
    content: str
    status: str
    rationale: str
    analysis_source: str = "model"

    def as_dict(self) -> dict[str, str]:
        return {
            "test_node_id": self.test_node_id,
            "content": self.content,
            "status": self.status,
            "rationale": self.rationale,
            "analysis_source": self.analysis_source,
        }


def _dotted_name(node: ast.AST) -> str | None:
    """还原 ``a.b.c`` 形式的属性调用链；链底不是 Name 时返回 None。"""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _import_allowed(top_level: str) -> bool:
    """白名单模块与仓库内目标模块放行；其余标准库/已知危险模块拒绝。"""
    if top_level in ALLOWED_MODULES:
        return True
    if top_level in FORBIDDEN_MODULES:
        return False
    return top_level not in sys.stdlib_module_names


def static_check(content: str) -> list[str]:
    """返回违规项列表；空列表表示通过静态安全检查。"""
    try:
        tree = ast.parse(content)
    except SyntaxError as exc:
        return [f"syntax_error: {exc.msg} (line {exc.lineno})"]

    problems: list[str] = []
    has_test_definition = any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
        for node in ast.walk(tree)
    )
    if not has_test_definition:
        problems.append("missing_def_test")

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALL_NAMES:
                problems.append(f"forbidden_call: {node.func.id}")
            elif isinstance(node.func, ast.Attribute):
                dotted = _dotted_name(node.func)
                if dotted is not None and (
                    dotted.split(".")[0] in FORBIDDEN_MODULES or dotted in FORBIDDEN_ATTRIBUTE_CALLS
                ):
                    problems.append(f"forbidden_call: {dotted}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if not _import_allowed(alias.name.split(".")[0]):
                    problems.append(f"forbidden_import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            top_level = (node.module or "").split(".")[0]
            if top_level and not _import_allowed(top_level):
                problems.append(f"forbidden_import: {node.module}")
    return sorted(set(problems))


def build_generation_request(
    changed: Sequence[ChangedSymbol],
    symbols: Sequence[Symbol],
    existing_tests: Sequence[TestCaseInfo],
    style_samples: Sequence[str],
    base_sources: Mapping[str, str] | None = None,
    target_sources: Mapping[str, str] | None = None,
) -> GenerationRequest:
    return GenerationRequest(
        changed=tuple(changed),
        symbols=tuple(symbols),
        existing_tests=tuple(existing_tests),
        style_samples=tuple(style_samples),
        base_sources=dict(base_sources or {}),
        target_sources=dict(target_sources or {}),
    )


def generate_test_suggestions(request: GenerationRequest, adapter) -> list[GeneratedTest]:
    """把模型建议转成生成物；静态检查不通过的标记为 ``rejected``。"""
    generated: list[GeneratedTest] = []
    for suggestion in adapter.suggest(request):
        problems = static_check(suggestion.content)
        generated.append(
            GeneratedTest(
                test_node_id=suggestion.test_name,
                content=suggestion.content,
                status=PENDING_REVIEW if not problems else REJECTED,
                rationale=suggestion.rationale,
            )
        )
    return generated