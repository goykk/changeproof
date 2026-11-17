"""Deterministic stage orchestration for analysis tasks (C-ORCH@1)."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, replace
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping, Sequence

from core.coverage.mapper import build_coverage_records, parse_coverage_json
from core.coverage.selector import fallback_full_selection, select_tests
from core.generation.generator import (
    PENDING_REVIEW,
    REJECTED,
    GeneratedTest,
    build_generation_request,
    generate_test_suggestions,
)
from core.generation.model_adapter import ModelAdapter, default_model_adapter
from core.impact.mapper import load_sources, map_changes
from core.impact.propagation import compute_impact
from core.indexer.indexer import IndexResult, index_repository
from core.repo.git_repo import diff_commits, export_snapshot
from core.schema.state import InvalidTransitionError, TaskStatus, assert_transition
from core.schema.types import (
    ChangedSymbol,
    CoverageRecord,
    DependencyEdge,
    ImpactResult,
    RunResult,
    RunStatus,
    Symbol,
    TestSelectionItem,
)
from core.validation.validator import ValidationOutcome, validate_generated_tests
from services.runner.executor import RunnerJob, RunnerUnavailableError
from services.runner.pytest_support import build_pytest_command, parse_pytest_summary
from services.worker.reporting import build_report_payload, to_jsonable, utc_now, write_json, write_report

DEFAULT_PYTEST_COMMAND = ["python", "-m", "pytest", "-q"]
RUN_TIMEOUT_S = 600
MAX_PER_TEST_COVERAGE_RUNS = 40


@dataclass
class AnalyzeOptions:
    repo_local_path: str
    base_sha: str
    target_sha: str
    pytest_command: list[str] | None = None
    max_depth: int = 3
    max_results: int = 200
    generate_tests: bool = False
    high_risk_globs: tuple[str, ...] = ("app/core/**",)
    artifacts_root: str = "var/artifacts"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _symbol_payload(symbol: Symbol | None, symbol_id: str) -> dict[str, Any]:
    if symbol is None:
        return {
            "symbol_id": symbol_id,
            "kind": "file",
            "qualified_name": symbol_id,
            "file_path": "",
            "line_start": 0,
            "line_end": 0,
        }
    return {
        "symbol_id": symbol.symbol_id,
        "kind": symbol.kind.value,
        "qualified_name": symbol.qualified_name,
        "file_path": symbol.file_path,
        "line_start": symbol.line_start,
        "line_end": symbol.line_end,
    }


def _changed_payload(item: ChangedSymbol) -> dict[str, Any]:
    return {
        "symbol_id": item.symbol_id,
        "kind": item.kind.value,
        "qualified_name": item.qualified_name,
        "file_path": item.file_path,
        "change_type": item.change_type.value,
        "diff_ranges": [[start, end] for start, end in item.diff_ranges],
        "confidence": item.confidence.value,
    }


def _impact_payload(item: ImpactResult, symbols_by_id: Mapping[str, Symbol]) -> dict[str, Any]:
    symbol = symbols_by_id.get(item.symbol_id)
    payload = _symbol_payload(symbol, item.symbol_id)
    payload.pop("line_start", None)
    payload.pop("line_end", None)
    payload.update(
        {
            "path": list(item.path),
            "distance": item.distance,
            "rank": item.rank,
            "confidence": item.confidence.value,
            "analysis_source": item.analysis_source.value,
            "is_direct": item.is_direct,
        }
    )
    return payload


def _selection_payload(items: Sequence[TestSelectionItem]) -> list[dict[str, Any]]:
    return [
        {
            "test_node_id": item.test_node_id,
            "strategy": item.strategy,
            "reasons": [
                {"source": reason.source, "detail": reason.detail, "ref_id": reason.ref_id}
                for reason in item.reasons
            ],
        }
        for item in items
    ]


def _merge_edges(base_edges: Sequence[DependencyEdge], target_edges: Sequence[DependencyEdge]) -> list[DependencyEdge]:
    merged: dict[tuple[str, str, str], DependencyEdge] = {}
    for edge in (*base_edges, *target_edges):
        merged[(edge.source_id, edge.target_id, edge.edge_type.value)] = edge
    return list(merged.values())


def _pytest_command(options: AnalyzeOptions, node_ids: Sequence[str], coverage_path: Path | None) -> list[str]:
    if options.pytest_command:
        command = list(options.pytest_command)
        if coverage_path is not None and not any(arg.startswith("--cov") for arg in command):
            command += ["--cov=.", "--cov-context=test", f"--cov-report=json:{coverage_path}"]
        return command + list(node_ids)
    return build_pytest_command(node_ids, str(coverage_path) if coverage_path is not None else None)


class _Run:
    """Tracks the current status/stage of one task and emits state-machine events."""

    def __init__(self, store, task_id: str, changeset: Any, options: AnalyzeOptions) -> None:
        self.store = store
        self.task_id = task_id
        self.changeset = changeset
        self.options = options
        self.status = TaskStatus.CREATED.value
        self.stage = TaskStatus.CREATED.value
        self.record = None
        self.warnings: list[str] = []

    @property
    def root(self) -> Path:
        # 绝对路径：执行器以 snapshot 为 cwd 运行，相对路径会导致覆盖率/日志落到 snapshot 内。
        return Path(self.options.artifacts_root).resolve() / self.task_id

    def stage_dir(self, stage: str) -> Path:
        path = self.root / stage
        path.mkdir(parents=True, exist_ok=True)
        return path

    def enter(self, target: TaskStatus, message: str) -> None:
        previous = self.status
        assert_transition(previous, target.value)
        self.status = target.value
        self.stage = target.value
        self.record = self.store.tasks.update_task(
            self.task_id,
            status=self.status,
            stage=self.stage,
            resume_status=None,
            error_code=None,
            error_message=None,
        )
        self.store.tasks.append_event(self.task_id, previous, self.status, self.stage, message)

    def fail(self, exc: Exception) -> None:
        code = str(getattr(exc, "code", None) or f"{self.stage}_failed")
        message = f"{type(exc).__name__}: {exc}"
        previous: str | None = self.status
        try:
            assert_transition(previous, TaskStatus.FAILED.value)
        except InvalidTransitionError:
            previous = None
        if previous is None:
            return
        self.status = TaskStatus.FAILED.value
        self.record = self.store.tasks.update_task(
            self.task_id,
            status=self.status,
            stage=self.stage,
            resume_status=previous,
            error_code=code,
            error_message=message,
        )
        self.store.tasks.append_event(self.task_id, previous, self.status, self.stage, message)


def _normalize_coverage_contexts(
    parsed: Mapping[str, Mapping[str, Sequence[int]]],
) -> dict[str, dict[str, list[int]]]:
    """pytest-cov 的 context 名为 ``<nodeid>|run``，归一化为 pytest node id。"""
    normalized: dict[str, dict[str, list[int]]] = {}
    for file_path, contexts in parsed.items():
        merged: dict[str, set[int]] = {}
        for context, lines in contexts.items():
            name = context.rsplit("|", 1)[0] if "|" in context else context
            if not name:
                continue
            merged.setdefault(name, set()).update(int(line) for line in lines)
        normalized[file_path] = {name: sorted(lines) for name, lines in merged.items()}
    return normalized


def _prepare_baseline(store, executor, run: _Run) -> dict[str, Any]:
    stage = run.stage_dir(TaskStatus.BASELINE_PREPARING.value)
    snapshot = stage / "snapshot"
    if snapshot.exists():
        shutil.rmtree(snapshot)
    export_snapshot(run.options.repo_local_path, run.options.base_sha, str(snapshot))

    coverage_path = stage / "coverage.json"
    if coverage_path.exists():
        coverage_path.unlink()
    command = _pytest_command(run.options, [], coverage_path)
    result = executor.run(
        RunnerJob(
            job_id="baseline",
            snapshot_dir=str(snapshot),
            command=command,
            timeout_s=RUN_TIMEOUT_S,
            artifact_dir=str(stage),
        )
    )

    coverage_missing = not coverage_path.exists()
    parsed: dict[str, dict[str, list[int]]] | None = None
    if not coverage_missing:
        try:
            parsed = parse_coverage_json(str(coverage_path))
        except FileNotFoundError:
            coverage_missing = True
    if parsed and not any(context for file_map in parsed.values() for context in file_map if context):
        # pytest-cov 的 JSON 报告默认不输出 contexts；用 coverage json --show-contexts 重新导出，
        # 否则覆盖率无法归属到具体测试，选测只能退化到静态与保底来源。
        contexts_json = stage / "coverage_contexts.json"
        if contexts_json.exists():
            contexts_json.unlink()
        executor.run(
            RunnerJob(
                job_id="coverage-contexts",
                snapshot_dir=str(snapshot),
                command=["python", "-m", "coverage", "json", "--show-contexts", "-o", str(contexts_json)],
                timeout_s=120,
                artifact_dir=str(stage),
            )
        )
        if contexts_json.exists():
            try:
                parsed = parse_coverage_json(str(contexts_json))
            except (FileNotFoundError, ValueError):
                pass
    if parsed:
        parsed = _normalize_coverage_contexts(parsed)
    records: list[CoverageRecord] = build_coverage_records(parsed, run.options.base_sha) if parsed else []
    if records:
        store.coverage.replace_coverage(run.options.base_sha, records)
    if coverage_missing:
        run.warnings.append("coverage_missing")
    result = replace(result, coverage_path=str(coverage_path) if not coverage_missing else None)
    write_json(stage / "baseline_result.json", result)
    return {
        "snapshot": str(snapshot),
        "run": result,
        "parsed": parsed,
        "records": records,
        "coverage_missing": coverage_missing,
    }


def _index_versions(store, run: _Run, base_snapshot: str) -> dict[str, Any]:
    stage = run.stage_dir(TaskStatus.INDEXING.value)
    target_snapshot = stage / "snapshot_target"
    if target_snapshot.exists():
        shutil.rmtree(target_snapshot)
    export_snapshot(run.options.repo_local_path, run.options.target_sha, str(target_snapshot))

    base_index: IndexResult = index_repository(base_snapshot, run.changeset.repo_id, run.options.base_sha)
    target_index: IndexResult = index_repository(str(target_snapshot), run.changeset.repo_id, run.options.target_sha)

    store.symbols.upsert_symbols([*base_index.symbols, *target_index.symbols])
    store.edges.upsert_edges([*base_index.edges, *target_index.edges])
    store.tests.upsert_tests([*base_index.tests, *target_index.tests])

    write_json(stage / "index_base.json", base_index)
    write_json(stage / "index_target.json", target_index)
    return {
        "base": base_index,
        "target": target_index,
        "target_snapshot": str(target_snapshot),
        "warnings": [*base_index.warnings, *target_index.warnings],
    }


def _analyze(store, run: _Run, index: Mapping[str, Any], baseline: Mapping[str, Any]) -> dict[str, Any]:
    stage = run.stage_dir(TaskStatus.ANALYZING.value)
    changeset_id = run.changeset.changeset_id
    base_index: IndexResult = index["base"]
    target_index: IndexResult = index["target"]

    diffs = diff_commits(run.options.repo_local_path, run.options.base_sha, run.options.target_sha)
    diff_paths = [diff.file_path for diff in diffs]
    changed = map_changes(
        diffs,
        base_index.symbols,
        target_index.symbols,
        changeset_id,
        base_sources=load_sources(str(baseline["snapshot"]), diff_paths),
        target_sources=load_sources(str(index["target_snapshot"]), diff_paths),
    )

    symbols_by_id: dict[str, Symbol] = {}
    for symbol in (*base_index.symbols, *target_index.symbols):
        symbols_by_id[symbol.symbol_id] = symbol

    output = compute_impact(
        changed,
        _merge_edges(base_index.edges, target_index.edges),
        symbols_by_id,
        changeset_id,
        run.options.max_depth,
        run.options.max_results,
    )

    base_by_id = {symbol.symbol_id: symbol for symbol in base_index.symbols}
    uncovered_ids: list[str] = []
    for item in changed:
        symbol = base_by_id.get(item.symbol_id) or symbols_by_id.get(item.symbol_id)
        if symbol is None or not store.coverage.tests_covering_symbol(run.options.base_sha, symbol):
            uncovered_ids.append(item.symbol_id)

    changed_payloads = [_changed_payload(item) for item in changed]
    impact_payloads = [_impact_payload(item, symbols_by_id) for item in output.items]

    store.changed_symbols.replace_changed_symbols(changeset_id, changed)
    store.impacts.replace_impacts(changeset_id, output.items)

    write_json(
        stage / "impact.json",
        {
            "changed_symbols": changed_payloads,
            "impacts": impact_payloads,
            "truncated": output.truncated,
            "max_depth": output.max_depth,
            "max_results": output.max_results,
        },
    )
    uncovered_payloads = [
        _symbol_payload(symbols_by_id.get(symbol_id) or base_by_id.get(symbol_id), symbol_id)
        for symbol_id in uncovered_ids
    ]
    write_json(stage / "uncovered_symbols.json", uncovered_payloads)
    return {
        "changed": changed,
        "impacts": output.items,
        "truncated": output.truncated,
        "changed_payloads": changed_payloads,
        "impact_payloads": impact_payloads,
        "uncovered_ids": uncovered_ids,
        "symbols_by_id": symbols_by_id,
    }


def _read_executed_lines(path: Path) -> dict[str, list[int]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, list[int]] = {}
    for raw_path, entry in (payload.get("files") or {}).items():
        lines = [int(line) for line in (entry.get("executed_lines") or [])]
        if not lines:
            continue
        normalized = str(raw_path).replace("\\", "/")
        if normalized.startswith("./"):
            normalized = normalized[2:]
        out[normalized] = lines
    return out


def _per_test_coverage(
    store,
    run: _Run,
    executor,
    snapshot: str,
    tests: Sequence[Any],
    stage: Path,
) -> list[CoverageRecord]:
    """pytest-cov 未写入测试级上下文时，逐测试运行以建立 test→代码行映射。"""
    work = stage / "per_test_coverage"
    work.mkdir(parents=True, exist_ok=True)
    records: list[CoverageRecord] = []
    targets = list(tests)[:MAX_PER_TEST_COVERAGE_RUNS]
    if len(tests) > MAX_PER_TEST_COVERAGE_RUNS:
        run.warnings.append("coverage_per_test_capped")
    for position, test in enumerate(targets):
        json_path = (work / f"cov_{position}.json").resolve()
        if json_path.exists():
            json_path.unlink()
        try:
            executor.run(
                RunnerJob(
                    job_id=f"coverage-{position}",
                    snapshot_dir=snapshot,
                    command=build_pytest_command([test.node_id], str(json_path)),
                    timeout_s=120,
                    artifact_dir=str(work),
                )
            )
        except RunnerUnavailableError:
            raise
        if not json_path.exists():
            continue
        for file_path, lines in _read_executed_lines(json_path).items():
            records.append(
                CoverageRecord(
                    commit_sha=run.options.base_sha,
                    test_node_id=test.node_id,
                    file_path=file_path,
                    lines=sorted(set(lines)),
                )
            )
    if records:
        store.coverage.replace_coverage(run.options.base_sha, records)
    return records


def _selecting(
    store,
    run: _Run,
    executor,
    index: Mapping[str, Any],
    analysis: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    stage = run.stage_dir(TaskStatus.TEST_SELECTING.value)
    symbols: list[Symbol] = []
    seen_symbols: set[str] = set()
    for symbol in (*index["base"].symbols, *index["target"].symbols):
        if symbol.symbol_id in seen_symbols:
            continue
        seen_symbols.add(symbol.symbol_id)
        symbols.append(symbol)

    tests = []
    seen_tests: set[str] = set()
    for test in (*index["base"].tests, *index["target"].tests):
        if test.node_id in seen_tests:
            continue
        seen_tests.add(test.node_id)
        tests.append(test)

    coverage_records = baseline["records"]
    if tests and not any(record.test_node_id for record in coverage_records):
        coverage_records = _per_test_coverage(
            store, run, executor, baseline["snapshot"], tests, stage
        )
        baseline["records"] = coverage_records

    items = select_tests(
        analysis["changed"],
        analysis["impacts"],
        symbols,
        tests,
        coverage_records,
        run.options.high_risk_globs,
        strategy="combo",
        edges=index["target"].edges,
    )
    if not items and tests and analysis["changed"]:
        # C-SELECT@8：存在变更符号且仓库有测试时，运行时不得返回空选择集（评测基线保持原样）。
        items = fallback_full_selection(run.changeset.changeset_id, tests)
    store.selections.replace_selections(run.changeset.changeset_id, items)
    payload = _selection_payload(items)
    write_json(stage / "selections.json", payload)
    return {"items": items, "payload": payload}


def _execute(executor, job_id: str, snapshot_dir: str, command: Sequence[str], artifact_dir: Path) -> RunResult:
    return executor.run(
        RunnerJob(
            job_id=job_id,
            snapshot_dir=snapshot_dir,
            command=list(command),
            timeout_s=RUN_TIMEOUT_S,
            artifact_dir=str(artifact_dir),
        )
    )



def _empty_run(executor) -> RunResult:
    return RunResult(
        exit_code=0,
        status=RunStatus.PASSED,
        duration_ms=0,
        log_path="",
        isolation_mode=executor.mode,
        selected_count=0,
        passed_count=0,
        failed_count=0,
        error_count=0,
        skipped_count=0,
        timed_out=False,
        coverage_path=None,
    )


def _run_selected_tests(
    store,
    executor,
    run: _Run,
    index: Mapping[str, Any],
    selections: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> dict[str, RunResult]:
    stage = run.stage_dir(TaskStatus.TEST_RUNNING.value)
    node_ids = [item.test_node_id for item in selections["items"]]
    if not node_ids:
        empty = _empty_run(executor)
        store.runs.upsert_run(run.changeset.changeset_id, "target", empty)
        store.runs.upsert_run(run.changeset.changeset_id, "base", empty)
        return {"target": empty, "base": empty}

    command = _pytest_command(run.options, node_ids, None)
    # 计数由执行器统一负责（junitxml），编排层不再自行解析日志或覆盖率。
    target_result = _execute(executor, "run_target", index["target_snapshot"], command, stage)
    base_result = _execute(executor, "run_base", baseline["snapshot"], command, stage)
    store.runs.upsert_run(run.changeset.changeset_id, "target", target_result)
    store.runs.upsert_run(run.changeset.changeset_id, "base", base_result)
    return {"target": target_result, "base": base_result}


def _style_samples(snapshot: Path, limit: int = 2) -> list[str]:
    tests_dir = snapshot / "tests"
    if not tests_dir.is_dir():
        return []
    samples: list[str] = []
    for path in sorted(tests_dir.glob("*.py"))[:limit]:
        try:
            samples.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return samples


def _suggestion_entry(item: GeneratedTest, outcome: ValidationOutcome | None) -> dict[str, Any]:
    return {
        "test_node_id": item.test_node_id,
        "status": item.status,
        "rationale": item.rationale,
        "analysis_source": item.analysis_source,
        "validation_evidence": outcome.evidence if outcome is not None else {},
    }


def _validate_suggestions(
    executor,
    root: Path,
    suggestions: Sequence[GeneratedTest],
) -> tuple[list[GeneratedTest], dict[str, ValidationOutcome]]:
    base_snapshot = root / TaskStatus.BASELINE_PREPARING.value / "snapshot"
    target_snapshot = root / TaskStatus.INDEXING.value / "snapshot_target"
    candidates = [item for item in suggestions if item.status == PENDING_REVIEW]
    if not candidates or not base_snapshot.is_dir() or not target_snapshot.is_dir():
        return list(suggestions), {}

    validating_dir = root / TaskStatus.VALIDATING.value
    validating_dir.mkdir(parents=True, exist_ok=True)

    def job_factory(*, snapshot_dir: str, node_id: str, job_id: str) -> RunResult:
        return executor.run(
            RunnerJob(
                job_id=job_id,
                snapshot_dir=snapshot_dir,
                command=[*DEFAULT_PYTEST_COMMAND, node_id],
                timeout_s=RUN_TIMEOUT_S,
                artifact_dir=str(validating_dir),
            )
        )

    outcomes = validate_generated_tests(
        candidates,
        str(base_snapshot),
        str(target_snapshot),
        job_factory,
        repeats=2,
    )
    by_id = {outcome.test_node_id: outcome for outcome in outcomes}
    validated = [
        replace(item, status=by_id[item.test_node_id].status) if item.test_node_id in by_id else item
        for item in suggestions
    ]
    return validated, by_id


def _changed_sources(snapshot: Path, changed: Sequence[ChangedSymbol]) -> dict[str, str]:
    """读取变更符号所在文件的快照源码，供生成模板提取 base 事实。"""
    sources: dict[str, str] = {}
    for item in changed:
        rel_path = str(item.file_path).replace("\\", "/")
        if rel_path in sources:
            continue
        try:
            sources[rel_path] = (snapshot / rel_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return sources


def run_generation_stage(
    store,
    executor,
    task_id: str,
    options: AnalyzeOptions,
    model_adapter: ModelAdapter,
) -> dict[str, Any]:
    task = store.tasks.get_task(task_id)
    if task is None:
        raise ValueError(f"unknown task: {task_id}")
    changeset = store.changesets.get_changeset(task.changeset_id)
    if changeset is None:
        raise ValueError(f"unknown changeset: {task.changeset_id}")

    root = Path(options.artifacts_root) / task_id
    uncovered = _read_json(root / TaskStatus.ANALYZING.value / "uncovered_symbols.json")
    uncovered_ids = {
        str(item.get("symbol_id")) for item in (uncovered if isinstance(uncovered, list) else [])
    }
    changed = [
        item
        for item in store.changed_symbols.list_changed_symbols(changeset.changeset_id)
        if item.symbol_id in uncovered_ids
    ]
    base_symbols = store.symbols.list_symbols(changeset.repo_id, options.base_sha)
    existing_tests = store.tests.list_tests(changeset.repo_id, options.base_sha)
    style_samples = _style_samples(root / TaskStatus.BASELINE_PREPARING.value / "snapshot")
    request = build_generation_request(
        changed,
        base_symbols,
        existing_tests,
        style_samples,
        base_sources=_changed_sources(root / TaskStatus.BASELINE_PREPARING.value / "snapshot", changed),
        target_sources=_changed_sources(root / TaskStatus.INDEXING.value / "snapshot_target", changed),
    )

    generating_dir = root / TaskStatus.TEST_GENERATING.value
    generating_dir.mkdir(parents=True, exist_ok=True)
    model_name = getattr(model_adapter, "name", "unknown")
    try:
        suggestions = generate_test_suggestions(request, model_adapter)
    except Exception as exc:  # 模型不可用 → 降级，绝不让任务失败
        payload = {
            "task_id": task_id,
            "model": model_name,
            "suggestions": [],
            "generated": [],
            "degraded_steps": ["generation"],
            "detail": f"{type(exc).__name__}: {exc}",
        }
        write_json(generating_dir / "generation.json", payload)
        return payload

    validated, outcomes = _validate_suggestions(executor, root, suggestions)
    degraded_steps = ["generation"] if validated and all(item.status == REJECTED for item in validated) else []
    entries = [_suggestion_entry(item, outcomes.get(item.test_node_id)) for item in validated]
    payload = {
        "task_id": task_id,
        "model": model_name,
        "suggestions": entries,
        "generated": entries,
        "degraded_steps": degraded_steps,
    }
    write_json(generating_dir / "generation.json", payload)
    if outcomes:
        write_json(root / TaskStatus.VALIDATING.value / "validation.json", payload)
    return payload


def analyze_changeset(
    store,
    executor,
    changeset_id: str,
    options: AnalyzeOptions,
    task_id: str | None = None,
):
    changeset = store.changesets.get_changeset(changeset_id)
    if changeset is None:
        raise ValueError(f"unknown changeset: {changeset_id}")

    if task_id is not None:
        task = store.tasks.get_task(task_id)
        if task is None:
            raise ValueError(f"unknown task: {task_id}")
    else:
        active = store.tasks.active_task_for_changeset(changeset_id)
        # 调用方已创建任务（status=created）时必须继续推进它，否则任务会永远停在 created。
        if active is not None and active.status != TaskStatus.CREATED.value:
            return active
        task = active if active is not None else store.tasks.create_task(changeset_id)

    run = _Run(store, task.task_id, changeset, options)
    try:
        run.enter(TaskStatus.BASELINE_PREPARING, "export base snapshot and run baseline pytest with coverage")
        baseline = _prepare_baseline(store, executor, run)

        run.enter(TaskStatus.INDEXING, "index base and target snapshots")
        index = _index_versions(store, run, baseline["snapshot"])

        run.enter(TaskStatus.ANALYZING, "map diff to changed symbols and propagate impact")
        analysis = _analyze(store, run, index, baseline)

        run.enter(TaskStatus.TEST_SELECTING, "select pytest nodes from coverage, static dependency and guard sources")
        selections = _selecting(store, run, executor, index, analysis, baseline)

        run.enter(TaskStatus.TEST_RUNNING, "run selected tests on target then base snapshot")
        runs = _run_selected_tests(store, executor, run, index, selections, baseline)

        model_suggestions: list[dict[str, Any]] = []
        degraded_steps: list[str] = []
        if options.generate_tests and analysis["uncovered_ids"]:
            run.enter(TaskStatus.TEST_GENERATING, "generate restricted pytest suggestions for uncovered changes")
            generation = run_generation_stage(store, executor, run.task_id, options, default_model_adapter())
            degraded_steps = list(generation.get("degraded_steps") or [])
            model_suggestions = [dict(item) for item in generation.get("suggestions") or []]
            if any(item.get("validation_evidence") for item in model_suggestions):
                run.enter(TaskStatus.VALIDATING, "double-version validation of generated suggestions")

        payload = build_report_payload(
            task_id=run.task_id,
            changeset_id=changeset_id,
            repo_id=changeset.repo_id,
            base_sha=options.base_sha,
            target_sha=options.target_sha,
            generated_at=utc_now(),
            changed_symbols=analysis["changed_payloads"],
            impacts=analysis["impact_payloads"],
            truncated=analysis["truncated"],
            selections=selections["payload"],
            base_run=runs["base"],
            target_run=runs["target"],
            coverage_missing=baseline["coverage_missing"],
            model_suggestions=model_suggestions,
            degraded_steps=degraded_steps,
            isolation_mode=executor.mode,
            uncovered_symbol_ids=analysis["uncovered_ids"],
            warnings=[*run.warnings, *index["warnings"]],
            baseline_run=baseline["run"],
        )
        run.enter(TaskStatus.REPORTED, "aggregate evidence report")
        report_path = write_report(store, run.task_id, changeset_id, payload, run.stage_dir(TaskStatus.REPORTED.value))
        run.record = store.tasks.update_task(run.task_id, report_path=report_path)
    except Exception as exc:
        run.fail(exc)
        raise

    return run.record if run.record is not None else store.tasks.get_task(run.task_id)