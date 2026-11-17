"""实测 Docker 执行器：构建镜像、断网自检、容器内 pytest 与覆盖率回读。

用法：python infra/runner/verify_isolation.py [--skip-build]

脚本输出 `DOCKER RUNNER PASS|FAIL` 并以 0/1 退出；证据留在 `var/runner_smoke/`。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.repo.git_repo import export_snapshot  # noqa: E402
from core.schema.types import RunStatus  # noqa: E402
from services.runner.executor import DockerExecutor, RunnerJob  # noqa: E402
from services.runner.pytest_support import build_pytest_command  # noqa: E402

DEFAULT_IMAGE = "changeproof-runner:latest"
DEFAULT_SAMPLE = "var/samples/shop_api"
DEFAULT_WORK = "var/runner_smoke"
TEST_TARGETS = ["tests/test_core_pricing.py"]
TIMEOUT_COMMAND = ["python", "-c", "import time; time.sleep(60)"]


def _docker(*args: str, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def build_image(image: str) -> None:
    completed = _docker(
        "build",
        "-f",
        "infra/Dockerfile.runner",
        "-t",
        image,
        str(REPO_ROOT),
        timeout=1800,
    )
    if completed.returncode != 0:
        raise SystemExit(f"镜像构建失败：\n{completed.stdout}\n{completed.stderr}")
    print(f"[ok] 镜像已构建：{image}")


def check_network(image: str) -> list[str]:
    completed = _docker(
        "run", "--rm", "--network=none", "--user", "65534:65534",
        image, "python", "/opt/runner/net_check.py",
    )
    if completed.returncode == 0 and "network_enforced=true" in completed.stdout:
        print("[ok] 断网自检通过（容器内无法访问外网）")
        return []
    return [f"断网自检失败：exit={completed.returncode} out={completed.stdout.strip()}"]


def target_sha(lock_path: Path, sample_name: str) -> str:
    payload: dict[str, Any] = json.loads(lock_path.read_text(encoding="utf-8"))
    entry = payload.get(sample_name)
    if not isinstance(entry, dict):
        raise SystemExit(f"{lock_path} 缺少 {sample_name}")
    # 兼容 C-SAMPLE@1 扁平结构与 C-SAMPLE-V2@1 嵌套结构
    # （{repo: {sample_id: {base_sha, target_sha}}}）。
    if entry.get("target_sha"):
        return str(entry["target_sha"])
    for value in entry.values():
        if isinstance(value, dict) and value.get("target_sha"):
            return str(value["target_sha"])
    raise SystemExit(f"{lock_path} 缺少 {sample_name}.target_sha")


def check_sample_run(executor: DockerExecutor, snapshot: str, artifacts: Path) -> list[str]:
    coverage_json = (artifacts / "coverage.json").resolve()
    coverage_json.unlink(missing_ok=True)
    result = executor.run(
        RunnerJob(
            job_id="t18-sample",
            snapshot_dir=snapshot,
            command=build_pytest_command(TEST_TARGETS, str(coverage_json)),
            timeout_s=300,
            artifact_dir=str(artifacts),
        )
    )
    failures: list[str] = []
    if result.status is not RunStatus.PASSED or result.exit_code != 0:
        failures.append(f"样例执行未通过：status={result.status.value} exit={result.exit_code}")
    if result.isolation_mode != "docker":
        failures.append(f"isolation_mode={result.isolation_mode}")
    if result.passed_count < 2:
        failures.append(f"passed_count={result.passed_count}")

    log_path = Path(result.log_path)
    first_line = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[:1]
    if first_line != ["network_enforced=true"]:
        failures.append(f"日志首行不是 network_enforced=true：{first_line}")

    if result.coverage_path is None or not Path(result.coverage_path).is_file():
        failures.append(f"覆盖率未回读：coverage_path={result.coverage_path}")
    elif Path(result.coverage_path) != coverage_json:
        failures.append(f"覆盖率路径与宿主目标不一致：{result.coverage_path}")
    else:
        failures.extend(_check_coverage_contexts(coverage_json))

    if not failures:
        print(f"[ok] 样例执行通过：log={log_path} coverage={result.coverage_path}")
    return failures


def _check_coverage_contexts(coverage_json: Path) -> list[str]:
    payload = json.loads(coverage_json.read_text(encoding="utf-8"))
    files = payload.get("files") or {}
    pricing = next(
        (entry for path, entry in files.items() if str(path).replace("\\", "/").endswith("app/core/pricing.py")),
        None,
    )
    if not isinstance(pricing, dict):
        return [f"覆盖率 JSON 缺少 app/core/pricing.py：{sorted(files)[:5]}"]
    contexts = pricing.get("contexts") or {}
    found = {
        context.rsplit("|", 1)[0]
        for value in contexts.values()
        for context in value
        if context
    }
    if not found:
        return ["覆盖率 JSON 未包含 --cov-context=test 的测试上下文"]
    print(f"[ok] 覆盖率上下文回读：{sorted(found)}")
    return []


def check_timeout(executor: DockerExecutor, snapshot: str, work: Path, image: str) -> list[str]:
    result = executor.run(
        RunnerJob(
            job_id="t18-timeout",
            snapshot_dir=snapshot,
            command=TIMEOUT_COMMAND,
            timeout_s=5,
            artifact_dir=str(work / "timeout_artifacts"),
        )
    )
    failures: list[str] = []
    if result.status is not RunStatus.TIMEOUT or not result.timed_out:
        failures.append(f"超时未返回 TIMEOUT：status={result.status.value} timed_out={result.timed_out}")

    leftovers = _docker("ps", "--filter", f"ancestor={image}", "--format", "{{.ID}}")
    if leftovers.returncode == 0 and leftovers.stdout.strip():
        failures.append(f"超时后残留容器：{leftovers.stdout.split()}")
    if not failures:
        print("[ok] 超时通过停止容器返回 TIMEOUT，无残留容器")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Docker 执行器实测")
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--sample", default=DEFAULT_SAMPLE)
    parser.add_argument("--work", default=DEFAULT_WORK)
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()

    failures: list[str] = []
    if not args.skip_build:
        build_image(args.image)
    failures.extend(check_network(args.image))

    sample = (REPO_ROOT / args.sample).resolve()
    work = (REPO_ROOT / args.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    snapshot = export_snapshot(
        str(sample),
        target_sha(REPO_ROOT / "eval" / "samples" / "lock.json", sample.name),
        str(work / "snapshot"),
    )
    artifacts = work / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)

    executor = DockerExecutor(image=args.image)
    failures.extend(check_sample_run(executor, snapshot, artifacts))
    failures.extend(check_timeout(executor, snapshot, work, args.image))

    if failures:
        print("DOCKER RUNNER FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("DOCKER RUNNER PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())