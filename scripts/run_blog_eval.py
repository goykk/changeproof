"""第二示例仓库的泛化评测（复用主分析链路，输出 var/eval_blog/summary.json）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.evaluation.metrics import build_report  # noqa: E402
from core.evaluation.strategies import apply_strategy, rank_impact_files  # noqa: E402
from scripts.run_eval import _build_artifacts  # noqa: E402


def _export(repo: Path, ref: str, dest: Path) -> Path:
    import subprocess

    dest.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(["git", "archive", "--format=tar", ref], cwd=repo, capture_output=True, check=True)
    (dest / "repo.tar").write_bytes(archive.stdout)
    subprocess.run(["tar", "-xf", str(dest / "repo.tar"), "-C", str(dest)], check=True)
    return dest


def _failing(snapshot: Path) -> list[str]:
    import subprocess
    import xml.etree.ElementTree as ET

    junit = snapshot / "junit.xml"
    subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests", "--junitxml", str(junit)],
        cwd=snapshot,
        capture_output=True,
        text=True,
    )
    if not junit.exists():
        return ["<no-junit>"]
    root = ET.parse(junit).getroot()
    failures = []
    for case in root.iter("testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            module = (case.get("classname") or "").replace(".", "/")
            failures.append(f"{module}.py::{case.get('name')}")
    return sorted(failures)


def main() -> int:
    repo = ROOT / "var" / "samples" / "blog_api"
    lock = json.loads((ROOT / "eval" / "blog" / "lock.json").read_text(encoding="utf-8"))["blog_api"]
    samples = sorted((ROOT / "eval" / "blog").glob("*.json"))
    out_dir = ROOT / "var" / "eval_blog"
    out_dir.mkdir(parents=True, exist_ok=True)
    per_sample: list[dict] = []

    # 防自证：先核对真实失败集与标注一致，再计算指标（C-SECOND-REPO@1）。
    mismatches: list[str] = []
    for path in samples:
        if path.name == "lock.json":
            continue
        sample = json.loads(path.read_text(encoding="utf-8"))
        sid = sample["sample_id"]
        base_fail = _failing(_export(repo, "v1-baseline", out_dir / "_verify" / sid / "base"))
        target_fail = _failing(_export(repo, f"sample/{sid}", out_dir / "_verify" / sid / "target"))
        if base_fail:
            mismatches.append(f"{sid}: base 未全通过 {base_fail}")
        expected = set(sample["expected_failing_tests"])
        actual = set(target_fail)
        if expected != actual:
            mismatches.append(
                f"{sid}: 标注与实测不一致 标注外={sorted(actual - expected)} 未命中={sorted(expected - actual)}"
            )
    if mismatches:
        for message in mismatches:
            print("[annotation_mismatch]", message)
        return 1
    print(f"[ok] {len(samples) - 1} 组样本的 base 全通过、target 失败集与标注一致")

    for path in samples:
        if path.name == "lock.json":
            continue
        sample = json.loads(path.read_text(encoding="utf-8"))
        sid = sample["sample_id"]
        sha = lock[sid]
        artifacts = _build_artifacts(
            sample,
            repo,
            sha["base_sha"],
            sha["target_sha"],
            out_dir / "_work" / sid,
            True,
            coverage_per_test=True,
            with_generation=False,
            coverage_cache_dir=out_dir / "coverage_cache",
            coverage_cache_version="v1",
            coverage_memo={},
        )
        items = apply_strategy(
            "combo",
            changed=artifacts["changed"],
            impacts=artifacts["impacts"],
            symbols=artifacts["symbols"],
            tests=artifacts["tests"],
            coverage=artifacts["coverage"],
            high_risk_globs=sample.get("high_risk_globs") or (),
            edges=artifacts.get("edges") or (),
        )
        ranked = rank_impact_files(artifacts["changed"], artifacts["impacts"], artifacts["symbols"])
        per_sample.append(
            {
                "sample_id": sid,
                "selected_tests": [item.test_node_id for item in items],
                "selected_count": len(items),
                "total_count": len(artifacts["tests"]),
                "affected_symbols": len(artifacts["changed"]),
                "expected_no_behavior_change": sample["expected_no_behavior_change"],
                "recall_at_20": __import__("core.evaluation.metrics", fromlist=["recall_at_20"]).recall_at_20(
                    sample["expected_impact_files"], ranked
                ),
                "regression_recall": __import__("core.evaluation.metrics", fromlist=["regression_recall"]).regression_recall(
                    sample["expected_failing_tests"], [item.test_node_id for item in items]
                ),
            }
        )
    report = build_report("combo", per_sample)
    summary = {
        "generated_at": report.generated_at,
        "samples_total": report.samples_total,
        "strategies": [
            {
                "strategy": report.strategy,
                "samples_total": report.samples_total,
                "recall_at_20": report.recall_at_20,
                "regression_recall": report.regression_recall,
                "median_test_count_reduction": report.median_test_count_reduction,
                "verified_rate": report.verified_rate,
            }
        ],
        "per_sample": per_sample,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"combo: samples={report.samples_total} recall_at_20={report.recall_at_20:.3f} "
        f"regression_recall={report.regression_recall:.3f} reduction={report.median_test_count_reduction:.3f}"
    )
    for item in per_sample:
        print(f"  {item['sample_id']}: selected={item['selected_count']}/{item['total_count']} recall={item['regression_recall']:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())