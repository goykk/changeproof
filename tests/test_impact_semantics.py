"""注释/空白/格式变更不得产生符号级变更（C-IMPACT@1 的不变式防护）。

真实样例仓库 `var/samples/shop_api` 中的 5 个 `comment_only` 样本历史上会各自产生
3~9 个符号级变更（假阳性），这些用例用于防止该问题复发。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.run_eval as ev

ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads((ROOT / "eval/samples/lock.json").read_text(encoding="utf-8"))["shop_api"]
REPO = ROOT / "var/samples/shop_api"


def build(sample_id: str, tmp_path: Path):
    sample = json.loads((ROOT / f"eval/samples/{sample_id}.json").read_text(encoding="utf-8"))
    return ev._build_artifacts(
        sample,
        REPO,
        LOCK[sample_id]["base_sha"],
        LOCK[sample_id]["target_sha"],
        tmp_path / sample_id,
        True,
        coverage_per_test=False,
        with_generation=False,
        coverage_cache_dir=tmp_path / "cc",
        coverage_cache_version="test",
        coverage_memo={},
    )


@pytest.mark.parametrize(
    "sample_id",
    [
        "comment_only_pricing_docstring",
        "comment_only_order_comment",
        "comment_only_catalog_docstring",
        "comment_only_money_docstrings",
        "comment_only_config_comment",
    ],
)
def test_comment_only_produces_no_symbol_changes(sample_id: str, tmp_path: Path) -> None:
    artifacts = build(sample_id, tmp_path)
    assert artifacts["changed"] == [], [c.symbol_id for c in artifacts["changed"]]


def test_real_change_is_still_detected(tmp_path: Path) -> None:
    """字段改名属于真实语义变更，不能被语义等价过滤误删。"""
    artifacts = build("shop_api_rename_q1", tmp_path)
    assert len(artifacts["changed"]) > 0
    assert any("Order" in symbol.qualified_name for symbol in artifacts["changed"])