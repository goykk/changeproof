"""第二示例仓库（包式布局 blog_api）的样本生成器（C-SECOND-REPO@1）。

幂等重建 `var/samples/blog_api`：基线 tag `v1-baseline` + 每样本分支/tag，
并写 `eval/blog/<sample_id>.json` 与 `eval/blog/lock.json`。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "samples" / "blog_api_seed"
REPO = ROOT / "var" / "samples" / "blog_api"
OUT = ROOT / "eval" / "blog"


@dataclass(frozen=True)
class Sample:
    sample_id: str
    change_kind: str
    edits: tuple[tuple[str, str, str], ...]
    expected_impact_files: tuple[str, ...]
    expected_failing_tests: tuple[str, ...]
    no_behavior_change: bool = False


SAMPLES = (
    Sample(
        sample_id="blog_rename_post_title",
        change_kind="rename_field",
        edits=(("src/blog/models.py", "title: Mapped[str] = mapped_column(String(200))", "headline: Mapped[str] = mapped_column(String(200))"),),
        expected_impact_files=("src/blog/models.py", "src/blog/services/posts.py", "src/blog/api/posts.py"),
        expected_failing_tests=(
            "tests/test_api.py::test_get_post",
            "tests/test_api.py::test_list_posts",
            "tests/test_models.py::test_post_author_relationship",
            "tests/test_models.py::test_post_title",
            "tests/test_services.py::test_excerpt",
            "tests/test_services.py::test_list_posts",
            "tests/test_services.py::test_title_of",
        ),
    ),
    Sample(
        sample_id="blog_signature_excerpt_limit",
        change_kind="signature_change",
        edits=(
            (
                "src/blog/services/posts.py",
                "def excerpt(self, post_id: int) -> str:",
                "def excerpt(self, post_id: int, max_words: int) -> str:",
            ),
            (
                "src/blog/services/posts.py",
                'return " ".join(post.body.split()[:8])',
                'return " ".join(post.body.split()[:max_words])',
            ),
        ),
        expected_impact_files=("src/blog/services/posts.py", "src/blog/api/posts.py"),
        expected_failing_tests=("tests/test_services.py::test_excerpt", "tests/test_api.py::test_get_post"),
    ),
    Sample(
        sample_id="blog_route_post_detail_prefix",
        change_kind="route_param",
        edits=(("src/blog/api/posts.py", '@router.get("/{post_id}")', '@router.get("/detail/{post_id}")'),),
        expected_impact_files=("src/blog/api/posts.py",),
        expected_failing_tests=("tests/test_api.py::test_get_post",),
    ),
    Sample(
        sample_id="blog_shared_slugify_underscore",
        change_kind="shared_util",
        edits=(("src/blog/core/text.py", 'return "-".join(', 'return "_".join('),),
        expected_impact_files=("src/blog/core/text.py", "src/blog/api/posts.py"),
        expected_failing_tests=("tests/test_core_text.py::test_slugify",),
    ),
    Sample(
        sample_id="blog_comment_only_models_doc",
        change_kind="comment_only",
        edits=(
            (
                "src/blog/models.py",
                '"""SQLAlchemy 模型：Author/Post/Comment 及其关系。"""',
                '"""SQLAlchemy 模型：Author/Post/Comment 及其关系（含反向引用）。"""',
            ),
        ),
        expected_impact_files=(),
        expected_failing_tests=(),
        no_behavior_change=True,
    ),
)


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _apply_edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"edit_mismatch: {path} 中找到 {text.count(old)} 处匹配")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def build() -> None:
    if REPO.exists():
        shutil.rmtree(REPO)
    shutil.copytree(SEED, REPO)
    subprocess.run(["git", "init", "-b", "main", str(REPO)], check=True, capture_output=True)
    _git("config", "user.email", "changeproof@example.com")
    _git("config", "user.name", "ChangeProof")
    _git("add", ".")
    _git("commit", "-m", "baseline: blog api")
    _git("tag", "v1-baseline")
    base_sha = _git("rev-parse", "HEAD")

    OUT.mkdir(parents=True, exist_ok=True)
    lock: dict[str, dict[str, dict[str, str]]] = {"blog_api": {}}
    for sample in SAMPLES:
        _git("checkout", "-b", f"sample_work/{sample.sample_id}", "v1-baseline")
        for rel, old, new in sample.edits:
            _apply_edit(REPO / rel, old, new)
        _git("add", ".")
        _git("commit", "-m", f"sample: {sample.sample_id}")
        _git("tag", f"sample/{sample.sample_id}")
        target_sha = _git("rev-parse", "HEAD")
        _git("checkout", "main")
        _git("branch", "-D", f"sample_work/{sample.sample_id}")
        lock["blog_api"][sample.sample_id] = {"base_sha": base_sha, "target_sha": target_sha}
        (OUT / f"{sample.sample_id}.json").write_text(
            json.dumps(
                {
                    "sample_id": sample.sample_id,
                    "repo_name": "blog_api",
                    "base_ref": "v1-baseline",
                    "target_ref": f"sample/{sample.sample_id}",
                    "change_kind": sample.change_kind,
                    "expected_impact_files": list(sample.expected_impact_files),
                    "expected_impact_symbols": [],
                    "expected_failing_tests": list(sample.expected_failing_tests),
                    "expected_no_behavior_change": sample.no_behavior_change,
                    "high_risk_globs": ["src/blog/core/**"],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    (OUT / "lock.json").write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    print(f"built {REPO} with {len(SAMPLES)} samples; baseline={base_sha[:8]}")


if __name__ == "__main__":
    build()