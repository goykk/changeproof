"""pytest 命令构造与摘要解析。"""

from __future__ import annotations

import re
from collections.abc import Sequence

_SUMMARY_TIME = re.compile(r"\bin\s+\d+(?:\.\d+)?s\b")
_COUNT = re.compile(r"(\d+)\s+(passed|failed|errors?|skipped|deselected)\b")
_COLLECTED = re.compile(r"collected\s+(\d+)\s+items?\b")

_COUNT_KEYS = ("passed", "failed", "error", "skipped")


def parse_pytest_summary(log_text: str) -> dict[str, int]:
    """解析 pytest 输出末尾摘要；解析失败返回全 0。"""
    result = {key: 0 for key in (*_COUNT_KEYS, "selected")}
    if not log_text:
        return result

    lines = [line.strip() for line in log_text.splitlines() if line.strip()]
    summary_line = ""
    for line in reversed(lines):
        if _SUMMARY_TIME.search(line):
            summary_line = line
            break
    if not summary_line:
        return result

    counts: dict[str, int] = {}
    for number, name in _COUNT.findall(summary_line):
        key = "error" if name.startswith("error") else name
        counts[key] = counts.get(key, 0) + int(number)

    for key in _COUNT_KEYS:
        result[key] = counts.get(key, 0)

    collected_match = _COLLECTED.search(log_text)
    if collected_match:
        collected = int(collected_match.group(1))
    else:
        collected = sum(result[key] for key in _COUNT_KEYS)
    result["selected"] = max(0, collected - counts.get("deselected", 0))
    return result


def build_pytest_command(
    test_node_ids: Sequence[str],
    coverage_json_path: str | None,
) -> list[str]:
    command = ["python", "-m", "pytest", "-p", "no:cacheprovider", "-q"]
    command.extend(str(node_id) for node_id in test_node_ids)
    if coverage_json_path:
        command.extend(
            ["--cov=.", "--cov-context=test", f"--cov-report=json:{coverage_json_path}"]
        )
    return command