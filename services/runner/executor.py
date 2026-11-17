"""隔离执行器：本地进程与 Docker 两种实现，统一返回 RunResult。"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import signal
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from core.schema.types import RunResult, RunStatus

from services.runner.pytest_support import parse_pytest_summary

DEFAULT_ENV_ALLOWLIST: list[str] = [
    "PATH",
    "PYTHONPATH",
    "HOME",
    "LANG",
    "TMPDIR",
    "SYSTEMROOT",
    "CHANGEPROOF_*",
]

DEFAULT_IMAGE = "changeproof-runner:latest"
DOCKER_ARTIFACTS_MOUNT = "/artifacts"
DOCKER_SNAPSHOT_MOUNT = "/work"
_COV_JSON_PREFIX = "--cov-report=json:"



@dataclass
class RunnerJob:
    job_id: str
    snapshot_dir: str
    command: list[str]
    cwd: str = "."
    timeout_s: int = 600
    max_output_bytes: int = 5_000_000
    network: bool = False
    artifact_dir: str | None = None
    env_allowlist: list[str] = field(default_factory=lambda: list(DEFAULT_ENV_ALLOWLIST))


class Executor(Protocol):
    def run(self, job: RunnerJob) -> RunResult: ...

    @property
    def mode(self) -> str: ...


class RunnerUnavailableError(RuntimeError):
    """执行器不可用（daemon 不可达、命令不存在、快照目录缺失等）。"""

    def __init__(
        self,
        message: str = "runner unavailable",
        *,
        code: str = "runner_unavailable",
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


class LocalProcessExecutor:
    """以子进程方式执行命令；本地模式无法强制断网，只如实记录。"""

    def __init__(self, max_concurrency: int = 1) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._gate = threading.BoundedSemaphore(max_concurrency)

    @property
    def mode(self) -> str:
        return "local"

    def run(self, job: RunnerJob) -> RunResult:
        _validate_job(job)
        snapshot_root = Path(job.snapshot_dir).resolve()
        work_dir = _resolve_work_dir(snapshot_root, job.cwd)
        command, junit_path = _ensure_junit(list(job.command), _artifact_base(job), "local")
        with self._gate:
            return _execute(
                job,
                argv=command,
                work_dir=work_dir,
                isolation_mode="local",
                network_enforced=False,
                junit_path=junit_path,
            )


class DockerExecutor:
    """以 docker run 执行命令；daemon 不可达时抛 RunnerUnavailableError。"""

    def __init__(self, image: str = DEFAULT_IMAGE) -> None:
        self._image = image
        # 构造即探测 daemon：`services/api/deps.get_executor` 依赖此异常降级为本地执行器。
        if not self._daemon_available():
            raise RunnerUnavailableError(
                "docker daemon is not reachable",
                code="docker_unavailable",
            )

    @property
    def mode(self) -> str:
        return "docker"

    def run(self, job: RunnerJob) -> RunResult:
        _validate_job(job)
        if not self._daemon_available():
            raise RunnerUnavailableError(
                "docker daemon is not reachable",
                code="docker_unavailable",
            )

        snapshot_root = Path(job.snapshot_dir).resolve()
        work_rel = _relative_work_dir(job.cwd)
        host_work_dir = snapshot_root / work_rel if work_rel.parts else snapshot_root
        container_work_dir = (
            f"{DOCKER_SNAPSHOT_MOUNT}/{work_rel.as_posix()}"
            if work_rel.parts
            else DOCKER_SNAPSHOT_MOUNT
        )
        artifact_base = _artifact_base(job)
        command, coverage_targets = _rewrite_docker_coverage(
            job.command,

            artifact_base=artifact_base,
        )
        command, junit_path = _ensure_junit(command, artifact_base, "docker")
        container_name = _container_name(job.job_id)
        # 同名容器可能因上次异常退出而残留（--rm 只在正常退出时清理）
        _docker_remove(container_name)

        argv = [
            "docker",
            "run",
            "--rm",
            "--name",
            container_name,
            "--network=none",
            "--cpus=1",
            "--memory=512m",
            "--pids-limit=128",
            "--user",
            "65534:65534",
            "-v",
            f"{snapshot_root}:{DOCKER_SNAPSHOT_MOUNT}:ro",
            "-v",
            f"{artifact_base}:{DOCKER_ARTIFACTS_MOUNT}",
            "-w",
            container_work_dir,
            self._image,
            *command,
        ]
        return _execute(
            job,
            argv=argv,
            command_label=command,
            work_dir=host_work_dir,
            isolation_mode="docker",
            network_enforced=True,
            artifact_base=artifact_base,
            coverage_targets=coverage_targets,
            junit_path=junit_path,
            timeout_handler=lambda: _docker_remove(container_name, stop=True),
        )

    def _daemon_available(self) -> bool:
        try:
            completed = subprocess.run(
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                shell=False,
                capture_output=True,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return completed.returncode == 0


def _validate_job(job: RunnerJob) -> None:
    if not job.command or not all(isinstance(part, str) for part in job.command):
        raise RunnerUnavailableError("job command must be a non-empty list[str]", code="bad_job")
    if not job.snapshot_dir:
        raise RunnerUnavailableError("job snapshot_dir is required", code="bad_job")
    snapshot_root = Path(job.snapshot_dir)
    if not snapshot_root.is_dir():
        raise RunnerUnavailableError(
            f"snapshot dir not found: {job.snapshot_dir}", code="bad_job"
        )


def _relative_work_dir(cwd: str) -> Path:
    rel = Path(cwd or ".")
    if rel.is_absolute():
        raise RunnerUnavailableError(f"cwd must be relative: {cwd}", code="bad_job")
    if any(part == ".." for part in rel.parts):
        raise RunnerUnavailableError(f"cwd must stay inside snapshot: {cwd}", code="bad_job")
    return Path(*[part for part in rel.parts if part not in (".", "")])


def _resolve_work_dir(snapshot_root: Path, cwd: str) -> Path:
    rel = _relative_work_dir(cwd)
    work_dir = (snapshot_root / rel).resolve() if rel else snapshot_root
    if not work_dir.is_dir():
        raise RunnerUnavailableError(f"job cwd not found: {cwd}", code="bad_job")
    return work_dir


def _artifact_base(job: RunnerJob) -> Path:
    if job.artifact_dir:
        base = Path(job.artifact_dir)
    else:
        base = Path("var") / "artifacts" / job.job_id
    base = base.resolve()
    base.mkdir(parents=True, exist_ok=True)
    return base


def _container_name(job_id: str) -> str:
    # 容器名必须是合法 DNS 标签，且长度受限；job_id 可能含任意字符
    digest = hashlib.sha1(job_id.encode("utf-8")).hexdigest()[:12]
    return f"changeproof-runner-{digest}"


def _docker_remove(name: str, *, stop: bool = False) -> None:
    """清理容器：`--rm` 只在容器正常退出时生效，超时/异常需要显式 stop + rm。"""
    if stop:
        try:
            subprocess.run(
                ["docker", "stop", "--time", "1", name],
                shell=False,
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        subprocess.run(
            ["docker", "rm", "-f", name],
            shell=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _rewrite_docker_coverage(
    command: Sequence[str],
    *,
    artifact_base: Path,
) -> tuple[list[str], list[tuple[str, str]]]:
    """把覆盖率 JSON 的宿主路径改写为容器内 `/artifacts/...`，并记录宿主回读目标。

    容器内 `/work` 只读，pytest-cov 无法把 JSON 写到快照目录，因此任何宿主路径
    都必须重定向到可写的 `/artifacts` 挂载；映射表用于执行后回读（必要时复制到
    调用方指定的宿主路径）。
    """
    rewritten: list[str] = []
    targets: list[tuple[str, str]] = []
    used: set[str] = set()

    for arg in command:
        if arg == "--cov-report=json":
            host_target = (artifact_base / "coverage.json").resolve()
        elif arg.startswith(_COV_JSON_PREFIX):
            raw = arg.split(":", 1)[1].strip()
            if not raw:
                rewritten.append(arg)
                continue
            host_target = _host_coverage_target(raw, artifact_base)
        else:
            rewritten.append(arg)
            continue

        rel = _artifact_relative(host_target, artifact_base, used)
        container_path = f"{DOCKER_ARTIFACTS_MOUNT}/{rel.as_posix()}"
        targets.append((container_path, str(host_target)))
        rewritten.append(f"{_COV_JSON_PREFIX}{container_path}")

    return rewritten, targets


def _host_coverage_target(raw: str, artifact_base: Path) -> Path:
    if raw.startswith(f"{DOCKER_ARTIFACTS_MOUNT}/"):
        return (artifact_base / raw[len(DOCKER_ARTIFACTS_MOUNT) + 1 :]).resolve()
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate.resolve()
    # 相对路径按 artifact_dir 解析：容器内 cwd 只读，写不进去
    return (artifact_base / candidate).resolve()


def _artifact_relative(host_target: Path, artifact_base: Path, used: set[str]) -> Path:
    """宿主路径在 artifact_dir 内时保留相对结构，否则退化为唯一文件名。"""
    try:
        rel = host_target.relative_to(artifact_base)
    except ValueError:
        rel = Path(host_target.name)
    base = rel.as_posix()
    if base not in used:
        used.add(base)
        return rel
    index = 1
    while True:
        candidate = rel.with_name(f"{rel.stem}-{index}{rel.suffix}")
        if candidate.as_posix() not in used:
            used.add(candidate.as_posix())
            return candidate
        index += 1


def _collect_container_coverage(
    artifact_base: Path,
    targets: Sequence[tuple[str, str]],
) -> str | None:
    """从 `/artifacts` 挂载回读覆盖率 JSON，必要时复制回调用方给出的宿主路径。"""
    for container_path, host_target in targets:
        rel = container_path[len(DOCKER_ARTIFACTS_MOUNT) + 1 :]
        produced = artifact_base / rel
        if not produced.is_file():
            continue
        target = Path(host_target)
        if target != produced:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(produced, target)
            except OSError:
                return str(produced)
        return str(target)
    return None


def _log_path(job: RunnerJob, artifact_base: Path) -> Path:
    log_path = artifact_base / (f"{job.job_id}.log" if job.artifact_dir else "run.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    return log_path


def _build_env(allowlist: list[str]) -> dict[str, str]:
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if any(fnmatch.fnmatchcase(key.upper(), pattern.upper()) for pattern in allowlist):
            env[key] = value
    return env


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0


def _kill_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                shell=False,
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except OSError:
            pass
    try:
        process.kill()
    except OSError:
        pass


def _resolve_coverage_path(
    argv: list[str],
    work_dir: Path,
    artifact_base: Path,
    isolation_mode: str,
) -> str | None:
    raw: str | None = None
    for arg in argv:
        if arg.startswith("--cov-report=json:"):
            raw = arg.split(":", 1)[1]
            break
    if not raw:
        return None

    if isolation_mode == "docker" and raw.startswith(f"{DOCKER_ARTIFACTS_MOUNT}/"):
        mapped = artifact_base / raw[len(DOCKER_ARTIFACTS_MOUNT) + 1 :]
        return str(mapped) if mapped.is_file() else None

    candidate = Path(raw)
    resolved = candidate if candidate.is_absolute() else work_dir / candidate
    resolved = resolved.resolve()
    return str(resolved) if resolved.is_file() else None


def _is_pytest_command(argv: Sequence[str]) -> bool:
    return any(str(part) == "pytest" for part in argv)


def _ensure_junit(
    argv: list[str],
    artifact_base: Path,
    isolation_mode: str,
) -> tuple[list[str], Path]:
    """pytest 命令统一追加 ``--junitxml``，计数只以该文件为准（C-RUNNER@2）。

    返回 (命令, junit 宿主路径)。非 pytest 命令或调用方已显式给出 `--junitxml` 时命令保持不变。
    """
    host_path = artifact_base / "junit.xml"
    if isolation_mode == "docker":
        host_path = artifact_base / "junit.xml"
        container_path = f"{DOCKER_ARTIFACTS_MOUNT}/{host_path.name}"
        if "--junitxml" in argv or not _is_pytest_command(argv):
            return argv, host_path
        return [*argv, "--junitxml", container_path], host_path
    if "--junitxml" in argv or not _is_pytest_command(argv):
        return argv, host_path
    return [*argv, "--junitxml", str(host_path)], host_path


def _junit_counts(junit_path: Path | None) -> dict[str, int]:
    """解析 junitxml 得到 pytest 计数；文件缺失或不可解析时返回空字典。"""
    if junit_path is None or not junit_path.is_file():
        return {}
    try:
        root = ET.parse(junit_path).getroot()
    except (ET.ParseError, OSError):
        return {}
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    total = failures = errors = skipped = 0
    for suite in suites:
        total += int(suite.get("tests") or 0)
        failures += int(suite.get("failures") or 0)
        errors += int(suite.get("errors") or 0)
        skipped += int(suite.get("skipped") or 0)
    return {
        "selected": total,
        "passed": max(0, total - failures - errors - skipped),
        "failed": failures,
        "error": errors,
        "skipped": skipped,
    }


def _execute(
    job: RunnerJob,
    *,
    argv: list[str],
    work_dir: Path,
    isolation_mode: str,
    network_enforced: bool,
    artifact_base: Path | None = None,
    timeout_handler: Callable[[], None] | None = None,
    coverage_targets: Sequence[tuple[str, str]] | None = None,
    command_label: Sequence[str] | None = None,
    junit_path: Path | None = None,
) -> RunResult:
    base = artifact_base if artifact_base is not None else _artifact_base(job)
    log_path = _log_path(job, base)
    started = time.monotonic()

    stdout = b""
    stderr = b""
    timed_out = False
    exit_code = -1
    status = RunStatus.ERROR
    startup_error: str | None = None

    try:
        process = subprocess.Popen(
            argv,
            shell=False,
            cwd=str(work_dir),
            env=_build_env(job.env_allowlist),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_creation_flags(),
            start_new_session=os.name != "nt",
        )
    except FileNotFoundError as exc:
        raise RunnerUnavailableError(
            f"command not found: {argv[0]}", code="bad_job", detail=str(exc)
        ) from exc
    except OSError as exc:
        startup_error = f"{type(exc).__name__}: {exc}"
        process = None

    if process is not None:
        try:
            stdout, stderr = process.communicate(timeout=job.timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            if timeout_handler is not None:
                timeout_handler()
            _kill_process_tree(process)
            try:
                stdout, stderr = process.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                stdout, stderr = b"", b""
        exit_code = process.returncode if process.returncode is not None else -1
        if timed_out:
            status = RunStatus.TIMEOUT
        elif exit_code == 0:
            status = RunStatus.PASSED
        else:
            status = RunStatus.FAILED

    duration_ms = int((time.monotonic() - started) * 1000)
    budget = max(0, int(job.max_output_bytes))
    out_bytes = stdout or b""
    err_bytes = stderr or b""
    out_text = out_bytes[:budget].decode("utf-8", errors="replace")
    remaining = max(0, budget - min(len(out_bytes), budget))
    err_text = err_bytes[:remaining].decode("utf-8", errors="replace")
    truncated = len(out_bytes) > budget or len(err_bytes) > remaining

    metrics = _junit_counts(junit_path)
    counts_missing = not metrics
    if counts_missing:
        metrics = {"selected": 0, "passed": 0, "failed": 0, "error": 0, "skipped": 0}

    log_lines = [
        f"network_enforced={'true' if network_enforced else 'false'}",
        f"# job_id={job.job_id}",
        f"# isolation_mode={isolation_mode}",
        f"# cwd={work_dir}",
        f"# command={' '.join(argv)}",
        f"# timeout_s={job.timeout_s}",
        f"# counts_missing={str(counts_missing).lower()}",
    ]
    if command_label:
        log_lines.append(f"# container_command={' '.join(command_label)}")
    if startup_error:
        log_lines.append(f"# startup_error={startup_error}")
    log_lines.extend(
        [
            "# --- stdout ---",
            out_text,
            "# --- stderr ---",
            err_text,
            "# --- summary ---",
            f"# exit_code={exit_code}",
            f"# duration_ms={duration_ms}",
            f"# timed_out={str(timed_out).lower()}",
            f"# truncated={str(truncated).lower()}",
        ]
    )
    log_path.write_text("\n".join(log_lines), encoding="utf-8")

    if timed_out or startup_error:
        coverage_path = None
    elif coverage_targets is not None:
        coverage_path = _collect_container_coverage(base, coverage_targets)
    else:
        coverage_path = _resolve_coverage_path(argv, work_dir, base, isolation_mode)

    return RunResult(
        exit_code=exit_code,
        status=status,
        duration_ms=duration_ms,
        log_path=str(log_path),
        isolation_mode=isolation_mode,
        selected_count=metrics["selected"],
        passed_count=metrics["passed"],
        failed_count=metrics["failed"],
        error_count=metrics["error"],
        skipped_count=metrics["skipped"],
        timed_out=timed_out,
        coverage_path=coverage_path,
    )