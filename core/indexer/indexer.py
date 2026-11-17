"""基于 AST 与框架约定构建仓库符号图。"""

from __future__ import annotations

import ast
import tokenize
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from core.indexer.framework import (
    AttributeAccess,
    IncludeSpec,
    RouteSpec,
    RouterSpec,
    callee_name,
    clean_route_prefix,
    collect_attribute_accesses,
    collect_local_types,
    join_route_path,
    parse_http_client_calls,
    parse_include_router,
    parse_model_fields,
    parse_route_decorators,
    parse_router_declarations,
    route_path_matches,
)
from core.schema.ids import (
    field_symbol_id,
    file_symbol_id,
    qualname_symbol_id,
    route_symbol_id,
    test_symbol_id,
)
from core.schema.types import (
    AnalysisSource,
    Confidence,
    DependencyEdge,
    EdgeType,
    Symbol,
    SymbolKind,
    TestCaseInfo,
)

_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "site-packages",
        ".eggs",
    }
)


@dataclass
class IndexResult:
    symbols: list[Symbol] = field(default_factory=list)
    edges: list[DependencyEdge] = field(default_factory=list)
    tests: list[TestCaseInfo] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _ParsedFile:
    rel_path: str
    module: str
    line_count: int
    tree: ast.Module


@dataclass(frozen=True)
class _ImportBinding:
    target_rel: str | None
    target_name: str | None


def index_repository(
    snapshot_dir: str,
    repo_id: str,
    commit_sha: str,
    include_typescript: bool = False,
) -> IndexResult:
    """索引 ``snapshot_dir`` 下的 Python 快照（可选合并 TypeScript 索引级支持）。"""
    root = Path(snapshot_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"snapshot directory not found: {snapshot_dir}")

    parsed: list[_ParsedFile] = []
    warnings = _Warnings()
    for rel_path in _iter_python_files(root):
        parsed_file, message = _parse_file(root, rel_path)
        if message is not None:
            warnings.add(message)
        # 降级解析（语法不兼容但保留了模块级导入）不能被丢弃，否则跨模块导入边整片断链。
        if parsed_file.tree.body or message is None:
            parsed.append(parsed_file)
    result = _Indexer(repo_id, commit_sha, parsed, warnings).build()
    if not include_typescript:
        return result

    from .typescript import index_typescript

    ts_symbols, ts_edges, ts_tests, ts_warnings = index_typescript(str(root), repo_id, commit_sha)
    return IndexResult(
        symbols=[*result.symbols, *ts_symbols],
        edges=[*result.edges, *ts_edges],
        tests=[*result.tests, *ts_tests],
        warnings=[*result.warnings, *ts_warnings],
    )


class _Warnings:
    def __init__(self) -> None:
        self._items: list[str] = []
        self._seen: set[str] = set()

    def add(self, message: str) -> None:
        if message not in self._seen:
            self._seen.add(message)
            self._items.append(message)

    def items(self) -> list[str]:
        return list(self._items)


class _EdgeCollector:
    def __init__(self) -> None:
        self._edges: dict[tuple[str, str, str], DependencyEdge] = {}

    def add(self, edge: DependencyEdge) -> None:
        self._edges.setdefault((edge.source_id, edge.target_id, edge.edge_type.value), edge)

    def items(self) -> list[DependencyEdge]:
        return list(self._edges.values())


class _Indexer:
    def __init__(
        self,
        repo_id: str,
        commit_sha: str,
        parsed: list[_ParsedFile],
        warnings: _Warnings,
    ) -> None:
        self.repo_id = repo_id
        self.commit_sha = commit_sha
        self.parsed = parsed
        self.warnings = warnings
        self.symbols: dict[str, Symbol] = {}
        self.edges = _EdgeCollector()
        self.tests: dict[str, TestCaseInfo] = {}
        self.file_symbols: dict[str, Symbol] = {}
        self.symbol_by_qual: dict[tuple[str, str], Symbol] = {}
        self.file_depends: dict[str, list[str]] = {}
        self.module_globals: dict[str, set[str]] = {}
        self.class_symbols: dict[tuple[str, str], Symbol] = {}
        self.callables: list[tuple[str, ast.AST, Symbol]] = []
        self.modules: dict[str, str] = {}
        self.bindings: dict[tuple[str, str], _ImportBinding] = {}
        self.fields: dict[tuple[str, str], dict[str, Symbol]] = {}
        self.name_index: dict[str, list[Symbol]] = {}

    def build(self) -> IndexResult:
        self._collect_modules()
        self._collect_module_symbols()
        for parsed_file in self.parsed:
            self._collect_imports(parsed_file)
        for parsed_file in self.parsed:
            self._collect_inheritance(parsed_file)
        self._collect_calls()
        self._collect_global_uses()
        self._collect_fields()
        self._collect_routes()
        self._collect_test_route_dependencies()

        return IndexResult(
            symbols=sorted(
                self.symbols.values(),
                key=lambda symbol: (
                    symbol.file_path,
                    symbol.line_start,
                    symbol.kind.value,
                    symbol.symbol_id,
                ),
            ),
            edges=sorted(
                self.edges.items(),
                key=lambda edge: (
                    edge.file_path,
                    edge.line_start,
                    edge.edge_type.value,
                    edge.source_id,
                    edge.target_id,
                ),
            ),
            tests=sorted(self.tests.values(), key=lambda test: test.node_id),
            warnings=self.warnings.items(),
        )

    # -- 符号收集 ---------------------------------------------------------

    def _collect_modules(self) -> None:
        for parsed_file in self.parsed:
            self.modules[parsed_file.module] = parsed_file.rel_path

    def _collect_module_symbols(self) -> None:
        for parsed_file in self.parsed:
            self._add_file_symbol(parsed_file)
            # 文件级 Depends 扫描：覆盖 `Annotated[..., Depends(x)]` 类型别名（真实仓库常见）。
            file_depends: list[str] = []
            for node in ast.walk(parsed_file.tree):
                if not isinstance(node, ast.Call):
                    continue
                name = _depends_call_name(node)
                if name and name not in file_depends:
                    file_depends.append(name)
            self.file_depends[parsed_file.rel_path] = file_depends
            module_globals: set[str] = set()
            for node in parsed_file.tree.body:
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            module_globals.add(target.id)
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    module_globals.add(node.target.id)
            self.module_globals[parsed_file.rel_path] = module_globals
        for parsed_file in self.parsed:
            for node in parsed_file.tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self._collect_function(parsed_file, node, None, "")
                elif isinstance(node, ast.ClassDef):
                    self._collect_class(parsed_file, node)

    def _add_file_symbol(self, parsed_file: _ParsedFile) -> Symbol:
        symbol = Symbol(
            symbol_id=file_symbol_id(parsed_file.rel_path),
            repo_id=self.repo_id,
            commit_sha=self.commit_sha,
            kind=SymbolKind.FILE,
            qualified_name=parsed_file.rel_path,
            file_path=parsed_file.rel_path,
            line_start=1,
            line_end=parsed_file.line_count,
            parent_id=None,
            metadata={"module": parsed_file.module},
        )
        self.file_symbols[parsed_file.rel_path] = symbol
        return self._register(symbol)

    def _collect_class(self, parsed_file: _ParsedFile, node: ast.ClassDef) -> Symbol:
        start, end = _node_span(node)
        symbol = self._register(
            Symbol(
                symbol_id=qualname_symbol_id(parsed_file.rel_path, node.name),
                repo_id=self.repo_id,
                commit_sha=self.commit_sha,
                kind=SymbolKind.CLASS,
                qualified_name=node.name,
                file_path=parsed_file.rel_path,
                line_start=start,
                line_end=end,
                parent_id=file_symbol_id(parsed_file.rel_path),
            )
        )
        self.class_symbols[(parsed_file.rel_path, node.name)] = symbol
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._collect_function(parsed_file, child, node.name, f"{node.name}.")
        return symbol

    def _collect_function(
        self,
        parsed_file: _ParsedFile,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        class_name: str | None,
        qualname_prefix: str,
    ) -> Symbol:
        start, end = _node_span(node)
        qualname = f"{qualname_prefix}{node.name}"
        is_test = _is_test_file(parsed_file.rel_path) and node.name.startswith("test_")
        if is_test:
            suffix = f"{class_name}::{node.name}" if class_name else node.name
            symbol_id = test_symbol_id(parsed_file.rel_path, suffix)
            kind = SymbolKind.TEST
            qualified_name = suffix
        else:
            symbol_id = qualname_symbol_id(parsed_file.rel_path, qualname)
            kind = SymbolKind.METHOD if class_name else SymbolKind.FUNCTION
            qualified_name = qualname

        symbol = self._register(
            Symbol(
                symbol_id=symbol_id,
                repo_id=self.repo_id,
                commit_sha=self.commit_sha,
                kind=kind,
                qualified_name=qualified_name,
                file_path=parsed_file.rel_path,
                line_start=start,
                line_end=end,
                parent_id=(
                    qualname_symbol_id(parsed_file.rel_path, class_name)
                    if class_name
                    else file_symbol_id(parsed_file.rel_path)
                ),
                metadata={"depends": _depends_targets(node)},
            )
        )
        self.callables.append((parsed_file.rel_path, node, symbol))
        self._index_callable_name(symbol)

        if is_test:
            self.tests.setdefault(
                symbol_id,
                TestCaseInfo(
                    repo_id=self.repo_id,
                    commit_sha=self.commit_sha,
                    node_id=symbol_id,
                    file_path=parsed_file.rel_path,
                    name=node.name,
                    markers=_test_markers(node),
                ),
            )
            self.edges.add(
                DependencyEdge(
                    repo_id=self.repo_id,
                    commit_sha=self.commit_sha,
                    source_id=file_symbol_id(parsed_file.rel_path),
                    target_id=symbol_id,
                    edge_type=EdgeType.DEFINES,
                    analysis_source=AnalysisSource.AST,
                    confidence=Confidence.CERTAIN,
                    file_path=parsed_file.rel_path,
                    line_start=start,
                    line_end=end,
                )
            )
        return symbol

    def _register(self, symbol: Symbol) -> Symbol:
        existing = self.symbols.get(symbol.symbol_id)
        if existing is not None:
            return existing
        self.symbols[symbol.symbol_id] = symbol
        self.symbol_by_qual.setdefault((symbol.file_path, symbol.qualified_name), symbol)
        return symbol

    def _index_callable_name(self, symbol: Symbol) -> None:
        leaf = symbol.qualified_name.replace("::", ".").split(".")[-1]
        bucket = self.name_index.setdefault(leaf, [])
        if all(item.symbol_id != symbol.symbol_id for item in bucket):
            bucket.append(symbol)

    # -- 导入 -------------------------------------------------------------

    def _collect_imports(self, parsed_file: _ParsedFile) -> None:
        file_symbol = self.file_symbols[parsed_file.rel_path]
        for node in ast.walk(parsed_file.tree):
            if isinstance(node, ast.Import):
                self._collect_plain_import(parsed_file, node, file_symbol)
            elif isinstance(node, ast.ImportFrom):
                self._collect_from_import(parsed_file, node, file_symbol)

    def _collect_plain_import(
        self,
        parsed_file: _ParsedFile,
        node: ast.Import,
        file_symbol: Symbol,
    ) -> None:
        for alias in node.names:
            target_rel = self.modules.get(alias.name)
            if alias.asname:
                self.bindings[(parsed_file.rel_path, alias.asname)] = _ImportBinding(
                    target_rel, None
                )
            if target_rel is None:
                continue
            self._add_import_edge(parsed_file, node, file_symbol, target_rel, None)

    def _collect_from_import(
        self,
        parsed_file: _ParsedFile,
        node: ast.ImportFrom,
        file_symbol: Symbol,
    ) -> None:
        module = node.module or ""
        module_rel = _resolve_module(module, node.level, parsed_file.rel_path, self.modules)
        for alias in node.names:
            if alias.name == "*":
                if module_rel is not None:
                    self._add_import_edge(
                        parsed_file, node, file_symbol, module_rel, None, Confidence.INFERRED
                    )
                continue

            sub_module = f"{module}.{alias.name}" if module else alias.name
            sub_rel = _resolve_module(
                sub_module, node.level, parsed_file.rel_path, self.modules
            )
            local_name = alias.asname or alias.name
            if sub_rel is not None:
                self.bindings[(parsed_file.rel_path, local_name)] = _ImportBinding(
                    sub_rel, None
                )
                self._add_import_edge(parsed_file, node, file_symbol, sub_rel, None)
                continue
            if module_rel is None:
                continue

            target_symbol = self.symbol_by_qual.get((module_rel, alias.name))
            self.bindings[(parsed_file.rel_path, local_name)] = _ImportBinding(
                module_rel, alias.name
            )
            if target_symbol is None:
                self.warnings.add(
                    f"{parsed_file.rel_path}:{node.lineno}: 导入 {module}.{alias.name} "
                    "无法解析为仓库内符号，按文件级依赖记录"
                )
                self._add_import_edge(
                    parsed_file, node, file_symbol, module_rel, None, Confidence.INFERRED
                )
            else:
                self._add_import_edge(
                    parsed_file, node, file_symbol, module_rel, target_symbol
                )

    def _add_import_edge(
        self,
        parsed_file: _ParsedFile,
        node: ast.stmt,
        file_symbol: Symbol,
        target_rel: str,
        target_symbol: Symbol | None,
        confidence: Confidence = Confidence.CERTAIN,
    ) -> None:
        target_id = (
            target_symbol.symbol_id if target_symbol is not None else file_symbol_id(target_rel)
        )
        self.edges.add(
            DependencyEdge(
                repo_id=self.repo_id,
                commit_sha=self.commit_sha,
                source_id=file_symbol.symbol_id,
                target_id=target_id,
                edge_type=EdgeType.IMPORTS,
                analysis_source=AnalysisSource.AST,
                confidence=confidence,
                file_path=parsed_file.rel_path,
                line_start=node.lineno,
                line_end=node.end_lineno or node.lineno,
            )
        )
        module_id = file_symbol_id(target_rel)
        if target_symbol is not None and _is_test_file(parsed_file.rel_path):
            # 测试文件额外记录模块级导入边，使被导入模块的变更能反向触达该测试文件
            self.edges.add(
                DependencyEdge(
                    repo_id=self.repo_id,
                    commit_sha=self.commit_sha,
                    source_id=file_symbol.symbol_id,
                    target_id=module_id,
                    edge_type=EdgeType.IMPORTS,
                    analysis_source=AnalysisSource.AST,
                    confidence=Confidence.INFERRED,
                    file_path=parsed_file.rel_path,
                    line_start=node.lineno,
                    line_end=node.end_lineno or node.lineno,
                )
            )

    # -- 继承与调用 -------------------------------------------------------

    def _collect_inheritance(self, parsed_file: _ParsedFile) -> None:
        for node in parsed_file.tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            class_symbol = self.class_symbols.get((parsed_file.rel_path, node.name))
            if class_symbol is None:
                continue
            for base in node.bases:
                target = self._resolve_class_reference(parsed_file.rel_path, base)
                if target is None or target.symbol_id == class_symbol.symbol_id:
                    continue
                self.edges.add(
                    DependencyEdge(
                        repo_id=self.repo_id,
                        commit_sha=self.commit_sha,
                        source_id=class_symbol.symbol_id,
                        target_id=target.symbol_id,
                        edge_type=EdgeType.INHERITS,
                        analysis_source=AnalysisSource.AST,
                        confidence=Confidence.CERTAIN,
                        file_path=parsed_file.rel_path,
                        line_start=base.lineno,
                        line_end=base.end_lineno or base.lineno,
                    )
                )

    def _collect_calls(self) -> None:
        for rel_path, node, scope in self.callables:
            for stmt in node.body:
                for call in ast.walk(stmt):
                    if not isinstance(call, ast.Call):
                        continue
                    name = callee_name(call)
                    if not name:
                        continue
                    candidates = [
                        symbol
                        for symbol in self.name_index.get(name, ())
                        if symbol.symbol_id != scope.symbol_id
                    ]
                    if not candidates:
                        continue
                    if len(candidates) > 1:
                        self.warnings.add(
                            f"{rel_path}:{call.lineno}: 调用 {name} 无法唯一解析，未建立 CALLS 边"
                        )
                        continue
                    target = candidates[0]
                    confidence = (
                        Confidence.CERTAIN
                        if isinstance(call.func, ast.Name)
                        else Confidence.INFERRED
                    )
                    self.edges.add(
                        DependencyEdge(
                            repo_id=self.repo_id,
                            commit_sha=self.commit_sha,
                            source_id=scope.symbol_id,
                            target_id=target.symbol_id,
                            edge_type=EdgeType.CALLS,
                            analysis_source=AnalysisSource.AST,
                            confidence=confidence,
                            file_path=rel_path,
                            line_start=call.lineno,
                            line_end=call.end_lineno or call.lineno,
                        )
                    )

    def _collect_global_uses(self) -> None:
        """函数体引用本模块级变量 → `USES_GLOBAL` 边（target 为模块 FILE 符号）。

        模块级变量变更会落到 FILE 符号上，该边让"改 engine/常量 → 使用它的函数"
        能够被反向传播命中（C-IMPACT@1 / C-DI@1 的补充）。
        """
        for rel_path, node, scope in self.callables:
            names = self.module_globals.get(rel_path) or set()
            if not names:
                continue
            args = getattr(node, "args", None)
            params: set[str] = set()
            if args is not None:
                for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs):
                    params.add(arg.arg)
            local_assigns: set[str] = set()
            for stmt in getattr(node, "body", ()):  # type: ignore[union-attr]
                for sub in ast.walk(stmt):
                    if isinstance(sub, ast.Name) and isinstance(sub.ctx, (ast.Store, ast.Del)):
                        local_assigns.add(sub.id)
            for stmt in getattr(node, "body", ()):  # type: ignore[union-attr]
                for sub in ast.walk(stmt):
                    if not isinstance(sub, ast.Name) or not isinstance(sub.ctx, ast.Load):
                        continue
                    if sub.id not in names or sub.id in params or sub.id in local_assigns:
                        continue
                    self.edges.add(
                        DependencyEdge(
                            repo_id=self.repo_id,
                            commit_sha=self.commit_sha,
                            source_id=scope.symbol_id,
                            target_id=file_symbol_id(rel_path),
                            edge_type=EdgeType.USES_GLOBAL,
                            analysis_source=AnalysisSource.AST,
                            confidence=Confidence.INFERRED,
                            file_path=rel_path,
                            line_start=sub.lineno,
                            line_end=sub.lineno,
                        )
                    )

    def _resolve_class_reference(self, rel_path: str, expr: ast.expr) -> Symbol | None:
        if isinstance(expr, ast.Name):
            local = self.class_symbols.get((rel_path, expr.id))
            if local is not None:
                return local
            binding = self.bindings.get((rel_path, expr.id))
            if binding is not None and binding.target_name:
                return self.symbol_by_qual.get((binding.target_rel, binding.target_name))
            return None
        if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
            binding = self.bindings.get((rel_path, expr.value.id))
            if binding is None or binding.target_rel is None:
                return None
            return self.symbol_by_qual.get((binding.target_rel, expr.attr))
        return None

    # -- SQLAlchemy 字段 --------------------------------------------------

    def _collect_fields(self) -> None:
        for parsed_file in self.parsed:
            for spec in parse_model_fields(parsed_file.rel_path, parsed_file.tree):
                class_symbol = self.class_symbols.get(
                    (parsed_file.rel_path, spec.model_name)
                )
                if class_symbol is None:
                    continue
                symbol = self._register(
                    Symbol(
                        symbol_id=field_symbol_id(
                            parsed_file.rel_path, spec.model_name, spec.field_name
                        ),
                        repo_id=self.repo_id,
                        commit_sha=self.commit_sha,
                        kind=SymbolKind.FIELD,
                        qualified_name=f"{spec.model_name}.{spec.field_name}",
                        file_path=parsed_file.rel_path,
                        line_start=spec.line_start,
                        line_end=spec.line_end,
                        parent_id=class_symbol.symbol_id,
                        metadata={"model": spec.model_name, "field": spec.field_name},
                    )
                )
                self.fields.setdefault(
                    (parsed_file.rel_path, spec.model_name), {}
                )[spec.field_name] = symbol
        self._collect_field_usages()

    def _collect_field_usages(self) -> None:
        class_names = set(self.class_symbols)
        for parsed_file in self.parsed:
            rel_path = parsed_file.rel_path
            local_types = collect_local_types(rel_path, parsed_file.tree)
            fallback = self._accessible_fields(rel_path)
            file_symbol = self.file_symbols[rel_path]
            for access in collect_attribute_accesses(rel_path, parsed_file.tree):
                models = self._model_fields_for(rel_path, access, local_types, class_names)
                if models is None:
                    bucket = fallback.get(access.field_name, [])
                    if len(bucket) != 1:
                        continue
                    target = bucket[0]
                    confidence = Confidence.INFERRED
                else:
                    target = models.get(access.field_name)
                    if target is None:
                        continue
                    confidence = Confidence.CERTAIN

                source = (
                    self.symbol_by_qual.get((rel_path, access.scope))
                    if access.scope
                    else None
                )
                source_id = source.symbol_id if source is not None else file_symbol.symbol_id
                if source_id == target.symbol_id:
                    continue
                self.edges.add(
                    DependencyEdge(
                        repo_id=self.repo_id,
                        commit_sha=self.commit_sha,
                        source_id=source_id,
                        target_id=target.symbol_id,
                        edge_type=EdgeType.USES_FIELD,
                        analysis_source=AnalysisSource.AST,
                        confidence=confidence,
                        file_path=rel_path,
                        line_start=access.line_start,
                        line_end=access.line_end,
                    )
                )

    def _model_fields_for(
        self,
        rel_path: str,
        access: AttributeAccess,
        local_types: Mapping[str, Mapping[str, str]],
        class_names: set[tuple[str, str]],
    ) -> dict[str, Symbol] | None:
        base = access.base
        if base is None:
            return None
        if base == "self":
            if access.class_name and (rel_path, access.class_name) in class_names:
                return self.fields.get((rel_path, access.class_name), {})
            return None
        if (rel_path, base) in class_names:
            return self.fields.get((rel_path, base), {})
        binding = self.bindings.get((rel_path, base))
        if (
            binding is not None
            and binding.target_name
            and (binding.target_rel, binding.target_name) in class_names
        ):
            return self.fields.get((binding.target_rel, binding.target_name), {})
        class_name = local_types.get(access.scope or "", {}).get(base) if access.scope else None
        if not class_name:
            return None
        if (rel_path, class_name) in class_names:
            return self.fields.get((rel_path, class_name), {})
        binding = self.bindings.get((rel_path, class_name))
        if (
            binding is not None
            and binding.target_name
            and (binding.target_rel, binding.target_name) in class_names
        ):
            return self.fields.get((binding.target_rel, binding.target_name), {})
        return None

    def _accessible_fields(self, rel_path: str) -> dict[str, list[Symbol]]:
        index: dict[str, list[Symbol]] = {}

        def merge(models: Mapping[str, Symbol]) -> None:
            for symbol in models.values():
                field_name = symbol.qualified_name.split(".")[-1]
                bucket = index.setdefault(field_name, [])
                if all(item.symbol_id != symbol.symbol_id for item in bucket):
                    bucket.append(symbol)

        for (model_rel, _model_name), models in self.fields.items():
            if model_rel == rel_path:
                merge(models)
        for (binding_rel, _alias), binding in self.bindings.items():
            if binding_rel != rel_path or binding is None or not binding.target_name:
                continue
            models = self.fields.get((binding.target_rel, binding.target_name))
            if models:
                merge(models)
        return index

    # -- FastAPI 路由 -----------------------------------------------------

    def _collect_routes(self) -> None:
        routers: dict[tuple[str, str], RouterSpec] = {}
        routes: list[RouteSpec] = []
        includes: list[IncludeSpec] = []
        for parsed_file in self.parsed:
            for spec in parse_router_declarations(parsed_file.rel_path, parsed_file.tree):
                routers[(parsed_file.rel_path, spec.var_name)] = spec
            routes.extend(parse_route_decorators(parsed_file.rel_path, parsed_file.tree))
            includes.extend(parse_include_router(parsed_file.rel_path, parsed_file.tree))

        incoming: dict[tuple[str, str], list[tuple[tuple[str, str], str]]] = {}
        for include in includes:
            child = self._router_key(include.rel_path, include.child_ref, routers)
            if child is None:
                self.warnings.add(
                    f"{include.rel_path}:{include.line_start}: include_router 目标 "
                    f"{include.child_ref} 无法解析"
                )
                continue
            parent = self._router_key(
                include.rel_path, include.parent_ref, routers
            ) or (include.rel_path, include.parent_ref)
            incoming.setdefault(child, []).append((parent, include.prefix))

        for spec in routes:
            key = (spec.rel_path, spec.router_var)
            router = routers.get(key)
            own_prefix = router.prefix if router is not None else ""
            chains = _prefix_chains(key, incoming, frozenset())
            if not chains:
                chains = [[]]
            seen_paths: set[str] = set()
            for chain in chains:
                full_path = join_route_path(*chain, own_prefix, spec.path)
                if full_path in seen_paths:
                    continue
                seen_paths.add(full_path)
                self._add_route(spec, full_path)

    def _collect_test_route_dependencies(self) -> None:
        """按测试内字面量 HTTP 调用把测试文件关联到路由所在模块。"""
        routes: dict[tuple[str, str], list[Symbol]] = {}
        for symbol in self.symbols.values():
            if symbol.kind is not SymbolKind.ROUTE:
                continue
            key = (str(symbol.metadata.get("method", "")), str(symbol.metadata.get("path", "")))
            routes.setdefault(key, []).append(symbol)
        if not routes:
            return

        calls_by_source: dict[str, list[str]] = {}
        for edge in self.edges.items():
            if edge.edge_type is EdgeType.CALLS:
                calls_by_source.setdefault(edge.source_id, []).append(edge.target_id)

        for parsed_file in self.parsed:
            if not _is_test_file(parsed_file.rel_path):
                continue
            test_file = self.file_symbols.get(parsed_file.rel_path)
            if test_file is None:
                continue
            for call in parse_http_client_calls(parsed_file.rel_path, parsed_file.tree):
                for (method, path), route_symbols in routes.items():
                    if method != call.method or not route_path_matches(path, call.path):
                        continue
                    for route_symbol in route_symbols:
                        targets = {file_symbol_id(route_symbol.file_path)}
                        handler = self.symbol_by_qual.get(
                            (route_symbol.file_path, str(route_symbol.metadata.get("handler", "")))
                        )
                        if handler is not None:
                            # 路由处理函数的调用目标一并关联，避免测试到处理函数的传播距离过长
                            targets.update(calls_by_source.get(handler.symbol_id, ()))
                        for target_id in sorted(targets):
                            if target_id == test_file.symbol_id:
                                continue
                            self.edges.add(
                                DependencyEdge(
                                    repo_id=self.repo_id,
                                    commit_sha=self.commit_sha,
                                    source_id=test_file.symbol_id,
                                    target_id=target_id,
                                    edge_type=EdgeType.DEPENDS_ON,
                                    analysis_source=AnalysisSource.FRAMEWORK,
                                    confidence=Confidence.INFERRED,
                                    file_path=parsed_file.rel_path,
                                    line_start=call.line_start,
                                    line_end=call.line_end,
                                )
                            )

    def _router_key(
        self,
        rel_path: str,
        reference: str,
        routers: Mapping[tuple[str, str], RouterSpec],
    ) -> tuple[str, str] | None:
        if "." in reference:
            head, _, tail = reference.partition(".")
            binding = self.bindings.get((rel_path, head))
            if (
                binding is not None
                and binding.target_rel is not None
                and (binding.target_rel, tail) in routers
            ):
                return (binding.target_rel, tail)
            return None
        if (rel_path, reference) in routers:
            return (rel_path, reference)
        binding = self.bindings.get((rel_path, reference))
        if binding is None or binding.target_rel is None:
            return None
        name = binding.target_name or reference
        if (binding.target_rel, name) in routers:
            return (binding.target_rel, name)
        return None

    def _add_route(self, spec: RouteSpec, full_path: str) -> None:
        symbol_id = route_symbol_id(spec.method, full_path)
        if symbol_id in self.symbols:
            self.warnings.add(
                f"{spec.rel_path}:{spec.line_start}: 路由 {spec.method} {full_path} "
                "与已有路由重复，保留先出现者"
            )
            return
        route_symbol = self._register(
            Symbol(
                symbol_id=symbol_id,
                repo_id=self.repo_id,
                commit_sha=self.commit_sha,
                kind=SymbolKind.ROUTE,
                qualified_name=f"{spec.method} {full_path}",
                file_path=spec.rel_path,
                line_start=spec.line_start,
                line_end=spec.line_end,
                parent_id=file_symbol_id(spec.rel_path),
                metadata={
                    "method": spec.method,
                    "path": full_path,
                    "handler": spec.handler_qualname,
                },
            )
        )
        handler = self.symbol_by_qual.get((spec.rel_path, spec.handler_qualname))
        if handler is None:
            self.warnings.add(
                f"{spec.rel_path}:{spec.line_start}: 路由处理器 {spec.handler_qualname} 无法解析"
            )
            return
        self.edges.add(
            DependencyEdge(
                repo_id=self.repo_id,
                commit_sha=self.commit_sha,
                source_id=route_symbol.symbol_id,
                target_id=handler.symbol_id,
                edge_type=EdgeType.ROUTES_TO,
                analysis_source=AnalysisSource.FRAMEWORK,
                confidence=Confidence.CERTAIN,
                file_path=spec.rel_path,
                line_start=handler.line_start,
                line_end=handler.line_end,
            )
        )

        # FastAPI 依赖注入图：处理函数参数中的 Depends(x) 形成 handler → x 的依赖边。
        # 精确来源（参数默认值）记 CERTAIN；文件级扫描（Annotated 别名）记 INFERRED。
        exact_depends = list(handler.metadata.get("depends") or ())
        candidate_depends = list(exact_depends)
        for name in self.file_depends.get(spec.rel_path, ()):
            if name not in candidate_depends:
                candidate_depends.append(name)
        for dep_name in candidate_depends:
            dep_symbol = next(
                (
                    symbol
                    for symbol in self.symbols.values()
                    if symbol.qualified_name == dep_name
                    and symbol.kind in {SymbolKind.FUNCTION, SymbolKind.METHOD}
                ),
                None,
            )
            if dep_symbol is None:
                continue
            self.edges.add(
                DependencyEdge(
                    repo_id=self.repo_id,
                    commit_sha=self.commit_sha,
                    source_id=handler.symbol_id,
                    target_id=dep_symbol.symbol_id,
                    edge_type=EdgeType.DEPENDS_ON,
                    analysis_source=AnalysisSource.FRAMEWORK,
                    confidence=Confidence.CERTAIN if dep_name in exact_depends else Confidence.INFERRED,
                    file_path=spec.rel_path,
                    line_start=handler.line_start,
                    line_end=handler.line_end,
                )
            )


def _prefix_chains(
    key: tuple[str, str],
    incoming: Mapping[tuple[str, str], list[tuple[tuple[str, str], str]]],
    visited: frozenset[tuple[str, str]],
) -> list[list[str]]:
    if key in visited:
        return []
    parents = incoming.get(key)
    if not parents:
        return [[]]
    chains: list[list[str]] = []
    for parent, prefix in parents:
        for chain in _prefix_chains(parent, incoming, visited | {key}):
            chains.append([*chain, prefix])
    return chains


def _iter_python_files(root: Path) -> list[str]:
    files: list[str] = []
    for path in sorted(root.rglob("*.py")):
        parts = path.relative_to(root).parts
        if any(part in _EXCLUDED_DIRS for part in parts[:-1]):
            continue
        files.append("/".join(parts))
    return files


def _parse_file(root: Path, rel_path: str) -> tuple[_ParsedFile, str | None]:
    path = root / rel_path
    try:
        with tokenize.open(str(path)) as handle:
            source = handle.read()
    except (UnicodeDecodeError, OSError, ValueError) as exc:
        return _ParsedFile(rel_path, "", 0, ast.Module(body=[], type_ignores=[])), (
            f"{rel_path}: 读取失败: {exc}"
        )
    line_count = max(1, source.count("\n") + (0 if source.endswith("\n") else 1))
    try:
        tree = ast.parse(source, filename=rel_path)
        return _ParsedFile(rel_path, _module_name(rel_path), line_count, tree), None
    except SyntaxError as exc:
        warning = f"{rel_path}: 解析失败: {exc}"
        lenient = _parse_lenient(source, rel_path, line_count)
        if lenient is not None:
            return lenient, warning + "（已降级解析：仅保留模块级导入）"
        return _ParsedFile(rel_path, "", 0, ast.Module(body=[], type_ignores=[])), warning


def _parse_lenient(source: str, rel_path: str, line_count: int) -> _ParsedFile | None:
    """语法不兼容时只保留顶格 `import`/`from` 行，保证跨模块导入边不断链。"""
    lines = [
        line
        for line in source.splitlines()
        if line.startswith("import ") or line.startswith("from ")
    ]
    if not lines:
        return None
    try:
        tree = ast.parse("\n".join(lines) + "\n", filename=rel_path)
    except SyntaxError:
        return None
    return _ParsedFile(rel_path, _module_name(rel_path), line_count, tree)


def _module_name(rel_path: str) -> str:
    parts = rel_path[: -len(".py")].split("/")
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_module(
    module: str,
    level: int,
    rel_path: str,
    modules: Mapping[str, str],
) -> str | None:
    if level <= 0:
        if not module:
            return None
        direct = modules.get(module)
        if direct is not None:
            return direct
        # 源码根可能不在仓库根（如真实仓库的 backend/），尝试唯一后缀匹配。
        suffix = "." + module
        candidates = {
            path
            for name, path in modules.items()
            if name == module or name.endswith(suffix)
        }
        return next(iter(candidates)) if len(candidates) == 1 else None
    directory = rel_path.split("/")[:-1]
    if level - 1 > len(directory):
        return None
    base = directory[: len(directory) - (level - 1)] if level > 1 else directory
    parts = [*base, *([module] if module else [])]
    return modules.get(".".join(parts)) if parts else None


def _is_test_file(rel_path: str) -> bool:
    name = rel_path.rsplit("/", 1)[-1]
    return name.startswith("test_") and name.endswith(".py")


def _depends_call_name(expr: ast.AST) -> str | None:
    """从 `Depends(x)` / `Depends(x.y)` 中提取依赖名。"""
    if not isinstance(expr, ast.Call):
        return None
    func = expr.func
    if not (isinstance(func, ast.Name) and func.id == "Depends"):
        return None
    if not expr.args:
        return None
    arg = expr.args[0]
    if isinstance(arg, ast.Name):
        return arg.id
    if isinstance(arg, ast.Attribute):
        return arg.attr
    return None


def _depends_targets(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """收集函数参数默认值中的 FastAPI 依赖目标（C-DI@1）。"""
    args = node.args
    defaults: list[ast.AST] = []
    positional = [*args.posonlyargs, *args.args]
    offset = len(positional) - len(args.defaults)
    for index, default in enumerate(args.defaults):
        defaults.append(default)
        _ = index + offset
    names: list[str] = []
    for index, default in enumerate(args.defaults):
        name = _depends_call_name(default)
        if name is None:
            continue
        names.append(name)
    for default in args.kw_defaults:
        if default is None:
            continue
        name = _depends_call_name(default)
        if name is not None:
            names.append(name)
    unique: list[str] = []
    for name in names:
        if name not in unique:
            unique.append(name)
    return unique


def _test_markers(node: ast.AST) -> list[str]:
    markers: list[str] = []
    for decorator in getattr(node, "decorator_list", ()):
        expr = decorator.func if isinstance(decorator, ast.Call) else decorator
        if not isinstance(expr, ast.Attribute):
            continue
        value = expr.value
        is_mark = (isinstance(value, ast.Attribute) and value.attr == "mark") or (
            isinstance(value, ast.Name) and value.id == "mark"
        )
        if is_mark and expr.attr not in markers:
            markers.append(expr.attr)
    return markers


def _node_span(node: ast.AST) -> tuple[int, int]:
    starts = [node.lineno]
    starts.extend(decorator.lineno for decorator in getattr(node, "decorator_list", ()))
    return min(starts), getattr(node, "end_lineno", None) or node.lineno