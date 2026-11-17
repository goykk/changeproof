"""批量评测入口（C-EVAL-V3@1，兼容 C-EVAL@1 / C-EVAL-V2@1）。

一次遍历全部标注样本，为每个样本构建一次分析 artifacts（索引、diff 映射、影响传播、
基线覆盖率），并缓存到 `var/eval/cache/<sample_id>.json`；随后五个策略复用同一份
artifacts 计算指标，输出 `var/eval/<strategy>.json` 与 `var/eval/summary.json`。

基线 `coverage.json` 缺少测试级上下文时，按测试逐个采集覆盖行作为兜底（可用
`--no-coverage-per-test` 关闭），结果按 `(base_sha, cache_version)` 缓存在
`var/eval/coverage_cache/<base_sha>.json`；回归样本还会走 C-GEN@1 的生成与双版本验证，
结果写入 artifacts 的 `generated`（可用 `--no-generation` 关闭）。

样本 sha 只从 `eval/samples/lock.json` 读取（兼容 C-SAMPLE@1 扁平结构与
C-SAMPLE-V2@1 的 `{repo: {sample_id: {...}}}` 结构）；缓存命中要求 sha 一致。
单个样本失败只记入 `per_sample[i].error` 并按未命中计，不中断整轮评测。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# C-SAMPLE@1：样例仓库默认测试命令（解释器用当前 Python，避免依赖 PATH 上的 python）
DEFAULT_PYTEST_ARGS: tuple[str, ...] = ("-m", "pytest", "-q", "tests")
# C-GEN@1：生成测试在单个 node_id 上的执行命令
DEFAULT_PYTEST_COMMAND: tuple[str, ...] = ("python", "-m", "pytest", "-p", "no:cacheprovider", "-q")
COVERAGE_TIMEOUT_S = 300
COVERAGE_PER_TEST_TIMEOUT_S = 120
GENERATION_TIMEOUT_S = 300
MAX_PER_TEST_COVERAGE_RUNS = 60
DEFAULT_HIGH_RISK_GLOBS: tuple[str, ...] = ("app/core/**",)
DEFAULT_STRATEGIES: tuple[str, ...] = ("full", "neighbor", "static", "coverage", "combo")
DEFAULT_COVERAGE_CACHE_VERSION = "v1"

# 缓存载荷结构变化时自增，旧缓存自动失效
CACHE_VERSION = 4


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_samples(samples_dir: Path) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for path in sorted(samples_dir.glob("*.json")):
        if path.name == "lock.json":
            continue
        payload = _load_json(path)
        if isinstance(payload, dict) and payload.get("sample_id"):
            samples.append(payload)
    return samples


def _repo_marker(sample: dict[str, Any]) -> str:
    marker = sample.get("repo_path_marker")
    if marker:
        return str(marker)
    return f"var/samples/{sample.get('repo_name', '')}"


def _repo_path(project_root: Path, marker: str) -> Path:
    path = Path(marker).expanduser()
    return path if path.is_absolute() else project_root / path


def _parse_strategies(raw: str) -> list[str]:
    selected: list[str] = []
    for part in str(raw).split(","):
        name = part.strip()
        if not name:
            continue
        if name not in DEFAULT_STRATEGIES:
            raise ValueError(f"unknown strategy: {name}")
        if name not in selected:
            selected.append(name)
    if not selected:
        raise ValueError("no strategy selected")
    return selected


def _lookup_shas(lock: Any, repo_name: str, sample_id: str) -> tuple[str | None, str | None]:
    if not isinstance(lock, dict):
        return None, None
    entry = lock.get(repo_name)
    if not isinstance(entry, dict):
        return None, None
    # C-SAMPLE@1 扁平结构：{repo: {base_sha, target_sha}}
    if "base_sha" in entry:
        return entry.get("base_sha"), entry.get("target_sha")
    # C-SAMPLE-V2@1 多样本结构：{repo: {sample_id: {base_sha, target_sha}}}
    per_sample = entry.get(sample_id)
    if isinstance(per_sample, dict):
        return per_sample.get("base_sha"), per_sample.get("target_sha")
    return None, None


def _run_baseline_coverage(snapshot_dir: str, work_dir: Path) -> tuple[dict, bool, str]:
    """在基线快照上跑 pytest 采集 `--cov-context=test` 覆盖率；失败返回空映射并标记缺失。"""
    coverage_path = work_dir / "coverage_base.json"
    log_path = work_dir / "baseline.log"
    command = [
        sys.executable,
        *DEFAULT_PYTEST_ARGS,
        "--cov=.",
        "--cov-context=test",
        f"--cov-report=json:{coverage_path}",
    ]
    env = {**os.environ, "PYTHONPATH": snapshot_dir}
    try:
        completed = subprocess.run(
            command,
            cwd=snapshot_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=COVERAGE_TIMEOUT_S,
        )
        log_text = f"{completed.stdout}\n{completed.stderr}"
    except (OSError, subprocess.TimeoutExpired) as exc:
        log_text = f"coverage run failed: {exc}"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(log_text, encoding="utf-8", newline="\n")

    if not coverage_path.is_file():
        return {}, True, str(log_path)

    from core.coverage import parse_coverage_json

    try:
        return parse_coverage_json(str(coverage_path)), False, str(log_path)
    except (OSError, ValueError):
        return {}, True, str(log_path)


def _has_test_contexts(parsed: dict[str, dict[str, list[int]]]) -> bool:
    """`--cov-context=test` 是否真正写入了测试级上下文（空 test_node_id 视为缺失）。"""
    return any(test_node_id for per_test in parsed.values() for test_node_id in per_test)


def _coverage_record_path(file_path: str, root: str) -> str:
    """把覆盖率文件路径统一成相对快照根的 posix 形态；无法相对化时保留原值。"""
    normalized = str(file_path).replace("\\", "/")
    raw = Path(normalized)
    if raw.is_absolute():
        try:
            return raw.resolve().relative_to(Path(root).resolve()).as_posix()
        except (OSError, ValueError):
            return normalized.lstrip("/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.strip("/")


def _relativize_records(records: list[Any], root: str) -> list[Any]:
    return [replace(record, file_path=_coverage_record_path(record.file_path, root)) for record in records]


def _load_coverage_cache(
    path: Path,
    base_sha: str,
    cache_version: str,
) -> tuple[list[Any], str] | None:
    if not path.is_file():
        return None
    try:
        payload = _load_json(path)
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("base_sha") != base_sha or payload.get("cache_version") != cache_version:
        return None
    source = payload.get("source")
    if source not in {"per_test", "contexts"}:
        return None
    raw_records = payload.get("records")
    if not isinstance(raw_records, list):
        return None

    from core.schema.types import CoverageRecord

    records: list[CoverageRecord] = []
    try:
        for raw in raw_records:
            if not isinstance(raw, dict):
                return None
            records.append(
                CoverageRecord(
                    commit_sha=str(raw.get("commit_sha") or base_sha),
                    test_node_id=str(raw["test_node_id"]),
                    file_path=str(raw["file_path"]),
                    lines=[int(line) for line in raw.get("lines", [])],
                )
            )
    except (KeyError, TypeError, ValueError):
        return None
    return records, str(source)


def _write_coverage_cache(
    path: Path,
    base_sha: str,
    cache_version: str,
    source: str,
    records: list[Any],
) -> None:
    _write_json(
        path,
        {
            "cache_version": cache_version,
            "base_sha": base_sha,
            "source": source,
            "records": [asdict(record) for record in records],
        },
    )


def _read_executed_lines(path: Path) -> dict[str, list[int]]:
    payload = _load_json(path)
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, dict):
        return {}
    executed: dict[str, list[int]] = {}
    for raw_path, entry in files.items():
        if not isinstance(entry, dict):
            continue
        lines = sorted({int(line) for line in entry.get("executed_lines") or []})
        if lines:
            executed[str(raw_path)] = lines
    return executed


def _per_test_coverage(
    base_dir: str,
    tests: list[Any],
    work_dir: Path,
) -> tuple[dict[str, dict[str, list[int]]], bool]:
    """逐测试运行 pytest 采集执行行；返回与 `parse_coverage_json` 同构的映射与上限标记。"""
    parsed: dict[str, dict[str, list[int]]] = {}
    capped = len(tests) > MAX_PER_TEST_COVERAGE_RUNS
    targets = list(tests)[:MAX_PER_TEST_COVERAGE_RUNS]
    work = work_dir / "coverage_per_test"
    work.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONPATH": base_dir}
    for position, test in enumerate(targets):
        json_path = (work / f"cov_{position}.json").resolve()
        if json_path.exists():
            json_path.unlink()
        command = [
            *DEFAULT_PYTEST_COMMAND,
            str(test.node_id),
            "--cov=.",
            f"--cov-report=json:{json_path}",
        ]
        try:
            subprocess.run(
                command,
                cwd=base_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=COVERAGE_PER_TEST_TIMEOUT_S,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if not json_path.is_file():
            continue
        for file_path, lines in _read_executed_lines(json_path).items():
            parsed.setdefault(file_path, {})[str(test.node_id)] = lines
    return parsed, capped


def _collect_coverage(
    base_dir: str,
    tests: list[Any],
    work_dir: Path,
    base_sha: str,
    coverage_cache_dir: Path,
    coverage_cache_version: str,
    per_test: bool,
    memo: dict[str, tuple[list[Any], str]],
) -> tuple[list[Any], bool, str, list[str]]:
    """返回 `(records, coverage_missing, context_source, warnings)`，同一 base_sha 只采集一次。"""
    from core.coverage import build_coverage_records

    if base_sha in memo:
        records, source = memo[base_sha]
        return list(records), not records, source, []

    cache_path = coverage_cache_dir / f"{base_sha}.json"
    # 关闭逐测试兜底时不读缓存，保持与 C-EVAL-V2@1 完全一致的采集路径
    cached = _load_coverage_cache(cache_path, base_sha, coverage_cache_version) if per_test else None
    if cached is not None:
        records, source = cached
        memo[base_sha] = (records, source)
        return list(records), not records, source, []

    warnings: list[str] = []
    parsed, _missing, _log_path = _run_baseline_coverage(base_dir, work_dir)
    source = "contexts" if _has_test_contexts(parsed) else "missing"
    if source == "contexts":
        records = _relativize_records(build_coverage_records(parsed, base_sha), base_dir)
    elif per_test:
        per_test_parsed, capped = _per_test_coverage(base_dir, tests, work_dir)
        if capped:
            warnings.append("coverage_per_test_capped")
        records = _relativize_records(build_coverage_records(per_test_parsed, base_sha), base_dir)
        source = "per_test" if records else "missing"
    else:
        records = _relativize_records(build_coverage_records(parsed, base_sha), base_dir)

    if records:
        try:
            _write_coverage_cache(cache_path, base_sha, coverage_cache_version, source, records)
        except OSError:
            # 覆盖率缓存只是加速手段，不影响本轮指标
            pass
    memo[base_sha] = (records, source)
    return list(records), not records, source, warnings


def _style_samples(base_dir: str, limit: int = 2) -> list[str]:
    tests_dir = Path(base_dir) / "tests"
    if not tests_dir.is_dir():
        return []
    samples: list[str] = []
    for path in sorted(tests_dir.glob("*.py"))[:limit]:
        try:
            samples.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return samples


def _generation_candidates(changed: list[Any], base_symbols: list[Any], coverage: list[Any]) -> list[Any]:
    """生成输入：全部变更符号，未被任何测试覆盖的排在前面（C-GEN@2）。

    基线套件几乎执行了每一行源码，若把"被覆盖"的变更符号直接剔除，输入会退化成
    只剩空行/注释命中的噪声符号，真正的变更反而进不了生成阶段；因此覆盖率只用于
    排序，具体断言哪一处由适配器按两版源码比对决定。
    """
    covered: dict[str, set[int]] = {}
    for record in coverage:
        path = str(record.file_path).replace("\\", "/")
        covered.setdefault(path, set()).update(int(line) for line in record.lines)
    base_by_id = {symbol.symbol_id: symbol for symbol in base_symbols}
    uncovered: list[Any] = []
    rest: list[Any] = []
    for item in changed:
        symbol = base_by_id.get(item.symbol_id)
        lines = covered.get(str(item.file_path).replace("\\", "/"), set())
        if symbol is None or not lines:
            uncovered.append(item)
            continue
        span = set(range(symbol.line_start, symbol.line_end + 1))
        (uncovered if span - lines else rest).append(item)
    return uncovered + rest


def _changed_sources(snapshot_dir: str, changed: list[Any]) -> dict[str, str]:
    """读取变更符号所在文件的快照源码，供生成模板提取 base 事实。"""
    sources: dict[str, str] = {}
    for item in changed:
        rel_path = str(item.file_path).replace("\\", "/")
        if rel_path in sources:
            continue
        try:
            sources[rel_path] = (Path(snapshot_dir) / rel_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return sources


def _run_generation(
    sample: dict[str, Any],
    base_dir: str,
    target_dir: str,
    changed: list[Any],
    base_symbols: list[Any],
    base_tests: list[Any],
    coverage: list[Any],
    work_dir: Path,
) -> tuple[list[dict[str, Any]], list[str]]:
    """对回归样本执行生成与双版本验证；失败降级为空生成物。"""
    if not sample.get("expected_failing_tests"):
        return [], []
    candidates = _generation_candidates(changed, base_symbols, coverage)
    if not candidates:
        return [], []

    from core.generation.generator import (
        PENDING_REVIEW,
        REJECTED,
        build_generation_request,
        generate_test_suggestions,
    )
    from core.generation.model_adapter import default_model_adapter
    from core.validation.validator import validate_generated_tests
    from services.runner.executor import LocalProcessExecutor, RunnerJob

    try:
        request = build_generation_request(
            candidates,
            base_symbols,
            base_tests,
            _style_samples(base_dir),
            base_sources=_changed_sources(base_dir, candidates),
            target_sources=_changed_sources(target_dir, candidates),
        )
        suggestions = generate_test_suggestions(request, default_model_adapter())
    except Exception:  # 模型不可用时只降级生成步骤，不中断评测
        return [], ["generation"]

    outcomes: dict[str, Any] = {}
    pending = [item for item in suggestions if item.status == PENDING_REVIEW]
    if pending:
        generation_dir = work_dir / "generation"
        generation_dir.mkdir(parents=True, exist_ok=True)
        executor = LocalProcessExecutor()

        def job_factory(*, snapshot_dir: str, node_id: str, job_id: str):
            return executor.run(
                RunnerJob(
                    job_id=job_id,
                    snapshot_dir=snapshot_dir,
                    command=[*DEFAULT_PYTEST_COMMAND, node_id],
                    timeout_s=GENERATION_TIMEOUT_S,
                    artifact_dir=str(generation_dir),
                )
            )

        try:
            validated = validate_generated_tests(
                pending,
                base_dir,
                target_dir,
                job_factory,
                repeats=2,
            )
        except Exception:
            return [], ["generation"]
        outcomes = {item.test_node_id: item for item in validated}

    entries: list[dict[str, Any]] = []
    for item in suggestions:
        outcome = outcomes.get(item.test_node_id)
        entries.append(
            {
                "test_node_id": item.test_node_id,
                "status": outcome.status if outcome is not None else item.status,
                "rationale": item.rationale,
                "analysis_source": item.analysis_source,
                "validation_evidence": outcome.evidence if outcome is not None else {},
            }
        )
    degraded = ["generation"] if entries and all(entry["status"] == REJECTED for entry in entries) else []
    return entries, degraded


def _build_artifacts(
    sample: dict[str, Any],
    repo_path: Path,
    base_sha: str,
    target_sha: str,
    work_dir: Path,
    skip_coverage: bool,
    coverage_per_test: bool,
    with_generation: bool,
    coverage_cache_dir: Path,
    coverage_cache_version: str,
    coverage_memo: dict[str, tuple[list[Any], str]],
) -> dict[str, Any]:
    from core.impact.mapper import load_sources, map_changes
    from core.impact.propagation import compute_impact
    from core.indexer.indexer import index_repository
    from core.repo.git_repo import diff_commits, export_snapshot

    repo_id = str(sample.get("repo_name", repo_path.name))
    changeset_id = f"eval-{sample['sample_id']}"
    base_dir = export_snapshot(str(repo_path), base_sha, str(work_dir / "snapshot_base"))
    target_dir = export_snapshot(str(repo_path), target_sha, str(work_dir / "snapshot_target"))

    base_index = index_repository(base_dir, repo_id, base_sha)
    target_index = index_repository(target_dir, repo_id, target_sha)

    diffs = diff_commits(str(repo_path), base_sha, target_sha)
    diff_paths = [diff.file_path for diff in diffs]
    changed = map_changes(
        diffs,
        base_index.symbols,
        target_index.symbols,
        changeset_id,
        base_sources=load_sources(base_dir, diff_paths),
        target_sources=load_sources(target_dir, diff_paths),
    )
    symbols_by_id = {symbol.symbol_id: symbol for symbol in target_index.symbols}
    impact = compute_impact(changed, target_index.edges, symbols_by_id, changeset_id)

    warnings: list[str] = []
    if skip_coverage:
        coverage: list[Any] = []
        coverage_missing = True
        coverage_source = "missing"
    else:
        coverage, coverage_missing, coverage_source, coverage_warnings = _collect_coverage(
            base_dir,
            target_index.tests,
            work_dir,
            base_sha,
            coverage_cache_dir,
            coverage_cache_version,
            coverage_per_test,
            coverage_memo,
        )
        warnings.extend(coverage_warnings)

    generated: list[dict[str, Any]] = []
    degraded: list[str] = []
    if with_generation:
        generated, degraded = _run_generation(
            sample,
            base_dir,
            target_dir,
            changed,
            base_index.symbols,
            base_index.tests,
            coverage,
            work_dir,
        )

    return {
        "changed": changed,
        "impacts": impact.items,
        "symbols": target_index.symbols,
        "tests": target_index.tests,
        "edges": target_index.edges,
        "coverage": coverage,
        "coverage_missing": coverage_missing,
        "coverage_context_source": coverage_source,
        "generated": generated,
        "degraded": degraded,
        "warnings": warnings,
    }


def _serialize_artifacts(
    sample: dict[str, Any],
    base_sha: str,
    target_sha: str,
    artifacts: dict[str, Any],
) -> dict[str, Any]:
    return {
        "cache_version": CACHE_VERSION,
        "sample_id": str(sample["sample_id"]),
        "repo_name": str(sample.get("repo_name", "")),
        "base_sha": base_sha,
        "target_sha": target_sha,
        "coverage_missing": bool(artifacts.get("coverage_missing")),
        "coverage_context_source": str(artifacts.get("coverage_context_source") or "missing"),
        "generated": list(artifacts.get("generated") or []),
        "degraded": list(artifacts.get("degraded") or []),
        "warnings": list(artifacts.get("warnings") or []),
        "changed": [asdict(item) for item in artifacts["changed"]],
        "impacts": [asdict(item) for item in artifacts["impacts"]],
        "symbols": [asdict(item) for item in artifacts["symbols"]],
        "tests": [asdict(item) for item in artifacts["tests"]],
        "edges": [asdict(item) for item in artifacts.get("edges") or []],
        "coverage": [asdict(item) for item in artifacts["coverage"]],
    }


def _deserialize_artifacts(payload: dict[str, Any]) -> dict[str, Any]:
    from core.schema.types import (
        AnalysisSource,
        ChangedSymbol,
        ChangeType,
        Confidence,
        CoverageRecord,
        DependencyEdge,
        EdgeType,
        ImpactResult,
        Symbol,
        SymbolKind,
        TestCaseInfo,
    )

    def _changed(raw: dict[str, Any]) -> ChangedSymbol:
        return ChangedSymbol(
            changeset_id=str(raw["changeset_id"]),
            symbol_id=str(raw["symbol_id"]),
            kind=SymbolKind(raw["kind"]),
            change_type=ChangeType(raw["change_type"]),
            diff_ranges=[(int(start), int(end)) for start, end in raw.get("diff_ranges", [])],
            qualified_name=str(raw["qualified_name"]),
            file_path=str(raw["file_path"]),
            confidence=Confidence(raw["confidence"]),
        )

    def _impact(raw: dict[str, Any]) -> ImpactResult:
        from core.schema.types import AnalysisSource

        return ImpactResult(
            changeset_id=str(raw["changeset_id"]),
            symbol_id=str(raw["symbol_id"]),
            path=[str(item) for item in raw.get("path", [])],
            distance=int(raw["distance"]),
            rank=int(raw["rank"]),
            confidence=Confidence(raw["confidence"]),
            analysis_source=AnalysisSource(raw["analysis_source"]),
            is_direct=bool(raw["is_direct"]),
        )

    def _symbol(raw: dict[str, Any]) -> Symbol:
        return Symbol(
            symbol_id=str(raw["symbol_id"]),
            repo_id=str(raw["repo_id"]),
            commit_sha=str(raw["commit_sha"]),
            kind=SymbolKind(raw["kind"]),
            qualified_name=str(raw["qualified_name"]),
            file_path=str(raw["file_path"]),
            line_start=int(raw["line_start"]),
            line_end=int(raw["line_end"]),
            parent_id=raw.get("parent_id"),
            metadata=dict(raw.get("metadata") or {}),
        )

    def _test(raw: dict[str, Any]) -> TestCaseInfo:
        return TestCaseInfo(
            repo_id=str(raw["repo_id"]),
            commit_sha=str(raw["commit_sha"]),
            node_id=str(raw["node_id"]),
            file_path=str(raw["file_path"]),
            name=str(raw["name"]),
            markers=[str(item) for item in raw.get("markers", [])],
        )

    def _coverage(raw: dict[str, Any]) -> CoverageRecord:
        return CoverageRecord(
            commit_sha=str(raw["commit_sha"]),
            test_node_id=str(raw["test_node_id"]),
            file_path=str(raw["file_path"]),
            lines=[int(item) for item in raw.get("lines", [])],
        )

    def _edge(raw: dict[str, Any]) -> DependencyEdge:
        return DependencyEdge(
            repo_id=str(raw["repo_id"]),
            commit_sha=str(raw["commit_sha"]),
            source_id=str(raw["source_id"]),
            target_id=str(raw["target_id"]),
            edge_type=EdgeType(raw["edge_type"]),
            analysis_source=AnalysisSource(raw["analysis_source"]),
            confidence=Confidence(raw["confidence"]),
            file_path=str(raw["file_path"]),
            line_start=int(raw["line_start"]),
            line_end=int(raw["line_end"]),
        )

    return {
        "changed": [_changed(raw) for raw in payload.get("changed", [])],
        "impacts": [_impact(raw) for raw in payload.get("impacts", [])],
        "symbols": [_symbol(raw) for raw in payload.get("symbols", [])],
        "tests": [_test(raw) for raw in payload.get("tests", [])],
        "edges": [_edge(raw) for raw in payload.get("edges", [])],
        "coverage": [_coverage(raw) for raw in payload.get("coverage", [])],
        "coverage_missing": bool(payload.get("coverage_missing")),
        "coverage_context_source": str(payload.get("coverage_context_source") or "missing"),
        "generated": list(payload.get("generated") or []),
        "degraded": list(payload.get("degraded") or []),
        "warnings": [str(item) for item in payload.get("warnings") or []],
    }


def _read_cache(
    path: Path,
    base_sha: str,
    target_sha: str,
) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = _load_json(path)
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("cache_version") != CACHE_VERSION:
        return None
    if payload.get("base_sha") != base_sha or payload.get("target_sha") != target_sha:
        return None
    try:
        return _deserialize_artifacts(payload)
    except (KeyError, TypeError, ValueError):
        return None


def _prepare_samples(
    samples: list[dict[str, Any]],
    lock: Any,
    project_root: Path,
    out_dir: Path,
    refresh: bool,
    skip_coverage: bool,
    coverage_per_test: bool = True,
    with_generation: bool = True,
    coverage_cache_dir: Path | None = None,
    coverage_cache_version: str = DEFAULT_COVERAGE_CACHE_VERSION,
) -> tuple[list[dict[str, Any]], int]:
    """为每个样本构建一次 artifacts（或复用缓存），返回准备结果与缓存命中数。"""
    cache_dir = out_dir / "cache"
    coverage_cache_dir = coverage_cache_dir or (out_dir / "coverage_cache")
    coverage_memo: dict[str, tuple[list[Any], str]] = {}
    prepared: list[dict[str, Any]] = []
    cache_hits = 0

    for sample in samples:
        sample_id = str(sample["sample_id"])
        repo_name = str(sample.get("repo_name", ""))
        base_sha, target_sha = _lookup_shas(lock, repo_name, sample_id)
        if not base_sha or not target_sha:
            prepared.append(
                {"sample": sample, "artifacts": None, "error": "skipped_missing_sha"}
            )
            continue

        cache_path = cache_dir / f"{sample_id}.json"
        if not refresh:
            cached = _read_cache(cache_path, str(base_sha), str(target_sha))
            if cached is not None:
                cache_hits += 1
                prepared.append({"sample": sample, "artifacts": cached, "error": None})
                continue

        repo_path = _repo_path(project_root, _repo_marker(sample))
        if not repo_path.is_dir():
            prepared.append(
                {"sample": sample, "artifacts": None, "error": "sample_repo_missing"}
            )
            continue

        work_dir = out_dir / "_work" / sample_id
        try:
            artifacts = _build_artifacts(
                sample,
                repo_path,
                str(base_sha),
                str(target_sha),
                work_dir,
                skip_coverage,
                coverage_per_test,
                with_generation,
                coverage_cache_dir,
                coverage_cache_version,
                coverage_memo,
            )
        except Exception as exc:  # 单样本失败不中断整轮评测
            prepared.append(
                {
                    "sample": sample,
                    "artifacts": None,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue

        try:
            _write_json(
                cache_path,
                _serialize_artifacts(sample, str(base_sha), str(target_sha), artifacts),
            )
        except OSError:
            # 缓存只是加速手段，不影响本轮指标
            pass
        prepared.append({"sample": sample, "artifacts": artifacts, "error": None})

    return prepared, cache_hits


def _evaluate_sample(
    strategy: str,
    sample: dict[str, Any],
    artifacts: dict[str, Any],
) -> dict[str, Any]:
    from core.evaluation.metrics import recall_at_20, regression_recall
    from core.evaluation.strategies import apply_strategy, rank_impact_files

    changed = artifacts["changed"]
    impacts = artifacts["impacts"]
    symbols = artifacts["symbols"]
    tests = artifacts["tests"]

    items = apply_strategy(
        strategy,
        changed=changed,
        impacts=impacts,
        symbols=symbols,
        tests=tests,
        coverage=artifacts["coverage"],
        high_risk_globs=sample.get("high_risk_globs") or DEFAULT_HIGH_RISK_GLOBS,
        edges=artifacts.get("edges") or (),
    )
    selected = [item.test_node_id for item in items]
    ranked_files = rank_impact_files(changed, impacts, symbols)
    total_count = len(tests)
    reduction = (1.0 - len(selected) / total_count) if total_count else None

    return {
        "sample_id": str(sample["sample_id"]),
        "strategy": strategy,
        "recall_at_20": recall_at_20(sample.get("expected_impact_files", []), ranked_files),
        "regression_recall": regression_recall(sample.get("expected_failing_tests", []), selected),
        "selected_count": len(selected),
        "total_count": total_count,
        "test_count_reduction": reduction,
        "coverage_missing": bool(artifacts.get("coverage_missing")),
        "coverage_context_source": str(artifacts.get("coverage_context_source") or "missing"),
        "warnings": list(artifacts.get("warnings") or []),
        "impact_ranked_files": ranked_files[:20],
        "selected_tests": selected,
        "generated": list(artifacts.get("generated") or []),
        "affected_symbols": len(changed),
        "expected_no_behavior_change": bool(sample.get("expected_no_behavior_change")),
    }


def _error_entry(strategy: str, sample: dict[str, Any], error: str) -> dict[str, Any]:
    return {
        "sample_id": str(sample["sample_id"]),
        "strategy": strategy,
        "error": error,
        "recall_at_20": 0.0,
        "regression_recall": 0.0,
    }


def _metrics_entry(report: Any) -> dict[str, Any]:
    return {
        "strategy": report.strategy,
        "samples_total": report.samples_total,
        "recall_at_20": report.recall_at_20,
        "regression_recall": report.regression_recall,
        "median_test_count_reduction": report.median_test_count_reduction,
        "verified_rate": report.verified_rate,
    }


def _subset_block(reports: dict[str, Any], sample_ids: set[str]) -> dict[str, Any]:
    from core.evaluation.metrics import build_report

    entries: dict[str, Any] = {}
    for strategy, report in reports.items():
        subset = [item for item in report.per_sample if str(item.get("sample_id")) in sample_ids]
        entries[strategy] = _metrics_entry(build_report(strategy, subset))
    # affected_symbols 与策略无关（只取决于映射结果），取任一份报告的子集求和即可。
    affected = 0
    if reports:
        first = next(iter(reports.values()))
        affected = sum(
            int(item.get("affected_symbols") or 0)
            for item in first.per_sample
            if str(item.get("sample_id")) in sample_ids
        )
    return {"samples_total": len(sample_ids), "strategies": entries, "affected_symbols": affected}


def _coverage_context_source(prepared: list[dict[str, Any]]) -> str:
    sources = [
        str(entry["artifacts"].get("coverage_context_source") or "missing")
        for entry in prepared
        if entry.get("artifacts")
    ]
    if "per_test" in sources:
        return "per_test"
    if "contexts" in sources:
        return "contexts"
    return "missing"


def _build_summary(
    reports: dict[str, Any],
    samples: list[dict[str, Any]],
    cache_hits: int,
    prepared: list[dict[str, Any]],
    include_v3_fields: bool = True,
) -> dict[str, Any]:
    from core.evaluation.metrics import sample_verification_stats

    sample_by_id = {str(sample["sample_id"]): sample for sample in samples}
    regression_ids = {
        sample_id
        for sample_id, sample in sample_by_id.items()
        if sample.get("expected_failing_tests")
    }
    no_change_ids = {
        sample_id
        for sample_id, sample in sample_by_id.items()
        if bool(sample.get("expected_no_behavior_change"))
    }
    summary: dict[str, Any] = {
        "generated_at": _utc_now(),
        "samples_total": len(samples),
        "cache_hits": cache_hits,
        "strategies": [_metrics_entry(report) for report in reports.values()],
        "subsets": {
            "regression_only": _subset_block(reports, regression_ids),
            "no_behavior_change": _subset_block(reports, no_change_ids),
        },
    }
    if include_v3_fields:
        stats = sample_verification_stats(
            next(iter(reports.values())).per_sample if reports else [],
            sorted(regression_ids),
        )
        summary["coverage_context_source"] = _coverage_context_source(prepared)
        summary["generated_samples"] = stats["generated_samples"]
        summary["verification_failures"] = stats["verification_failures"]
    return summary


def _evaluate_batch(
    strategies: list[str],
    samples_dir: Path,
    out_dir: Path,
    project_root: Path = PROJECT_ROOT,
    refresh: bool = False,
    skip_coverage: bool = False,
    coverage_per_test: bool = True,
    with_generation: bool = True,
    coverage_cache_dir: Path | None = None,
    coverage_cache_version: str = DEFAULT_COVERAGE_CACHE_VERSION,
) -> tuple[dict[str, Any], dict[str, Any]]:
    from core.evaluation.metrics import build_report

    if not strategies:
        raise ValueError("no strategy selected")
    for strategy in strategies:
        if strategy not in DEFAULT_STRATEGIES:
            raise ValueError(f"unknown strategy: {strategy}")

    lock_path = samples_dir / "lock.json"
    lock = _load_json(lock_path) if lock_path.is_file() else {}
    samples = _load_samples(samples_dir)

    prepared, cache_hits = _prepare_samples(
        samples,
        lock,
        project_root,
        out_dir,
        refresh,
        skip_coverage,
        coverage_per_test,
        with_generation,
        coverage_cache_dir,
        coverage_cache_version,
    )

    reports: dict[str, Any] = {}
    for strategy in strategies:
        per_sample: list[dict[str, Any]] = []
        for entry in prepared:
            sample = entry["sample"]
            if entry["error"]:
                per_sample.append(_error_entry(strategy, sample, entry["error"]))
                continue
            try:
                per_sample.append(_evaluate_sample(strategy, sample, entry["artifacts"]))
            except Exception as exc:  # 单样本失败不中断整轮评测
                per_sample.append(
                    _error_entry(strategy, sample, f"{type(exc).__name__}: {exc}")
                )
        report = build_report(strategy, per_sample)
        reports[strategy] = report
        _write_json(out_dir / f"{strategy}.json", asdict(report))

    summary = _build_summary(
        reports,
        samples,
        cache_hits,
        prepared,
        include_v3_fields=coverage_per_test or with_generation,
    )
    _write_json(out_dir / "summary.json", summary)
    return reports, summary


def run_batch(
    strategies: list[str] | None = None,
    samples_dir: Path | None = None,
    out_dir: Path | None = None,
    project_root: Path = PROJECT_ROOT,
    refresh: bool = False,
    skip_coverage: bool = False,
    coverage_per_test: bool = True,
    with_generation: bool = True,
    coverage_cache_dir: Path | None = None,
    coverage_cache_version: str = DEFAULT_COVERAGE_CACHE_VERSION,
) -> dict[str, Any]:
    """批量评测全部样本并写盘，返回 `summary.json` 载荷。"""
    selected = list(strategies) if strategies else list(DEFAULT_STRATEGIES)
    samples_dir = samples_dir or (project_root / "eval" / "samples")
    out_dir = out_dir or (project_root / "var" / "eval")
    _reports, summary = _evaluate_batch(
        selected,
        samples_dir,
        out_dir,
        project_root=project_root,
        refresh=refresh,
        skip_coverage=skip_coverage,
        coverage_per_test=coverage_per_test,
        with_generation=with_generation,
        coverage_cache_dir=coverage_cache_dir,
        coverage_cache_version=coverage_cache_version,
    )
    return summary


def run_evaluation(
    strategy: str,
    samples_dir: Path,
    out_dir: Path,
    project_root: Path = PROJECT_ROOT,
    skip_coverage: bool = False,
) -> Any:
    """C-EVAL@1 单策略入口（向后兼容），返回该策略的 `StrategyReport`。"""
    reports, _summary = _evaluate_batch(
        [strategy],
        samples_dir,
        out_dir,
        project_root=project_root,
        skip_coverage=skip_coverage,
    )
    return reports[strategy]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ChangeProof 批量策略评测（C-EVAL-V3@1）")
    parser.add_argument(
        "--strategies",
        default=",".join(DEFAULT_STRATEGIES),
        help=f"逗号分隔的策略列表，可选 {','.join(DEFAULT_STRATEGIES)}",
    )
    parser.add_argument(
        "--strategy",
        default=None,
        choices=DEFAULT_STRATEGIES,
        help="单策略入口（等价于 --strategies <name>，兼容 C-EVAL@1）",
    )
    parser.add_argument("--samples", default="eval/samples", help="标注样本目录")
    parser.add_argument("--out", default="var/eval", help="报告输出目录")
    parser.add_argument("--refresh", action="store_true", help="忽略样本级缓存重新构建 artifacts")
    parser.add_argument(
        "--project-root",
        default=str(PROJECT_ROOT),
        help="项目根目录（默认取脚本上级目录）",
    )
    parser.add_argument(
        "--skip-coverage",
        action="store_true",
        help="跳过基线 pytest 覆盖率采集（coverage/combo 将退化为静态来源）",
    )
    parser.add_argument(
        "--coverage-per-test",
        dest="coverage_per_test",
        action="store_true",
        default=True,
        help="基线覆盖率缺少测试级上下文时逐测试采集（默认开启）",
    )
    parser.add_argument(
        "--no-coverage-per-test",
        dest="coverage_per_test",
        action="store_false",
        help="关闭逐测试覆盖率兜底",
    )
    parser.add_argument(
        "--with-generation",
        dest="with_generation",
        action="store_true",
        default=True,
        help="对回归样本执行生成与双版本验证（默认开启）",
    )
    parser.add_argument(
        "--no-generation",
        dest="with_generation",
        action="store_false",
        help="关闭生成与双版本验证",
    )
    parser.add_argument(

        "--coverage-cache",
        default="var/eval/coverage_cache",
        help="逐测试覆盖率缓存目录（按 base_sha 共享）",
    )
    parser.add_argument(
        "--coverage-cache-version",
        default=DEFAULT_COVERAGE_CACHE_VERSION,
        help="覆盖率缓存版本；结构变化时自增以失效旧缓存",
    )
    args = parser.parse_args(argv)

    try:
        strategies = [args.strategy] if args.strategy else _parse_strategies(args.strategies)
    except ValueError as exc:
        parser.error(str(exc))

    project_root = Path(args.project_root).expanduser().resolve()
    samples_dir = Path(args.samples).expanduser()
    if not samples_dir.is_absolute():
        samples_dir = project_root / samples_dir
    out_dir = Path(args.out).expanduser()
    if not out_dir.is_absolute():
        out_dir = project_root / out_dir
    coverage_cache_dir = Path(args.coverage_cache).expanduser()
    if not coverage_cache_dir.is_absolute():
        coverage_cache_dir = project_root / coverage_cache_dir

    summary = run_batch(
        strategies,
        samples_dir.resolve(),
        out_dir.resolve(),
        project_root=project_root,
        refresh=args.refresh,
        skip_coverage=args.skip_coverage,
        coverage_per_test=args.coverage_per_test,
        with_generation=args.with_generation,
        coverage_cache_dir=coverage_cache_dir.resolve(),
        coverage_cache_version=str(args.coverage_cache_version),
    )
    for entry in summary["strategies"]:
        print(
            f"{entry['strategy']}: samples={entry['samples_total']} "
            f"recall_at_20={entry['recall_at_20']:.3f} "
            f"regression_recall={entry['regression_recall']:.3f} "
            f"median_test_count_reduction={entry['median_test_count_reduction']:.3f} "
            f"verified_rate={entry['verified_rate']:.3f}"
        )
    print(f"cache_hits={summary['cache_hits']}/{summary['samples_total']}")
    if "coverage_context_source" in summary:
        print(
            f"coverage_context_source={summary['coverage_context_source']} "
            f"generated_samples={summary['generated_samples']} "
            f"verification_failures={summary['verification_failures']}"
        )
    print(f"summary: {(out_dir / 'summary.json').resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())