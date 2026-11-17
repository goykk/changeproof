"""Evidence report aggregation for a completed analysis task.

The payload layout is frozen by C-API@1 / C-ORCH@1: static facts, dynamic facts and
model suggestions are stored in separate fields so they are never mixed.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from core.schema.types import RunResult, RunStatus


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_jsonable(value: Any) -> Any:
    """Recursively convert dataclasses/enums/paths into JSON-safe structures."""
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {key: to_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def write_json(path: Path, payload: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_jsonable(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def _status_value(result: RunResult | None) -> str | None:
    if result is None:
        return None
    return result.status.value if isinstance(result.status, RunStatus) else str(result.status)


def _run_payload(version_type: str, result: RunResult) -> dict[str, Any]:
    return {
        "version_type": version_type,
        "status": _status_value(result),
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
        "selected_count": result.selected_count,
        "passed_count": result.passed_count,
        "failed_count": result.failed_count,
        "error_count": result.error_count,
        "skipped_count": result.skipped_count,
        "isolation_mode": result.isolation_mode,
        "log_path": result.log_path,
        "coverage_path": result.coverage_path,
    }


def _selectors_by_source(selections: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in selections:
        for reason in item.get("reasons") or []:
            source = str(reason.get("source") or "unknown")
            counts[source] = counts.get(source, 0) + 1
    return counts


def _suggestion_payload(item: Mapping[str, Any]) -> dict[str, Any]:
    """生成物字段固定，且始终标注 ``analysis_source="model"``。"""
    return {
        "test_node_id": item.get("test_node_id"),
        "status": item.get("status"),
        "rationale": item.get("rationale"),
        "analysis_source": item.get("analysis_source") or "model",
        "validation_evidence": item.get("validation_evidence") or {},
    }


def build_report_payload(
    *,
    task_id: str,
    changeset_id: str,
    repo_id: str,
    base_sha: str,
    target_sha: str,
    generated_at: str,
    changed_symbols: Sequence[Mapping[str, Any]],
    impacts: Sequence[Mapping[str, Any]],
    truncated: bool,
    selections: Sequence[Mapping[str, Any]],
    base_run: RunResult,
    target_run: RunResult,
    coverage_missing: bool,
    model_suggestions: Sequence[Mapping[str, Any]],
    degraded_steps: Sequence[str],
    isolation_mode: str,
    uncovered_symbol_ids: Sequence[str] = (),
    warnings: Sequence[str] = (),
    baseline_run: RunResult | None = None,
) -> dict[str, Any]:
    target_status = _status_value(target_run)
    base_status = _status_value(base_run)
    regression_detected = base_status == RunStatus.PASSED.value and target_status in {
        RunStatus.FAILED.value,
        RunStatus.ERROR.value,
        RunStatus.TIMEOUT.value,
    }

    residual_risks: list[dict[str, Any]] = []
    if coverage_missing:
        residual_risks.append(
            {
                "risk": "coverage_missing",
                "reason": "baseline coverage.json is unavailable; selection relied on static and guard sources",
                "source": "coverage",
            }
        )
    if truncated:
        residual_risks.append(
            {
                "risk": "impact_truncated",
                "reason": "impact propagation reached max_depth or max_results",
                "source": "static",
            }
        )
    if uncovered_symbol_ids:
        residual_risks.append(
            {
                "risk": "changed_symbols_uncovered",
                "reason": ", ".join(list(uncovered_symbol_ids)[:20]),
                "source": "coverage",
            }
        )
    if not selections:
        residual_risks.append(
            {
                "risk": "no_tests_selected",
                "reason": "no pytest node matched coverage, static dependency or guard sources",
                "source": "static",
            }
        )
    if isolation_mode != "docker":
        residual_risks.append(
            {
                "risk": "weak_isolation",
                "reason": f"isolation_mode={isolation_mode}; network and resource limits are not enforced",
                "source": "execution",
            }
        )
    pending_suggestions = [item for item in model_suggestions if item.get("status") != "verified"]
    if pending_suggestions:
        residual_risks.append(
            {
                "risk": "generated_tests_pending_review",
                "reason": f"{len(pending_suggestions)} generated suggestion(s) are not verified",
                "source": "model",
            }
        )
    for warning in warnings:
        residual_risks.append({"risk": "warning", "reason": str(warning), "source": "static"})

    static_facts: dict[str, Any] = {
        "repo_id": repo_id,
        "base_sha": base_sha,
        "target_sha": target_sha,
        "changed_symbols": [dict(item) for item in changed_symbols],
        "impacts": [dict(item) for item in impacts],
        "selections": [dict(item) for item in selections],
        "sources": _selectors_by_source(selections),
        "truncated": truncated,
        "warnings": [str(warning) for warning in warnings],
    }

    dynamic_facts: dict[str, Any] = {
        "coverage_missing": coverage_missing,
        "runs": [_run_payload("target", target_run), _run_payload("base", base_run)],
        "isolation_mode": isolation_mode,
    }
    if baseline_run is not None:
        dynamic_facts["baseline"] = _run_payload("baseline", baseline_run)

    return {
        "task_id": task_id,
        "changeset_id": changeset_id,
        "generated_at": generated_at,
        "summary": {
            "changed_symbols": len(changed_symbols),
            "impacted_symbols": len(impacts),
            "selected_tests": len(selections),
            "regression_detected": regression_detected,
        },
        "static_facts": static_facts,
        "dynamic_facts": dynamic_facts,
        "model_suggestions": [_suggestion_payload(item) for item in model_suggestions],
        "residual_risks": residual_risks,
        "degraded_steps": [str(step) for step in degraded_steps],
        "truncated": truncated,
    }


def write_report(store, task_id: str, changeset_id: str, payload: Mapping[str, Any], report_dir: Path) -> str:
    report_dir.mkdir(parents=True, exist_ok=True)
    write_json(report_dir / "report.json", payload)
    return store.reports.save_report(task_id, changeset_id, to_jsonable(payload))