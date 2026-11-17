"""Git 仓库层：仓库校验、提交枚举、只读快照导出与双版本 diff。

全部操作只读取原始仓库：快照通过 ``git archive`` 导出到目标目录，不写 worktree、
不创建 worktree 元数据、不触碰暂存区与索引。
"""

from __future__ import annotations

import os
import re
import shutil
import tarfile
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import git

from core.schema.protocols import CommitRecord
from core.schema.types import FileDiff

NOT_A_REPOSITORY_CODE = "not_a_git_repository"
GIT_COMMAND_FAILED_CODE = "git_command_failed"

# 快照内需要跳过的三方/构建目录；``tests`` 不在其中（测试代码要参与索引）。
_EXCLUDED_DIR_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        "build",
        "dist",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        "site-packages",
        ".eggs",
    }
)

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)


@dataclass
class RepoInfo:
    local_path: str
    name: str
    default_branch: str


class NotAGitRepositoryError(ValueError):
    def __init__(self, local_path: str = "", message: str | None = None) -> None:
        self.code = NOT_A_REPOSITORY_CODE
        self.local_path = local_path
        self.message = message or f"not a git repository: {local_path or '<empty>'}"
        super().__init__(self.message)


class GitCommandError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        command: str | None = None,
        stderr: str = "",
    ) -> None:
        self.code = GIT_COMMAND_FAILED_CODE
        self.message = message
        self.command = command
        self.stderr = stderr
        super().__init__(message)


def validate_repository(local_path: str) -> RepoInfo:
    repo = _open_repo(local_path)
    root = Path(local_path).expanduser().resolve()
    return RepoInfo(
        local_path=str(root),
        name=root.name,
        default_branch=_default_branch(repo),
    )


def list_commits(local_path: str, limit: int = 50) -> list[CommitRecord]:
    repo = _open_repo(local_path)
    if limit <= 0:
        return []
    # list_commits 只有路径上下文，repo_id 取仓库目录名；调用方可按自身记录覆盖。
    repo_id = Path(local_path).expanduser().resolve().name
    records: list[CommitRecord] = []
    # 跨全部 ref 枚举：样本仓库的变更提交在各 sample/* 分支上，只遍历 HEAD 会看不到它们。
    for commit in repo.iter_commits("--all", max_count=limit):
        sha = commit.hexsha
        records.append(
            CommitRecord(
                repo_id=repo_id,
                sha=sha,
                short_sha=sha[:7],
                message=str(commit.message).strip(),
                author=_decode(commit.author.name),
                committed_at=datetime.fromtimestamp(
                    commit.committed_date, tz=timezone.utc
                ).isoformat(),
            )
        )
    return records


def export_snapshot(local_path: str, commit_sha: str, target_dir: str) -> str:
    repo = _open_repo(local_path)
    destination = Path(target_dir).expanduser().resolve()
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)

    fd, archive_path = tempfile.mkstemp(prefix="changeproof-", suffix=".tar")
    os.close(fd)
    try:
        repo.git.execute(
            ["git", "archive", "--format=tar", "-o", archive_path, commit_sha]
        )
        with tarfile.open(archive_path) as archive:
            archive.extractall(destination, filter="data")
    except git.exc.GitCommandError as exc:
        raise GitCommandError(
            f"git archive failed for commit {commit_sha}",
            command=f"git archive {commit_sha}",
            stderr=_decode(exc.stderr),
        ) from exc
    except tarfile.TarError as exc:
        raise GitCommandError(
            f"snapshot extraction failed for commit {commit_sha}",
            command=f"git archive {commit_sha}",
            stderr=str(exc),
        ) from exc
    finally:
        Path(archive_path).unlink(missing_ok=True)

    return str(destination)


def read_file_at(local_path: str, commit_sha: str, rel_path: str) -> str | None:
    repo = _open_repo(local_path)
    normalized = rel_path.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    try:
        blob = repo.commit(commit_sha).tree / normalized
    except (git.exc.BadName, git.exc.BadObject, KeyError):
        return None
    stream = getattr(blob, "data_stream", None)
    if stream is None:
        return None
    try:
        return stream.read().decode("utf-8", errors="replace")
    except OSError:
        return None


def list_python_files(
    snapshot_dir: str, include_globs: Sequence[str] = ("**/*.py",)
) -> list[str]:
    root = Path(snapshot_dir).expanduser()
    if not root.is_dir():
        return []
    found: set[str] = set()
    for pattern in include_globs:
        for candidate in root.glob(pattern):
            if not candidate.is_file():
                continue
            relative = candidate.relative_to(root)
            if _is_excluded(relative):
                continue
            found.add(relative.as_posix())
    return sorted(found)


def diff_commits(local_path: str, base_sha: str, target_sha: str) -> list[FileDiff]:
    repo = _open_repo(local_path)
    try:
        base_commit = repo.commit(base_sha)
        target_commit = repo.commit(target_sha)
    except (git.exc.BadName, git.exc.BadObject) as exc:
        raise GitCommandError(
            f"cannot resolve commits {base_sha}..{target_sha}",
            command=f"git rev-parse {base_sha} {target_sha}",
            stderr=str(exc),
        ) from exc

    diffs: list[FileDiff] = []
    for item in base_commit.diff(target_commit, create_patch=True):
        old_side_hunks, new_side_hunks = _parse_hunks(_decode(item.diff))
        a_path = _decode(item.a_path)
        b_path = _decode(item.b_path)

        if item.new_file:
            status, file_path, old_path = "added", b_path or a_path, None
        elif item.deleted_file:
            status, file_path, old_path = "deleted", a_path or b_path, a_path or b_path
        elif item.renamed_file:
            status, file_path, old_path = "renamed", b_path or a_path, a_path
        else:
            status, file_path, old_path = "modified", b_path or a_path, None

        # 删除文件在新文件侧没有任何行，故用旧文件侧区间表达被删除的行，
        # 供上层按 base 侧符号完成映射。
        hunks = old_side_hunks if status == "deleted" else new_side_hunks
        diffs.append(
            FileDiff(
                file_path=file_path,
                status=status,
                old_path=old_path,
                hunks=list(hunks),
            )
        )
    return sorted(diffs, key=lambda f: f.file_path)


def _open_repo(local_path: str) -> git.Repo:
    path = Path(local_path).expanduser() if local_path else None
    if path is None or not path.is_dir():
        raise NotAGitRepositoryError(local_path)
    try:
        return git.Repo(path, search_parent_directories=False)
    except (git.exc.InvalidGitRepositoryError, git.exc.NoSuchPathError) as exc:
        raise NotAGitRepositoryError(local_path) from exc


def _default_branch(repo: git.Repo) -> str:
    try:
        return repo.active_branch.name
    except TypeError:
        pass
    except (git.exc.GitCommandError, ValueError):
        pass
    branch_names = {branch.name for branch in repo.branches}
    for candidate in ("main", "master"):
        if candidate in branch_names:
            return candidate
    try:
        return repo.head.shorthand
    except (ValueError, git.exc.GitCommandError):
        return "HEAD"


def _is_excluded(relative: Path) -> bool:
    for part in relative.parts[:-1]:
        if part in _EXCLUDED_DIR_NAMES or part.endswith(".egg-info"):
            return True
    return False


def _parse_hunks(
    patch: str,
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    old_hunks: list[tuple[int, int]] = []
    new_hunks: list[tuple[int, int]] = []
    for match in _HUNK_RE.finditer(patch):
        old_start = int(match.group(1))
        old_count = int(match.group(2)) if match.group(2) is not None else 1
        new_start = int(match.group(3))
        new_count = int(match.group(4)) if match.group(4) is not None else 1
        old_hunks.append((old_start, old_count))
        new_hunks.append((new_start, new_count))
    return old_hunks, new_hunks


def _decode(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value