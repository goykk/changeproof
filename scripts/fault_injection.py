"""五类受控故障的降级验证（C-FAULT@1）。

同一组场景既被 `tests/test_fault_injection.py` 复用，也可独立运行：
`python scripts/fault_injection.py` → 输出每类 PASS/FAIL 与 `FAULT INJECTION x/5 PASS`。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from core.coverage.mapper import parse_coverage_json
from core.coverage.selector import select_tests
from core.generation.generator import build_generation_request
from core.generation.model_adapter import ModelUnavailableError, OllamaModelAdapter
from core.schema.protocols import ChangesetRecord, RepositoryRecord
from core.schema.types import (
    ChangedSymbol,
    ChangeType,
    Confidence,
    RunStatus,
    Symbol,
    SymbolKind,
    TestCaseInfo,
)
from core.store import SqliteStore
from services.runner.executor import (
    DockerExecutor,
    LocalProcessExecutor,
    RunnerJob,
    RunnerUnavailableError,
)
from services.worker.orchestrator import AnalyzeOptions, analyze_changeset


def case_timeout(work: Path) -> dict:
    result = LocalProcessExecutor().run(
        RunnerJob(
            job_id="fault-timeout",
            snapshot_dir=str(work),
            command=["python", "-c", "import time; time.sleep(8)"],
            timeout_s=1,
            artifact_dir=str(work / "out"),
        )
    )
    return {"pass": result.status is RunStatus.TIMEOUT and result.timed_out, "detail": result.status.value}


def case_dependency_missing(work: Path) -> dict:
    """依赖缺失（pytest 命令不存在）→ 任务 failed，错误码与日志路径保留。"""
    store, task, _ = _run_analysis(
        work,
        generate_tests=False,
        pytest_command=["./definitely_missing_pytest_binary"],
    )
    failed = task.status == "failed"
    has_code = bool(task.error_code)
    store.engine.dispose()
    return {"pass": failed and has_code, "detail": f"status={task.status} error_code={task.error_code}"}


def case_runner_unavailable(work: Path) -> dict:
    """daemon 失联 → 抛 docker_unavailable，且编排降级为本地执行器。"""
    original = DockerExecutor._daemon_available
    DockerExecutor._daemon_available = lambda self: False
    try:
        try:
            DockerExecutor()
        except RunnerUnavailableError as exc:
            daemon_error = exc.code == "docker_unavailable"
        else:
            daemon_error = False

        from services.api import deps

        os.environ["CHANGEPROOF_ISOLATION_MODE"] = "docker"
        deps.get_settings.cache_clear()
        fallback = deps.get_executor()
        degraded = fallback.mode == "local"
    finally:
        DockerExecutor._daemon_available = original
        os.environ.pop("CHANGEPROOF_ISOLATION_MODE", None)
        deps.get_settings.cache_clear()
    return {
        "pass": daemon_error and degraded,
        "detail": f"docker_unavailable={daemon_error} degraded_local={degraded}",
    }


def case_coverage_missing(work: Path) -> dict:
    try:
        parse_coverage_json(str(work / "absent.json"))
    except FileNotFoundError:
        missing = True
    else:
        missing = False

    symbol = Symbol(
        symbol_id="app/core/pricing.py::apply_tax",
        repo_id="r",
        commit_sha="c",
        kind=SymbolKind.FUNCTION,
        qualified_name="apply_tax",
        file_path="app/core/pricing.py",
        line_start=1,
        line_end=5,
    )
    changed = ChangedSymbol(
        changeset_id="cs",
        symbol_id=symbol.symbol_id,
        kind=symbol.kind,
        change_type=ChangeType.MODIFY,
        diff_ranges=[(1, 2)],
        qualified_name="apply_tax",
        file_path=symbol.file_path,
        confidence=Confidence.CERTAIN,
    )
    tests = [
        TestCaseInfo(
            repo_id="r",
            commit_sha="c",
            node_id="tests/test_pricing.py::test_apply_tax",
            file_path="tests/test_pricing.py",
            name="test_apply_tax",
        )
    ]
    items = select_tests([changed], [], [symbol], tests, [], high_risk_globs=("app/core/**",), edges=())
    return {"pass": missing and len(items) > 0, "detail": f"missing={missing} selected={len(items)}"}


def case_model_failure(work: Path) -> dict:
    """模型不可达 → 降级为 `degraded_steps=["generation"]`，任务仍 reported。"""
    os.environ["CHANGEPROOF_LLM"] = "ollama"
    os.environ["CHANGEPROOF_OLLAMA_URL"] = "http://127.0.0.1:9"
    store, task, _ = _run_analysis(work, generate_tests=True)
    report = store.reports.get_report("cs") or {}
    degraded = "generation" in (report.get("degraded_steps") or [])
    store.engine.dispose()
    return {
        "pass": task.status == "reported" and degraded,
        "detail": f"status={task.status} degraded_steps={report.get('degraded_steps')}",
    }


def _run_analysis(work: Path, generate_tests: bool, pytest_command=None):
    """用真实样例仓库跑一次完整分析，返回 (store, task, lock)。"""
    store = SqliteStore(f"sqlite:///{work}/db.sqlite")
    repo_path = str(Path("var/samples/shop_api").resolve())
    store.repositories.upsert_repository(
        RepositoryRecord(
            repo_id="r",
            name="shop",
            local_path=repo_path,
            default_branch="main",
            status="ready",
        )
    )
    lock = json.loads(Path("eval/samples/lock.json").read_text(encoding="utf-8"))["shop_api"]["shop_api_rename_q1"]
    store.changesets.get_or_create_changeset(
        ChangesetRecord(
            changeset_id="cs",
            repo_id="r",
            base_sha=lock["base_sha"],
            target_sha=lock["target_sha"],
            status="created",
        )
    )
    options = AnalyzeOptions(
        repo_local_path=repo_path,
        base_sha=lock["base_sha"],
        target_sha=lock["target_sha"],
        generate_tests=generate_tests,
        pytest_command=pytest_command,
        artifacts_root=str(work / "artifacts"),
    )
    try:
        task = analyze_changeset(store, LocalProcessExecutor(), "cs", options)
    except Exception:
        # 编排失败时已通过 run.fail 落库 failed 状态，取回任务用于断言。
        tasks = store.tasks.list_tasks("cs")
        task = tasks[-1] if tasks else None
    return store, task, lock


def case_snapshot_missing(work: Path) -> dict:
    """快照目录缺失 → 执行器抛 bad_job（不得静默返回成功）。"""
    try:
        LocalProcessExecutor().run(
            RunnerJob(
                job_id="fault-missing-snapshot",
                snapshot_dir=str(work / "absent"),
                command=["python", "-c", "print(1)"],
                artifact_dir=str(work / "out"),
            )
        )
    except RunnerUnavailableError as exc:
        return {"pass": exc.code == "bad_job", "detail": exc.code}
    return {"pass": False, "detail": "未抛出 RunnerUnavailableError"}


def case_git_command_failed(work: Path) -> dict:
    """非法提交导出快照 → GitCommandError(code=git_command_failed)。"""
    from core.repo import GitCommandError, export_snapshot

    repo = str(Path("var/samples/shop_api").resolve())
    try:
        export_snapshot(repo, "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef", str(work / "snap"))
    except GitCommandError as exc:
        return {"pass": exc.code == "git_command_failed", "detail": exc.code}
    return {"pass": False, "detail": "未抛出 GitCommandError"}


CASES = (
    ("timeout", case_timeout),
    ("dependency_missing", case_dependency_missing),
    ("runner_unavailable", case_runner_unavailable),
    ("coverage_missing", case_coverage_missing),
    ("model_failure", case_model_failure),
    ("snapshot_missing", case_snapshot_missing),
    ("git_command_failed", case_git_command_failed),
)


def run_all() -> list[dict]:
    results: list[dict] = []
    for name, case in CASES:
        with tempfile.TemporaryDirectory(prefix=f"fault_{name}_", ignore_cleanup_errors=True) as tmp:
            outcome = case(Path(tmp))
        results.append({"case": name, **outcome})
    return results


def main() -> int:
    results = run_all()
    passed = sum(1 for item in results if item["pass"])
    for item in results:
        print(f"[{'ok' if item['pass'] else 'FAIL'}] {item['case']}: {item['detail']}")
    print(f"FAULT INJECTION {passed}/{len(results)} PASS")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())