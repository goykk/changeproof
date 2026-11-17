"""联调冒烟（C-DEVUP@1）。

以真实子进程拉起 ``uvicorn`` 与 Next.js 开发服务器，顺序走完一次完整分析：
导入仓库 → 创建 changeset → 触发分析 → 轮询任务 → 读取报告 → Web 根路径 200。
结束时恒终止全部子进程；日志与最后一次任务 id 留在 ``var/smoke/``。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]

SAMPLE_MARKER = "var/samples/shop_api"
LOCK_PATH = "eval/samples/lock.json"
SMOKE_ROOT = "var/smoke"
SMOKE_DB_URL = "sqlite:///./var/smoke/changeproof.db"
SMOKE_ARTIFACTS = "var/smoke/artifacts"
TERMINAL_STATUSES = frozenset({"reported", "failed"})


class SmokeError(RuntimeError):
    """单个冒烟步骤失败，携带可直接展示的中文原因。"""


@dataclass
class Step:
    name: str
    status: str
    elapsed_ms: int
    detail: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _summarize(value: Any) -> str:
    if isinstance(value, dict):
        keys = ("repo_id", "changeset_id", "task_id", "status")
        return " ".join(f"{key}={value[key]}" for key in keys if key in value)
    if isinstance(value, (str, int, float)):
        return str(value)
    return ""


def _run_step(steps: list[Step], name: str, fn: Any) -> Any:
    started = time.perf_counter()
    try:
        value = fn()
    except SmokeError as exc:
        steps.append(Step(name, "fail", int((time.perf_counter() - started) * 1000), str(exc)))
        raise
    steps.append(
        Step(name, "ok", int((time.perf_counter() - started) * 1000), _summarize(value))
    )
    return value


def _load_locked_refs() -> tuple[str, str, str]:
    """返回 ``(repo_name, base_sha, target_sha)``；sha 只从 lock.json 第一组读取。"""
    path = PROJECT_ROOT / LOCK_PATH
    if not path.is_file():
        raise SmokeError(f"缺少 {LOCK_PATH}，先运行 python scripts/bootstrap_samples.py")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SmokeError(f"无法解析 {LOCK_PATH}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SmokeError(f"{LOCK_PATH} 结构不符合契约")
    for repo_name, refs in payload.items():
        if not isinstance(refs, dict):
            continue
        base_sha = refs.get("base_sha")
        target_sha = refs.get("target_sha")
        if isinstance(base_sha, str) and isinstance(target_sha, str) and base_sha and target_sha:
            return str(repo_name), base_sha, target_sha
        # C-SAMPLE-V2@1: {repo: {sample_id: {base_sha, target_sha}}}
        for _sample_id, entry in refs.items():
            if not isinstance(entry, dict):
                continue
            base_sha = entry.get("base_sha")
            target_sha = entry.get("target_sha")
            if isinstance(base_sha, str) and isinstance(target_sha, str) and base_sha and target_sha:
                return str(repo_name), base_sha, target_sha
    raise SmokeError(f"{LOCK_PATH} 中没有可用的 base_sha/target_sha")


def _reset_state() -> str:
    """每次冒烟从空库开始，避免历史任务让步骤变成空跑。"""
    db_path = PROJECT_ROOT / "var/smoke/changeproof.db"
    for suffix in ("", "-wal", "-shm"):
        candidate = db_path.with_name(db_path.name + suffix)
        try:
            candidate.unlink()
        except FileNotFoundError:
            continue
        except PermissionError as exc:
            raise SmokeError(f"无法清理 {candidate}（可能被旧进程占用）: {exc}") from exc
    return "已重置 var/smoke/changeproof.db"


def _api_env(web_port: int) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["CHANGEPROOF_DB_URL"] = SMOKE_DB_URL
    env["CHANGEPROOF_ARTIFACTS_ROOT"] = SMOKE_ARTIFACTS
    env["CHANGEPROOF_CORS_ORIGINS"] = (
        f"http://localhost:{web_port},http://127.0.0.1:{web_port}"
    )
    return env


def _web_env(api_port: int, web_port: int) -> dict[str, str]:
    env = os.environ.copy()
    env["NEXT_PUBLIC_API_BASE"] = f"http://127.0.0.1:{api_port}"
    env["PORT"] = str(web_port)
    return env


def _npm_command(args: list[str]) -> list[str]:
    npm = shutil.which("npm")
    if npm is None:
        raise SmokeError("未找到 npm，请安装 Node.js 并在 PATH 中暴露 npm")
    if os.name == "nt":
        # .cmd 无法被 CreateProcess 直接执行，统一经 cmd /c 启动
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        return [comspec, "/c", subprocess.list2cmdline([npm, *args])]
    return [npm, *args]


def _call(client: httpx.Client, method: str, url: str, **kwargs: Any) -> httpx.Response:
    try:
        return client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise SmokeError(f"{method} {url} 请求失败: {exc}") from exc


def _ensure(
    response: httpx.Response, allowed: set[int], what: str
) -> dict[str, Any]:
    if response.status_code not in allowed:
        raise SmokeError(f"{what} 返回 HTTP {response.status_code}: {response.text[:300]}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise SmokeError(f"{what} 响应不是 JSON: {response.text[:300]}") from exc
    if not isinstance(payload, dict):
        raise SmokeError(f"{what} 响应不是 JSON 对象")
    return payload


class ProcessSupervisor:
    """统一管理冒烟期间拉起的子进程，保证任何退出路径都不留残留进程。"""

    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd
        self._children: list[tuple[str, subprocess.Popen[str], TextIO]] = []

    def spawn(
        self,
        name: str,
        command: list[str],
        log_path: Path,
        env: dict[str, str] | None = None,
    ) -> str:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handle = log_path.open("w", encoding="utf-8", newline="\n")
        handle.write("$ " + " ".join(command) + "\n")
        handle.flush()
        kwargs: dict[str, Any] = {}
        if os.name != "nt":
            kwargs["start_new_session"] = True
        try:
            process = subprocess.Popen(
                command,
                cwd=str(self._cwd),
                env=env,
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
                **kwargs,
            )
        except OSError as exc:
            handle.close()
            raise SmokeError(f"无法启动 {name}: {exc}") from exc
        self._children.append((name, process, handle))
        return f"pid={process.pid} log={log_path.name}"

    def alive(self, name: str) -> bool:
        for child_name, process, _ in self._children:
            if child_name == name:
                return process.poll() is None
        return False

    def close(self) -> None:
        for _, process, handle in reversed(self._children):
            _terminate(process)
            handle.close()
        self._children.clear()


def _terminate(process: subprocess.Popen[str], timeout: float = 10.0) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def _wait_api(
    client: httpx.Client, supervisor: ProcessSupervisor, deadline: float
) -> str:
    last_error = "无响应"
    while time.monotonic() < deadline:
        if not supervisor.alive("api"):
            raise SmokeError("uvicorn 进程提前退出，见 var/smoke/api.log")
        try:
            response = client.get("/api/health", timeout=5.0)
        except httpx.HTTPError as exc:
            last_error = str(exc)
        else:
            if response.status_code == 200:
                payload = response.json()
                if payload.get("status") == "ok":
                    return (
                        f"isolation_mode={payload.get('isolation_mode')} "
                        f"llm={payload.get('llm')}"
                    )
                last_error = f"status={payload.get('status')}"
            else:
                last_error = f"HTTP {response.status_code}"
        time.sleep(0.5)
    raise SmokeError(f"API 健康检查超时: {last_error}")


def _wait_task(
    client: httpx.Client, task_id: str, deadline: float
) -> dict[str, Any]:
    last_status = "unknown"
    while time.monotonic() < deadline:
        payload = _ensure(
            _call(client, "GET", f"/api/tasks/{task_id}"), {200}, "GET /api/tasks/{task_id}"
        )
        last_status = str(payload.get("status", "unknown"))
        if last_status in TERMINAL_STATUSES:
            return payload
        time.sleep(1.0)
    raise SmokeError(f"任务 {task_id} 轮询超时，最后状态 {last_status}")


def _wait_web(supervisor: ProcessSupervisor, port: int, deadline: float) -> str:
    url = f"http://127.0.0.1:{port}/"
    last_error = "无响应"
    with httpx.Client(timeout=15.0, follow_redirects=True) as client:
        while time.monotonic() < deadline:
            if not supervisor.alive("web"):
                raise SmokeError("npm dev 进程提前退出，见 var/smoke/web.log")
            try:
                response = client.get(url)
            except httpx.HTTPError as exc:
                last_error = str(exc)
            else:
                if response.status_code == 200:
                    return f"GET {url} → 200"
                last_error = f"HTTP {response.status_code}"
            time.sleep(1.0)
    raise SmokeError(f"Web 根路径 200 超时: {last_error}")


def _write_state(
    smoke_dir: Path,
    result: dict[str, Any],
    steps: list[Step],
    ok: bool,
    error: str,
    total_ms: int,
) -> None:
    payload = {
        "status": "pass" if ok else "fail",
        "generated_at": _now_iso(),
        "total_ms": total_ms,
        "error": error or None,
        "steps": [asdict(step) for step in steps],
        **result,
    }
    path = smoke_dir / "last_task.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _print_steps(steps: list[Step]) -> None:
    total = len(steps)
    for index, step in enumerate(steps, start=1):
        print(
            f"[{index}/{total}] {step.name:<20} {step.status:<4} {step.elapsed_ms:>6} ms  {step.detail}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ChangeProof 联调冒烟（真实 uvicorn + Next.js 进程）"
    )
    parser.add_argument(
        "--api-port", type=int, default=8100, help="API 端口（默认 8100，避开 8000）"
    )
    parser.add_argument(
        "--web-port", type=int, default=3100, help="Web 端口（默认 3100，避开 3000）"
    )
    parser.add_argument("--timeout", type=float, default=300.0, help="整体超时秒数")
    args = parser.parse_args(argv)

    started = time.perf_counter()
    deadline = time.monotonic() + args.timeout
    smoke_dir = PROJECT_ROOT / SMOKE_ROOT
    smoke_dir.mkdir(parents=True, exist_ok=True)

    steps: list[Step] = []
    result: dict[str, Any] = {"api_port": args.api_port, "web_port": args.web_port}
    error = ""
    ok = False
    supervisor = ProcessSupervisor(PROJECT_ROOT)
    total_ms = 0

    try:
        repo_path = PROJECT_ROOT / SAMPLE_MARKER
        if not repo_path.is_dir():
            raise SmokeError(
                f"缺少样例仓库 {SAMPLE_MARKER}，先运行 python scripts/bootstrap_samples.py"
            )
        repo_name, base_sha, target_sha = _run_step(
            steps, "load_sample_refs", _load_locked_refs
        )
        _run_step(steps, "reset_state", _reset_state)
        _run_step(
            steps,
            "start_api",
            lambda: supervisor.spawn(
                "api",
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "services.api.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(args.api_port),
                ],
                smoke_dir / "api.log",
                env=_api_env(args.web_port),
            ),
        )

        with httpx.Client(base_url=f"http://127.0.0.1:{args.api_port}", timeout=60.0) as client:
            _run_step(steps, "api_health", lambda: _wait_api(client, supervisor, deadline))

            repo = _run_step(
                steps,
                "import_repo",
                lambda: _ensure(
                    _call(
                        client,
                        "POST",
                        "/api/repos",
                        json={"name": repo_name, "local_path": str(repo_path)},
                    ),
                    {200, 201},
                    "POST /api/repos",
                ),
            )
            result["repo_id"] = repo["repo_id"]

            changeset = _run_step(
                steps,
                "create_changeset",
                lambda: _ensure(
                    _call(
                        client,
                        "POST",
                        "/api/changesets",
                        json={
                            "repo_id": repo["repo_id"],
                            "base_sha": base_sha,
                            "target_sha": target_sha,
                        },
                    ),
                    {200, 201},
                    "POST /api/changesets",
                ),
            )
            changeset_id = changeset["changeset_id"]
            result["changeset_id"] = changeset_id

            task = _run_step(
                steps,
                "start_analysis",
                lambda: _ensure(
                    _call(
                        client,
                        "POST",
                        f"/api/changesets/{changeset_id}/analyze",
                        json={"generate_tests": False},
                    ),
                    {200, 202},
                    "POST /api/changesets/{id}/analyze",
                ),
            )
            task_id = task["task_id"]
            result["task_id"] = task_id

            final_task = _run_step(
                steps, "wait_task", lambda: _wait_task(client, task_id, deadline)
            )
            if final_task.get("status") != "reported":
                raise SmokeError(
                    f"任务 {task_id} 结束状态 {final_task.get('status')}: "
                    f"{final_task.get('error_code')} {final_task.get('error_message')}"
                )

            report = _run_step(
                steps,
                "read_report",
                lambda: _ensure(
                    _call(client, "GET", f"/api/changesets/{changeset_id}/report"),
                    {200},
                    "GET /api/changesets/{id}/report",
                ),
            )
            result["report_summary"] = report.get("summary")

        _run_step(
            steps,
            "start_web",
            lambda: supervisor.spawn(
                "web",
                _npm_command(
                    ["--prefix", "apps/web", "run", "dev", "--", "-p", str(args.web_port)]
                ),
                smoke_dir / "web.log",
                env=_web_env(args.api_port, args.web_port),
            ),
        )
        _run_step(steps, "web_root", lambda: _wait_web(supervisor, args.web_port, deadline))
        ok = True
    except SmokeError as exc:
        error = str(exc)
    finally:
        supervisor.close()
        total_ms = int((time.perf_counter() - started) * 1000)
        _write_state(smoke_dir, result, steps, ok, error, total_ms)

    _print_steps(steps)
    if ok:
        print(
            f"SMOKE PASS total={total_ms}ms repo_id={result.get('repo_id')} "
            f"changeset_id={result.get('changeset_id')} task_id={result.get('task_id')}"
        )
        print("日志与状态: var/smoke/")
        return 0

    print(f"SMOKE FAIL total={total_ms}ms task_id={result.get('task_id') or '-'}")
    print(f"原因: {error}")
    print("日志: var/smoke/api.log var/smoke/web.log；状态: var/smoke/last_task.json")
    return 1


if __name__ == "__main__":
    sys.exit(main())