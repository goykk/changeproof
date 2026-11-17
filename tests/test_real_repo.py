"""真实仓库泛化与目录镜像/空集保底的不变式（C-SECOND-REPO + C-SELECT@8）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.coverage.selector import _path_mirror, fallback_full_selection
from core.schema.types import TestCaseInfo

ROOT = Path(__file__).resolve().parents[1]
REAL_REPO = ROOT / "var" / "realrepos" / "full-stack-fastapi-template"


def test_path_mirror_matches_tests_to_app_layout() -> None:
    assert _path_mirror(
        "backend/tests/api/routes/test_items.py",
        "backend/app/api/routes/items.py",
    )
    assert _path_mirror("tests/api/test_users.py", "app/api/users.py")


def test_path_mirror_rejects_non_mirroring_paths() -> None:
    assert _path_mirror("tests/test_core_text.py", "app/core/text.py") is None
    assert _path_mirror("src/blog/tests/test_api.py", "src/blog/api/posts.py") is None


def test_fallback_full_selection_is_non_empty_and_tagged() -> None:
    tests = [
        TestCaseInfo(
            repo_id="r",
            commit_sha="c",
            node_id="tests/test_a.py::test_one",
            file_path="tests/test_a.py",
            name="test_one",
        )
    ]
    items = fallback_full_selection("cs", tests)
    assert len(items) == 1
    assert items[0].strategy == "fallback_full"
    assert items[0].reasons[0].source == "high_risk_guard"


@pytest.mark.skipif(not REAL_REPO.exists(), reason="真实仓库未克隆（var/realrepos）")
def test_real_repo_analysis_produces_selection(tmp_path) -> None:
    import json
    import subprocess
    import sys

    out = tmp_path / "real"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_real_eval.py"),
            "--repo",
            str(REAL_REPO),
            "--base",
            "f8dd304^",
            "--target",
            "f8dd304",
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, result.stderr[-800:]
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["target_symbols"] > 100
    assert summary["discovered_tests"] > 0
    assert summary["selected_total"] > 0

@pytest.mark.skipif(not REAL_REPO.exists(), reason="真实仓库未克隆（var/realrepos）")
def test_typescript_index_in_real_repo() -> None:
    from core.indexer import index_repository
    from core.indexer.typescript import typescript_available

    if not typescript_available():
        pytest.skip("TypeScript 工具链不可用")
    result = index_repository(
        str(REAL_REPO),
        "real",
        "head",
        include_typescript=True,
    )
    ts_symbols = [s for s in result.symbols if s.metadata.get("language") == "typescript"]
    assert len(ts_symbols) > 50
    assert any(edge.file_path.endswith((".ts", ".tsx")) for edge in result.edges)