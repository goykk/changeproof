"""浏览器不可用时的端到端等价门禁。

走与 `apps/web/e2e/analysis.spec.ts` 相同的链路（导入仓库 → 创建 changeset → 触发分析 →
轮询到 reported → 拉取报告），再以 httpx 断言三个页面的关键文案，输出 `HTTP E2E PASS|FAIL`。
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = PROJECT_ROOT / "eval" / "samples" / "lock.json"
SAMPLE_LOCAL_PATH = "var/samples/shop_api"
PREFERRED_SAMPLE_ID = "shop_api_rename_q1"
POLL_INTERVAL_SECONDS = 2.0
TERMINAL_STATUSES = {"reported", "failed"}


class HttpE2eError(RuntimeError):
    """门禁断言失败。"""


def _log(step: str, detail: str, started_at: float) -> None:
    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    print(f"[{step}] {detail} ok {elapsed_ms}ms", flush=True)


def load_sample_refs() -> tuple[str, str]:
    if not LOCK_PATH.exists():
        raise HttpE2eError(f"缺少 {LOCK_PATH}，请先执行 make sample")
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    group = lock.get("shop_api") or {}
    ref = group.get(PREFERRED_SAMPLE_ID) or next(iter(group.values()), None)
    if not ref:
        raise HttpE2eError(f"{LOCK_PATH} 中没有 shop_api 样本 sha")
    return str(ref["base_sha"]), str(ref["target_sha"])


def _json_or_fail(response: httpx.Response, action: str) -> dict[str, Any]:
    if response.status_code >= 400:
        body: Any
        try:
            body = response.json()
        except ValueError:
            body = response.text
        code = body.get("code") if isinstance(body, dict) else response.status_code
        message = body.get("message") if isinstance(body, dict) else str(body)
        raise HttpE2eError(f"{action} 失败：{response.status_code} {code} {message}")
    return response.json()


def wait_for_api(client: httpx.Client, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error = "未收到响应"
    while time.monotonic() < deadline:
        try:
            response = client.get("/api/health")
            if response.status_code == 200 and response.json().get("status") == "ok":
                return
            last_error = f"status={response.status_code}"
        except httpx.HTTPError as exc:
            last_error = str(exc)
        time.sleep(POLL_INTERVAL_SECONDS)
    raise HttpE2eError(f"API 未在 {timeout:.0f}s 内就绪（{last_error}）")


def wait_for_web(client: httpx.Client, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error = "未收到响应"
    while time.monotonic() < deadline:
        try:
            response = client.get("/")
            if response.status_code == 200:
                return
            last_error = f"status={response.status_code}"
        except httpx.HTTPError as exc:
            last_error = str(exc)
        time.sleep(POLL_INTERVAL_SECONDS)
    raise HttpE2eError(f"前端未在 {timeout:.0f}s 内就绪（{last_error}）")


def create_repository(api: httpx.Client) -> str:
    if not (PROJECT_ROOT / SAMPLE_LOCAL_PATH).exists():
        raise HttpE2eError(f"缺少样例仓库 {SAMPLE_LOCAL_PATH}，请先执行 make sample")
    response = api.post("/api/repos", json={"name": "shop_api", "local_path": SAMPLE_LOCAL_PATH})
    return str(_json_or_fail(response, "POST /api/repos")["repo_id"])


def create_changeset(api: httpx.Client, repo_id: str, base_sha: str, target_sha: str) -> str:
    response = api.post(
        "/api/changesets",
        json={"repo_id": repo_id, "base_sha": base_sha, "target_sha": target_sha},
    )
    return str(_json_or_fail(response, "POST /api/changesets")["changeset_id"])


def start_analysis(api: httpx.Client, changeset_id: str) -> str:
    response = api.post(f"/api/changesets/{changeset_id}/analyze", json={"generate_tests": False})
    return str(_json_or_fail(response, "POST /api/changesets/{id}/analyze")["task_id"])


def wait_for_task(api: httpx.Client, task_id: str, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = _json_or_fail(api.get(f"/api/tasks/{task_id}"), "GET /api/tasks/{id}")
        if last.get("status") in TERMINAL_STATUSES:
            return last
        time.sleep(POLL_INTERVAL_SECONDS)
    raise HttpE2eError(f"任务 {task_id} 未在 {timeout:.0f}s 内结束（最后状态 {last.get('status')}）")


def fetch_report(api: httpx.Client, changeset_id: str) -> dict[str, Any]:
    report = _json_or_fail(api.get(f"/api/changesets/{changeset_id}/report"), "GET /changesets/{id}/report")
    summary = report.get("summary")
    if not isinstance(summary, dict) or "changed_symbols" not in summary:
        raise HttpE2eError("报告缺少 summary 字段")
    if "residual_risks" not in report:
        raise HttpE2eError("报告缺少 residual_risks 字段")
    return report


def assert_html(client: httpx.Client, url: str, key_texts: tuple[str, ...]) -> None:
    response = client.get(url)
    if response.status_code != 200:
        raise HttpE2eError(f"{url} 返回 {response.status_code}")
    missing = [text for text in key_texts if text not in response.text]
    if missing:
        raise HttpE2eError(f"{url} 缺少关键文案：{missing}")


def run(args: argparse.Namespace) -> None:
    with httpx.Client(base_url=args.api_url.rstrip("/"), timeout=60.0) as api:
        started_at = time.monotonic()
        wait_for_api(api, min(args.timeout, 120.0))
        _log("1/8 health", "API 就绪", started_at)
        if args.only_health:
            print("API READY", flush=True)
            return

        started_at = time.monotonic()
        base_sha, target_sha = load_sample_refs()
        _log("2/8 lock", f"base={base_sha[:8]} target={target_sha[:8]}", started_at)

        started_at = time.monotonic()
        repo_id = create_repository(api)
        _log("3/8 repos", f"repo_id={repo_id}", started_at)

        started_at = time.monotonic()
        changeset_id = create_changeset(api, repo_id, base_sha, target_sha)
        _log("4/8 changesets", f"changeset_id={changeset_id}", started_at)

        started_at = time.monotonic()
        task_id = start_analysis(api, changeset_id)
        _log("5/8 analyze", f"task_id={task_id}", started_at)

        started_at = time.monotonic()
        task = wait_for_task(api, task_id, args.timeout)
        if task.get("status") != "reported":
            raise HttpE2eError(f"任务终态为 {task.get('status')}，期望 reported（error={task.get('error_code')}）")
        _log("6/8 reported", f"stage={task.get('stage')}", started_at)

        started_at = time.monotonic()
        report = fetch_report(api, changeset_id)
        _log("7/8 report", f"changed_symbols={report['summary']['changed_symbols']}", started_at)

    with httpx.Client(base_url=args.web_url.rstrip("/"), timeout=60.0) as web:
        started_at = time.monotonic()
        wait_for_web(web, args.timeout)
        assert_html(web, "/", ("仓库与变更", "导入仓库"))
        assert_html(web, f"/changesets/{changeset_id}", ("变更影响详情", changeset_id))
        assert_html(web, f"/changesets/{changeset_id}/report", ("证据报告", changeset_id))
        _log("8/8 web", "首页 / 影响页 / 报告页文案断言通过", started_at)


def main() -> int:
    parser = argparse.ArgumentParser(description="ChangeProof HTTP 端到端等价门禁")
    parser.add_argument("--api-url", default="http://127.0.0.1:8100", help="后端地址")
    parser.add_argument("--web-url", default="http://127.0.0.1:3100", help="前端地址")
    parser.add_argument("--timeout", type=float, default=300.0, help="整体等待上限（秒）")
    parser.add_argument("--only-health", action="store_true", help="只等待 API 健康检查，供 make e2e 前置使用")
    args = parser.parse_args()

    started_at = time.monotonic()
    try:
        run(args)
    except (HttpE2eError, httpx.HTTPError, OSError, ValueError) as exc:
        print(f"HTTP E2E FAIL: {exc}", flush=True)
        return 1
    if not args.only_health:
        print(f"HTTP E2E PASS total={int((time.monotonic() - started_at) * 1000)}ms", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())