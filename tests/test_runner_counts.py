"""执行计数必须来自 junitxml（C-RUNNER@2 的不变式防护）。"""

from __future__ import annotations

from pathlib import Path

from services.runner.executor import LocalProcessExecutor, RunnerJob
from core.schema.types import RunStatus


def _snapshot(tmp_path: Path) -> Path:
    tests = tmp_path / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "test_sample.py").write_text(
        "def test_ok():\n    assert 1 + 1 == 2\n\n\ndef test_bad():\n    assert 1 + 1 == 3\n",
        encoding="utf-8",
    )
    return tmp_path


def test_counts_come_from_junitxml(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    result = LocalProcessExecutor().run(
        RunnerJob(
            job_id="unit-normal",
            snapshot_dir=str(snapshot),
            command=["python", "-m", "pytest", "-q", "tests"],
            timeout_s=120,
            artifact_dir=str(tmp_path / "artifacts"),
        )
    )
    assert result.status is RunStatus.FAILED
    assert result.selected_count == 2
    assert result.passed_count == 1
    assert result.failed_count == 1
    assert result.error_count == 0
    assert "counts_missing=false" in Path(result.log_path).read_text(encoding="utf-8")


def test_missing_junit_marks_counts_missing(tmp_path: Path) -> None:
    result = LocalProcessExecutor().run(
        RunnerJob(
            job_id="unit-missing",
            snapshot_dir=str(tmp_path),
            command=["python", "-c", "print('not a pytest run')"],
            timeout_s=60,
            artifact_dir=str(tmp_path / "artifacts"),
        )
    )
    assert result.selected_count == 0
    assert result.passed_count == 0
    assert result.failed_count == 0
    assert "counts_missing=true" in Path(result.log_path).read_text(encoding="utf-8")


def test_timeout_returns_timeout_status(tmp_path: Path) -> None:
    result = LocalProcessExecutor().run(
        RunnerJob(
            job_id="unit-timeout",
            snapshot_dir=str(tmp_path),
            command=["python", "-c", "import time; time.sleep(8)"],
            timeout_s=1,
            artifact_dir=str(tmp_path / "artifacts"),
        )
    )
    assert result.status is RunStatus.TIMEOUT
    assert result.timed_out is True