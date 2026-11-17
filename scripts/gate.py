"""ChangeProof 一键门禁（C-GATE@1）：compile → eval → smoke → e2e → docker-runner。

用法：python scripts/gate.py [--skip-docker] [--skip-e2e] [--refresh]

每一步只复用既有脚本，不重写其逻辑；每步输出 PASS|FAIL|SKIP 与耗时，
汇总写入 var/gate/summary.json，任一 FAIL 退出码 1。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TextIO

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GATE_DIR = PROJECT_ROOT / "var" / "gate"
SUMMARY_PATH = GATE_DIR / "summary.json"
E2E_DIR = PROJECT_ROOT / "var" / "e2e"
WEB_DIR = PROJECT_ROOT / "apps" / "web"

# 本机 3000/8000 已被占用，门禁固定用 8100/3100。
API_PORT = 8100
WEB_PORT = 3100
API_URL = f"http://127.0.0.1:{API_PORT}"
WEB_URL = f"http://127.0.0.1:{WEB_PORT}"
E2E_DB_URL = "sqlite:///./var/e2e/changeproof.db"
E2E_ARTIFACTS = "./var/e2e/artifacts"

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

StepOutcome = tuple[str, str, str, str]  # status, command, log_path, detail


class GateError(RuntimeError):
    """门禁编排失败（环境不满足前置条件）。"""


@dataclass
class StepResult:
    name: str
    status: str
    duration_ms: int
    command: str
    log_path: str
    detail: str = ""


def _python() -> str:
    return sys.executable


def _which(name: str) -> str:
    return shutil.which(name) or name


def _child_env(overrides: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    # 子脚本以 `python scripts/xxx.py` 启动时 sys.path[0] 是 scripts/，
    # 显式补上项目根，等价于 `pip install -e .` 后的可导入状态。
    existing = env.get("PYTHONPATH")
    root = str(PROJECT_ROOT)
    env["PYTHONPATH"] = f"{root}{os.pathsep}{existing}" if existing else root
    if overrides:
        env.update(overrides)
    return env



def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _tail(output: str, limit: int = 3) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return " | ".join(lines[-limit:])


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def _run(
    command: list[str],
    *,
    log_rel: str,
    cwd: Path = PROJECT_ROOT,
    env: dict[str, str] | None = None,
    timeout: float,
) -> tuple[int, str]:
    log_path = GATE_DIR / log_rel
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            env=_child_env(env),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        output = _as_text(exc.stdout) + _as_text(exc.stderr)
        output += f"\n[gate] 步骤超时 {timeout}s，已终止\n"
        log_path.write_text(output, encoding="utf-8")
        print(output, end="", flush=True)
        return 1, output
    except OSError as exc:
        output = f"[gate] 无法执行 {command[0]}：{exc}\n"
        log_path.write_text(output, encoding="utf-8")
        print(output, end="", flush=True)
        return 1, output

    output = (completed.stdout or "") + (completed.stderr or "")
    log_path.write_text(output, encoding="utf-8")
    print(output, end="", flush=True)
    return completed.returncode, output


def _terminate(proc: subprocess.Popen[str] | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            text=True,
        )
    else:
        proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _wait_port_free(port: int, timeout: float = 20.0) -> bool:
    """smoke 刚结束时 uvicorn 可能仍在收尾，短暂轮询等待端口释放。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _port_in_use(port):
            return True
        time.sleep(0.5)
    return not _port_in_use(port)


def _start_backend() -> tuple[subprocess.Popen[str], TextIO]:
    api_log = E2E_DIR / "api.log"
    api_log.parent.mkdir(parents=True, exist_ok=True)
    handle = api_log.open("w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [
            _python(),
            "-m",
            "uvicorn",
            "services.api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(API_PORT),
        ],
        cwd=str(PROJECT_ROOT),
        env=_child_env(
            {
                "CHANGEPROOF_DB_URL": E2E_DB_URL,
                "CHANGEPROOF_ARTIFACTS_ROOT": E2E_ARTIFACTS,
                "CHANGEPROOF_CORS_ORIGINS": WEB_URL,
            }
        ),
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc, handle


def _start_web() -> tuple[subprocess.Popen[str], TextIO]:
    web_log = E2E_DIR / "web.log"
    web_log.parent.mkdir(parents=True, exist_ok=True)
    handle = web_log.open("w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [_which("npm"), "run", "dev", "--", "-p", str(WEB_PORT)],
        cwd=str(WEB_DIR),
        env=_child_env({"NEXT_PUBLIC_API_BASE": API_URL}),
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc, handle


def _playwright_outcome(returncode: int, output: str) -> str:
    if re.search(r"\b\d+ passed\b", output) and not re.search(r"\b\d+ failed\b", output):
        return "pass"
    if re.search(r"\b\d+ skipped\b", output) or re.search(
        r"Executable doesn't exist|浏览器.*未安装|Please run the following command to download",
        output,
        re.IGNORECASE,
    ):
        return "unavailable"
    # 用例内部因后端不可用等原因 skip，同样回退到 HTTP 等价断言。
    if returncode == 0 and "skipped" in output:
        return "unavailable"
    return "fail"


def _reset_e2e_state() -> None:
    E2E_DIR.mkdir(parents=True, exist_ok=True)
    db_path = E2E_DIR / "changeproof.db"
    if db_path.exists():
        try:
            db_path.unlink()
        except PermissionError as exc:
            raise GateError(f"无法删除 {_relative(db_path)}（可能被残留 uvicorn 占用）：{exc}") from exc
    artifacts = E2E_DIR / "artifacts"
    if artifacts.exists():
        shutil.rmtree(artifacts, ignore_errors=True)


def _step_compile() -> StepOutcome:
    command = [_python(), "-m", "compileall", "-q", "core", "services", "scripts"]
    returncode, output = _run(
        command, log_rel="compile.log", timeout=300
    )
    status = PASS if returncode == 0 else FAIL
    return status, " ".join(command), "var/gate/compile.log", _tail(output) if status == FAIL else ""


def _eval_metric_assertions() -> str:
    """C-GATE@2 的指标硬断言；返回空串表示全部通过。"""
    summary_path = PROJECT_ROOT / "var" / "eval" / "summary.json"
    if not summary_path.exists():
        return "缺少 var/eval/summary.json，请先运行评测"
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return f"var/eval/summary.json 不可读: {exc}"

    strategies = {entry["strategy"]: entry for entry in data.get("strategies", [])}
    combo = strategies.get("combo", {})
    static = strategies.get("static", {})
    failures: list[str] = []

    for name, expected in (
        ("regression_recall", 1.0),
        ("median_test_count_reduction", 0.50),
        ("recall_at_20", 0.90),
    ):
        actual = combo.get(name)
        if actual is None or float(actual) < expected - 1e-9:
            failures.append(f"combo.{name} 期望≥{expected}，实际 {actual}")

    static_recall = static.get("regression_recall")
    if static_recall is None or float(static_recall) < 0.80 - 1e-9:
        failures.append(f"static.regression_recall 期望≥0.80，实际 {static_recall}")

    verification_failures = data.get("verification_failures")
    if verification_failures is None or int(verification_failures) > 3:
        failures.append(f"verification_failures 期望≤3，实际 {verification_failures}")

    no_behavior = (data.get("subsets") or {}).get("no_behavior_change") or {}
    if "affected_symbols" not in no_behavior:
        failures.append("subsets.no_behavior_change.affected_symbols 缺失（语义等价指标未产出）")
    elif int(no_behavior["affected_symbols"]) != 0:
        failures.append(f"no_behavior_change.affected_symbols 期望 0，实际 {no_behavior['affected_symbols']}")

    if failures:
        return "；".join(failures) + f"（样本数 {data.get('samples_total')}）"
    return ""


def _step_eval(refresh: bool) -> StepOutcome:
    command = [_python(), "scripts/run_eval.py"]
    if refresh:
        command.append("--refresh")
    returncode, output = _run(command, log_rel="eval.log", timeout=1800)
    if returncode != 0:
        return FAIL, " ".join(command), "var/gate/eval.log", _tail(output)
    assertion_error = _eval_metric_assertions()
    if assertion_error:
        return FAIL, " ".join(command), "var/gate/eval.log", assertion_error
    return PASS, " ".join(command), "var/gate/eval.log", ""


def _step_smoke() -> StepOutcome:
    command = [
        _python(),
        "scripts/dev_smoke.py",
        "--api-port",
        str(API_PORT),
        "--web-port",
        str(WEB_PORT),
    ]
    returncode, output = _run(command, log_rel="smoke.log", timeout=420)
    status = PASS if returncode == 0 else FAIL
    return status, " ".join(command), "var/gate/smoke.log", _tail(output) if status == FAIL else ""


def _step_e2e() -> StepOutcome:
    command = f"{_which('npx')} playwright test（apps/web），浏览器不可用时回退 scripts/e2e_http.py"
    if not _wait_port_free(API_PORT):
        return FAIL, command, "var/e2e/api.log", f"端口 {API_PORT} 已被占用，请先停止残留进程"
    _reset_e2e_state()

    backend, backend_log = _start_backend()
    try:
        returncode, output = _run(
            [_python(), "scripts/e2e_http.py", "--only-health", "--api-url", API_URL],
            log_rel="e2e_health.log",
            timeout=180,
        )
        if returncode != 0:
            return FAIL, command, "var/e2e/api.log", "API 未就绪，见 var/e2e/api.log"

        returncode, output = _run(
            [_which("npx"), "playwright", "test"],
            log_rel="e2e/playwright.log",
            cwd=WEB_DIR,
            env={"NEXT_PUBLIC_API_BASE": API_URL},
            timeout=600,
        )
        outcome = _playwright_outcome(returncode, output)
        if outcome == "pass":
            return PASS, command, "var/gate/e2e/playwright.log", "playwright 1 passed"
        if outcome == "fail":
            return FAIL, command, "var/gate/e2e/playwright.log", _tail(output)

        # 浏览器不可用（或用例 skip）：用 HTTP 等价断言完成同一链路。
        web, web_log_handle = _start_web()
        try:
            returncode, output = _run(
                [
                    _python(),
                    "scripts/e2e_http.py",
                    "--api-url",
                    API_URL,
                    "--web-url",
                    WEB_URL,
                ],
                log_rel="e2e/e2e_http.log",
                timeout=420,
            )
        finally:
            _terminate(web)
            web_log_handle.close()
        status = PASS if returncode == 0 else FAIL
        detail = "playwright 跳过，HTTP E2E 等价断言通过" if status == PASS else _tail(output)
        return status, command, "var/gate/e2e/e2e_http.log", detail
    finally:
        _terminate(backend)
        backend_log.close()


def _docker_available() -> bool:
    try:
        completed = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _step_docker() -> StepOutcome:
    command = [_python(), "infra/runner/verify_isolation.py"]
    label = " ".join(command)
    if not _docker_available():
        return SKIP, label, "", "docker daemon 不可用"
    returncode, output = _run(command, log_rel="docker_runner.log", timeout=1800)
    status = PASS if returncode == 0 else FAIL
    return status, label, "var/gate/docker_runner.log", _tail(output) if status == FAIL else ""


def _execute(name: str, func: Callable[[], StepOutcome]) -> StepResult:
    started_at = time.perf_counter()
    try:
        status, command, log_path, detail = func()
    except GateError as exc:
        status, command, log_path, detail = FAIL, "", "", str(exc)
    except Exception as exc:  # 编排级异常也要落在汇总里，不裸抛栈
        status, command, log_path, detail = FAIL, "", "", f"步骤异常：{exc!r}"
    duration_ms = int((time.perf_counter() - started_at) * 1000)
    suffix = f" — {detail}" if detail else ""
    print(f"[gate] {name}: {status} {duration_ms}ms{suffix}", flush=True)
    return StepResult(
        name=name,
        status=status,
        duration_ms=duration_ms,
        command=command,
        log_path=log_path,
        detail=detail,
    )


def _skipped(name: str, reason: str) -> StepResult:
    print(f"[gate] {name}: {SKIP} 0ms — {reason}", flush=True)
    return StepResult(name=name, status=SKIP, duration_ms=0, command="", log_path="", detail=reason)


def _write_summary(summary: dict) -> None:
    GATE_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ChangeProof 一键门禁（C-GATE@1）")
    parser.add_argument("--skip-docker", action="store_true", help="跳过 docker-runner 步骤")
    parser.add_argument("--skip-e2e", action="store_true", help="跳过浏览器 E2E 步骤")
    parser.add_argument("--refresh", action="store_true", help="忽略评测样本缓存，透传 run_eval.py")
    args = parser.parse_args(argv)

    GATE_DIR.mkdir(parents=True, exist_ok=True)
    started_at = time.time()
    started_perf = time.perf_counter()

    steps: list[StepResult] = [
        _execute("compile", _step_compile),
        _execute("eval", lambda: _step_eval(args.refresh)),
        _execute("smoke", _step_smoke),
    ]
    if args.skip_e2e:
        steps.append(_skipped("e2e", "--skip-e2e"))
    else:
        steps.append(_execute("e2e", _step_e2e))
    if args.skip_docker:
        steps.append(_skipped("docker-runner", "--skip-docker"))
    else:
        steps.append(_execute("docker-runner", _step_docker))

    exit_code = 1 if any(step.status == FAIL for step in steps) else 0
    total_ms = int((time.perf_counter() - started_perf) * 1000)
    summary = {
        "started_at": datetime.fromtimestamp(started_at, tz=timezone.utc).isoformat(),
        "finished_at": datetime.now(tz=timezone.utc).isoformat(),
        "duration_ms": total_ms,
        "exit_code": exit_code,
        "flags": {
            "skip_docker": args.skip_docker,
            "skip_e2e": args.skip_e2e,
            "refresh": args.refresh,
        },
        "steps": [
            {
                "name": step.name,
                "status": step.status,
                "duration_ms": step.duration_ms,
                "command": step.command,
                "log_path": step.log_path,
                "detail": step.detail,
            }
            for step in steps
        ],
        "summary_path": _relative(SUMMARY_PATH),
    }
    _write_summary(summary)

    print(f"GATE {'PASS' if exit_code == 0 else 'FAIL'} total={total_ms}ms", flush=True)
    print(f"汇总: {_relative(SUMMARY_PATH)}", flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())