"""评测指标口径（C-EVAL@1）。

指标只依赖样本标注与预测结果，不做任何 IO；空预期按「无缺失」返回 1.0，
空样本集按「无收益」返回 0.0，二者是评测口径的既定语义，调用方不得再兜底改写。
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

RECALL_TOP_K = 20


def _normalize_path(path: str) -> str:
    normalized = str(path).replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.strip("/")


def _dedupe(items: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        value = str(item)
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def recall_at_20(expected_files: Sequence[str], ranked_files: Sequence[str]) -> float:
    """前 20 个预测文件覆盖的预期文件比例；空预期返回 1.0。"""
    expected = _dedupe(expected_files)
    if not expected:
        return 1.0
    top_k = {_normalize_path(path) for path in list(ranked_files)[:RECALL_TOP_K]}
    hits = sum(1 for path in expected if _normalize_path(path) in top_k)
    return hits / len(expected)


def regression_recall(
    expected_failing_tests: Sequence[str],
    selected_tests: Sequence[str],
) -> float:
    """被选中的预期失败用例比例；空预期返回 1.0。"""
    expected = _dedupe(expected_failing_tests)
    if not expected:
        return 1.0
    selected = set(_dedupe(selected_tests))
    return sum(1 for node_id in expected if node_id in selected) / len(expected)


def median_test_count_reduction(
    selected_counts: Sequence[int],
    total_counts: Sequence[int],
) -> float:
    """逐样本 `1 - selected/total` 的中位数；total 为 0 的样本跳过，无有效样本返回 0.0。"""
    reductions: list[float] = []
    for selected, total in zip(selected_counts, total_counts):
        if int(total) <= 0:
            continue
        reductions.append(1.0 - int(selected) / int(total))
    if not reductions:
        return 0.0
    return float(statistics.median(reductions))


def verified_rate(generated: Sequence[dict[str, Any]]) -> float:
    """`status == "verified"` 的比例；无生成样本返回 0.0。"""
    items = list(generated)
    if not items:
        return 0.0
    verified = sum(1 for item in items if str(item.get("status", "")) == "verified")
    return verified / len(items)


def sample_verification_stats(
    per_sample: Sequence[dict[str, Any]],
    sample_ids: Sequence[str],
) -> dict[str, int]:
    """回归样本中产出 `verified` 生成物的样本数与未产出 `verified` 的样本数。

    与 `verified_rate` 同口径只看样本 artifacts 的 `generated`；未选中的样本、
    缺 sha 或报错的样本都计入 `verification_failures`（它们没有 verified 产物）。
    """
    wanted = {str(sample_id) for sample_id in sample_ids}
    generated_samples = 0
    verification_failures = 0
    seen: set[str] = set()
    for item in per_sample:
        sample_id = str(item.get("sample_id"))
        if sample_id not in wanted or sample_id in seen:
            continue
        seen.add(sample_id)
        generated = item.get("generated")
        verified = isinstance(generated, list) and any(
            isinstance(entry, dict) and str(entry.get("status", "")) == "verified"
            for entry in generated
        )
        if verified:
            generated_samples += 1
        else:
            verification_failures += 1
    verification_failures += len(wanted - seen)
    return {
        "generated_samples": generated_samples,
        "verification_failures": verification_failures,
    }


@dataclass(frozen=True)
class StrategyReport:
    strategy: str
    samples_total: int
    recall_at_20: float
    regression_recall: float
    median_test_count_reduction: float
    verified_rate: float
    per_sample: list[dict[str, Any]]
    generated_at: str


def build_report(strategy: str, per_sample: Sequence[dict[str, Any]]) -> StrategyReport:
    """按统一口径聚合逐样本结果；样本缺失或报错在指标中按未命中计。"""
    samples = [dict(item) for item in per_sample]
    total = len(samples)

    def _mean(key: str) -> float:
        if not samples:
            return 0.0
        values = [float(item.get(key) or 0.0) for item in samples]
        return sum(values) / len(values)

    selected_counts: list[int] = []
    total_counts: list[int] = []
    generated: list[dict[str, Any]] = []
    for item in samples:
        selected = item.get("selected_count")
        total_tests = item.get("total_count")
        if isinstance(selected, int) and isinstance(total_tests, int):
            selected_counts.append(selected)
            total_counts.append(total_tests)
        raw_generated = item.get("generated")
        if isinstance(raw_generated, list):
            generated.extend(entry for entry in raw_generated if isinstance(entry, dict))

    return StrategyReport(
        strategy=strategy,
        samples_total=total,
        recall_at_20=_mean("recall_at_20"),
        regression_recall=_mean("regression_recall"),
        median_test_count_reduction=median_test_count_reduction(selected_counts, total_counts),
        verified_rate=verified_rate(generated),
        per_sample=samples,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


__all__ = [
    "StrategyReport",
    "build_report",
    "median_test_count_reduction",
    "no_behavior_change_summary",
    "recall_at_20",
    "regression_recall",
    "sample_verification_stats",
    "verified_rate",
]


def no_behavior_change_summary(per_sample: Sequence[dict[str, Any]]) -> dict[str, int]:
    """`expected_no_behavior_change` 子集的规模与其产生的符号级变更总数（应为 0）。"""
    samples = [
        item for item in per_sample if bool(item.get("expected_no_behavior_change"))
    ]
    affected = sum(int(item.get("affected_symbols") or 0) for item in samples)
    return {"samples": len(samples), "affected_symbols": affected}