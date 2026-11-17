"""testmon 参考基线对照（P1 多策略基准）。

对每个样本在同一工作副本中：checkout base → 全量建 testmon 基线 → checkout target →
`pytest --testmon` 只跑受影响测试，记录执行集合并计算召回与降幅，输出
`var/eval_testmon/summary.json`，并与 `var/eval/summary.json` 的 combo 指标对照。
"""

from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / "var" / "samples" / "shop_api"
OUT = ROOT / "var" / "eval_testmon"


def git(*args: str) -> None:
    subprocess.run(["git", *args], cwd=REPO, check=True, capture_output=True, text=True)


def executed(junit: Path) -> list[str]:
    if not junit.exists():
        return []
    root = ET.parse(junit).getroot()
    tests = []
    for case in root.iter("testcase"):
        module = (case.get("classname") or "").replace(".", "/")
        tests.append(f"{module}.py::{case.get('name')}")
    return sorted(tests)


def pytest(junit: Path, extra: list[str]) -> None:
    junit.parent.mkdir(parents=True, exist_ok=True)
    if junit.exists():
        junit.unlink()
    subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests", *extra, f"--junitxml={junit}"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )


def main() -> int:
    lock = json.loads((ROOT / "eval" / "samples" / "lock.json").read_text(encoding="utf-8"))["shop_api"]
    combo = json.loads((ROOT / "var" / "eval" / "combo.json").read_text(encoding="utf-8"))
    totals = {item["sample_id"]: item["total_count"] for item in combo["per_sample"]}
    OUT.mkdir(parents=True, exist_ok=True)

    per_sample = []
    for path in sorted((ROOT / "eval" / "samples").glob("*.json")):
        if path.name == "lock.json":
            continue
        sample = json.loads(path.read_text(encoding="utf-8"))
        sid = sample["sample_id"]
        sha = lock[sid]
        expected = list(sample.get("expected_failing_tests") or [])
        work = OUT / "_work" / sid
        junit_base = work / "base.xml"
        junit_target = work / "target.xml"
        try:
            git("checkout", "-f", "main")
            git("checkout", "-f", sha["base_sha"])
            for stale in REPO.rglob(".testmondata*"):
                stale.unlink(missing_ok=True)
            pytest(junit_base, ["--testmon"])
            git("checkout", "-f", sha["target_sha"])
            pytest(junit_target, ["--testmon"])
            selected = executed(junit_target)
            total = int(totals.get(sid) or 0)
            hit = len(set(expected) & set(selected))
            recall = 1.0 if not expected else hit / len(expected)
            reduction = (1.0 - len(selected) / total) if total else None
            per_sample.append(
                {
                    "sample_id": sid,
                    "selected_count": len(selected),
                    "total_count": total,
                    "test_count_reduction": reduction,
                    "expected_count": len(expected),
                    "recall": recall,
                    "selected_tests": selected,
                }
            )
            print(f"{sid}: selected={len(selected)}/{total} recall={recall:.2f}")
        finally:
            git("checkout", "-f", "main")
    git("checkout", "-f", "main")

    recalls = [item["recall"] for item in per_sample]
    reductions = [item["test_count_reduction"] for item in per_sample if item["test_count_reduction"] is not None]
    reductions.sort()
    median = reductions[len(reductions) // 2] if reductions else 0.0
    summary = {
        "tool": "pytest-testmon",
        "samples_total": len(per_sample),
        "regression_recall": sum(recalls) / len(recalls) if recalls else 0.0,
        "median_test_count_reduction": median,
        "selected_total": sum(item["selected_count"] for item in per_sample),
        "per_sample": per_sample,
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    ours = next(item for item in json.loads((ROOT / "var" / "eval" / "summary.json").read_text(encoding="utf-8"))["strategies"] if item["strategy"] == "combo")
    print("\n对照：")
    print(f"  testmon: recall={summary['regression_recall']:.3f} 降幅={summary['median_test_count_reduction']:.3f} 选测总数={summary['selected_total']}")
    print(f"  combo  : recall={ours['regression_recall']:.3f} 降幅={ours['median_test_count_reduction']:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())