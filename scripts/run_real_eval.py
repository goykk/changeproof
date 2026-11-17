"""真实开源仓库的分析链路验证（P1 真实仓库适配）。

在完整真实仓库上运行"快照 → 索引 → diff → 变更映射 → 影响传播 → 测试选择"，
输出 `var/eval_real/summary.json`。执行阶段因真实仓库依赖 PostgreSQL 未启用，
本脚本只验证静态分析与选测在该仓库上的可用性（不冒充执行验证）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.coverage.selector import fallback_full_selection, select_tests  # noqa: E402
from core.impact.mapper import load_sources, map_changes  # noqa: E402
from core.impact.propagation import compute_impact  # noqa: E402
from core.indexer import index_repository  # noqa: E402
from core.repo.git_repo import diff_commits, export_snapshot  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, help="真实仓库路径")
    parser.add_argument("--base", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--high-risk-glob", default="backend/app/core/**")
    parser.add_argument("--out", default="var/eval_real")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    out = (ROOT / args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    base_snapshot = export_snapshot(str(repo), args.base, str(out / "base"))
    target_snapshot = export_snapshot(str(repo), args.target, str(out / "target"))

    base_index = index_repository(base_snapshot, "real", args.base)
    target_index = index_repository(target_snapshot, "real", args.target)

    diffs = diff_commits(str(repo), args.base, args.target)
    py_diffs = [diff for diff in diffs if diff.file_path.endswith(".py")]
    changed = map_changes(
        py_diffs,
        base_index.symbols,
        target_index.symbols,
        "real",
        base_sources=load_sources(base_snapshot, [diff.file_path for diff in py_diffs]),
        target_sources=load_sources(target_snapshot, [diff.file_path for diff in py_diffs]),
    )
    symbols_by_id = {symbol.symbol_id: symbol for symbol in target_index.symbols}
    impact = compute_impact(changed, target_index.edges, symbols_by_id, "real")
    items = select_tests(
        changed,
        impact.items,
        list(symbols_by_id.values()),
        target_index.tests,
        [],
        high_risk_globs=(args.high_risk_glob,),
        edges=target_index.edges,
    )
    if not items and target_index.tests and changed:
        # C-SELECT@8：存在 Python 变更且仓库有测试时不得返回空选择集；纯非 Python 变更不保底。
        items = fallback_full_selection("real", target_index.tests)

    summary = {
        "repo": str(repo),
        "base": args.base,
        "target": args.target,
        "python_files_diffed": [diff.file_path for diff in py_diffs],
        "base_symbols": len(base_index.symbols),
        "target_symbols": len(target_index.symbols),
        "target_edges": len(target_index.edges),
        "discovered_tests": len(target_index.tests),
        "index_warnings": {
            "base": base_index.warnings[:10],
            "target": target_index.warnings[:10],
            "counts": [len(base_index.warnings), len(target_index.warnings)],
        },
        "changed_symbols": [
            {"symbol_id": item.symbol_id, "kind": item.kind.value, "change_type": item.change_type.value}
            for item in changed
        ],
        "impacts": [
            {
                "symbol_id": item.symbol_id,
                "distance": item.distance,
                "confidence": item.confidence.value,
                "analysis_source": item.analysis_source.value,
            }
            for item in impact.items[:50]
        ],
        "impact_total": len(impact.items),
        "truncated": impact.truncated,
        "selected_tests": [
            {
                "test_node_id": item.test_node_id,
                "reasons": [reason.source for reason in item.reasons],
            }
            for item in items
        ],
        "selected_total": len(items),
        "execution_note": "未执行：真实仓库测试依赖 PostgreSQL，本轮只验证静态分析与选测。",
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"repo={repo.name} {args.base[:8]}..{args.target[:8]}")
    print(f"  symbols base={summary['base_symbols']} target={summary['target_symbols']} edges={summary['target_edges']} tests={summary['discovered_tests']}")
    print(f"  index warnings base={summary['index_warnings']['counts'][0]} target={summary['index_warnings']['counts'][1]}")
    print(f"  changed={len(changed)} impacts={summary['impact_total']} selected={summary['selected_total']}")
    for item in changed[:8]:
        print(f"    changed: {item.symbol_id} ({item.change_type.value})")
    for item in impact.items[:6]:
        print(f"    impact: {item.symbol_id} d={item.distance} {item.confidence.value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())