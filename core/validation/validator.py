"""生成测试的双版本验证（C-GEN@1）。

判定唯一口径：``verified = base_passed ∧ target_failed ∧ stable``；base 失败即
``rejected``，其余为 ``pending_review``。``core`` 不得 import ``services``，因此
“怎么跑”由编排层通过 ``job_factory`` 注入，本模块只负责落测试、重复执行与判定。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from core.generation.generator import PENDING_REVIEW, REJECTED, VERIFIED
from core.schema.types import RunResult, RunStatus

# job_factory(snapshot_dir=..., node_id=..., job_id=...) -> RunResult
JobFactory = Callable[..., RunResult]

# 供复用与外部导入的默认执行命令（实际命令由 job_factory 决定）。
DEFAULT_PYTEST_COMMAND: list[str] = ["python", "-m", "pytest", "-q"]


@dataclass(frozen=True)
class ValidationOutcome:
    test_node_id: str
    base_passed: bool
    target_failed: bool
    stable: bool
    status: str
    evidence: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "test_node_id": self.test_node_id,
            "base_passed": self.base_passed,
            "target_failed": self.target_failed,
            "stable": self.stable,
            "status": self.status,
            "evidence": self.evidence,
        }


def _status_value(result: RunResult) -> str:
    return result.status.value if isinstance(result.status, RunStatus) else str(result.status)


def _run_entry(result: RunResult) -> dict[str, Any]:
    return {
        "status": _status_value(result),
        "exit_code": result.exit_code,
        "log_path": result.log_path,
        "timed_out": result.timed_out,
    }


def _consistent(results: Sequence[RunResult]) -> bool:
    return len({(_status_value(item), item.exit_code) for item in results}) <= 1


def _write_test(root: Path, rel_path: str, content: str) -> Path:
    target = root
    for part in rel_path.replace("\\", "/").split("/"):
        target = target / part
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def _remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    try:
        path.parent.rmdir()
    except OSError:
        pass


def validate_generated_tests(
    tests: Sequence[Any],
    base_snapshot: str,
    target_snapshot: str,
    job_factory: JobFactory,
    repeats: int = 2,
) -> list[ValidationOutcome]:
    """在 base/target 快照上各重复执行 ``repeats`` 次并给出唯一判定。"""
    rounds = max(1, int(repeats))
    outcomes: list[ValidationOutcome] = []
    for position, test in enumerate(tests):
        rel_path, _, node_name = test.test_node_id.partition("::")
        if not rel_path or not node_name:
            outcomes.append(
                ValidationOutcome(
                    test_node_id=test.test_node_id,
                    base_passed=False,
                    target_failed=False,
                    stable=False,
                    status=REJECTED,
                    evidence={"error": "invalid_test_node_id"},
                )
            )
            continue

        base_file = _write_test(Path(base_snapshot), rel_path, test.content)
        target_file = _write_test(Path(target_snapshot), rel_path, test.content)
        base_runs: list[RunResult] = []
        target_runs: list[RunResult] = []
        try:
            for index in range(rounds):
                base_runs.append(
                    job_factory(
                        snapshot_dir=base_snapshot,
                        node_id=test.test_node_id,
                        job_id=f"gen-{position}-base-{index}",
                    )
                )
                target_runs.append(
                    job_factory(
                        snapshot_dir=target_snapshot,
                        node_id=test.test_node_id,
                        job_id=f"gen-{position}-target-{index}",
                    )
                )
        finally:
            _remove(base_file)
            _remove(target_file)

        base_passed = bool(base_runs) and all(item.status == RunStatus.PASSED for item in base_runs)
        target_failed = bool(target_runs) and all(item.status == RunStatus.FAILED for item in target_runs)
        stable = bool(base_runs) and bool(target_runs) and _consistent(base_runs) and _consistent(target_runs)

        if not base_passed:
            status = REJECTED
        elif target_failed and stable:
            status = VERIFIED
        else:
            status = PENDING_REVIEW

        outcomes.append(
            ValidationOutcome(
                test_node_id=test.test_node_id,
                base_passed=base_passed,
                target_failed=target_failed,
                stable=stable,
                status=status,
                evidence={
                    "repeats": rounds,
                    "base": [_run_entry(item) for item in base_runs],
                    "target": [_run_entry(item) for item in target_runs],
                },
            )
        )
    return outcomes