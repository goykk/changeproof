"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Badge, Button, Card, ErrorBanner, Spinner } from "@/components/ui";
import { ApiError, createChangeset, createRepo, listCommits, listRepos } from "@/lib/api";
import type { CommitOut, RepositoryOut } from "@/lib/types";

type Banner = { code: string; message: string };

function toBanner(error: unknown): Banner {
  if (error instanceof ApiError) return { code: error.code, message: error.message };
  if (error instanceof Error) return { code: "unexpected_error", message: error.message };
  return { code: "unexpected_error", message: String(error) };
}

function defaultRepoName(localPath: string): string {
  const segments = localPath.split(/[\\/]+/).filter(Boolean);
  return segments[segments.length - 1] ?? "repo";
}

export default function HomePage() {
  const router = useRouter();

  const [repos, setRepos] = useState<RepositoryOut[]>([]);
  const [reposLoading, setReposLoading] = useState(true);
  const [error, setError] = useState<Banner | null>(null);

  const [name, setName] = useState("");
  const [localPath, setLocalPath] = useState("");
  const [importing, setImporting] = useState(false);

  const [repoId, setRepoId] = useState("");
  const [commits, setCommits] = useState<CommitOut[]>([]);
  const [commitsLoading, setCommitsLoading] = useState(false);
  const [baseSha, setBaseSha] = useState("");
  const [targetSha, setTargetSha] = useState("");
  const [creating, setCreating] = useState(false);

  const refreshRepos = useCallback(async () => {
    setReposLoading(true);
    try {
      const data = await listRepos();
      setRepos(data.items);
      setRepoId((previous) => previous || data.items[0]?.repo_id || "");
    } catch (caught) {
      setError(toBanner(caught));
    } finally {
      setReposLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshRepos();
  }, [refreshRepos]);

  useEffect(() => {
    if (!repoId) {
      setCommits([]);
      setBaseSha("");
      setTargetSha("");
      return;
    }
    let cancelled = false;
    setCommitsLoading(true);
    listCommits(repoId, 50)
      .then((data) => {
        if (cancelled) return;
        setCommits(data.items);
        setTargetSha(data.items[0]?.sha ?? "");
        setBaseSha(data.items[1]?.sha ?? "");
      })
      .catch((caught) => {
        if (!cancelled) setError(toBanner(caught));
      })
      .finally(() => {
        if (!cancelled) setCommitsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [repoId]);

  async function handleImport(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setImporting(true);
    try {
      const trimmedPath = localPath.trim();
      const repo = await createRepo({ name: name.trim() || defaultRepoName(trimmedPath), local_path: trimmedPath });
      setName("");
      setLocalPath("");
      await refreshRepos();
      setRepoId(repo.repo_id);
    } catch (caught) {
      setError(toBanner(caught));
    } finally {
      setImporting(false);
    }
  }

  async function handleCreate() {
    if (!repoId || !baseSha || !targetSha) return;
    setError(null);
    setCreating(true);
    try {
      const created = await createChangeset({ repo_id: repoId, base_sha: baseSha, target_sha: targetSha });
      router.push(`/changesets/${created.changeset_id}`);
    } catch (caught) {
      setError(toBanner(caught));
    } finally {
      setCreating(false);
    }
  }

  const canCreate = Boolean(repoId && baseSha && targetSha && baseSha !== targetSha) && !creating;

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900">仓库与变更</h1>
        <p className="mt-1 text-sm text-slate-500">
          导入本地 Git 仓库，选择基线与变更提交，创建 changeset 后在详情页触发分析。
        </p>
      </header>

      <ErrorBanner error={error} />

      <Card title="导入仓库" description="仅接受本地 Git 仓库路径；非 Git 仓库将返回 not_a_git_repository。">
        <form className="grid gap-3 md:grid-cols-[1fr_2fr_auto]" onSubmit={handleImport}>
          <input
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm focus:border-slate-500 focus:outline-none"
            placeholder="仓库名称（留空取路径末段）"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
          <input
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm focus:border-slate-500 focus:outline-none"
            placeholder="var/samples/shop_api"
            value={localPath}
            onChange={(event) => setLocalPath(event.target.value)}
            data-testid="repo-path-input"
            required
          />
          <Button type="submit" disabled={importing} data-testid="repo-submit">
            {importing ? "导入中…" : "导入"}
          </Button>
        </form>
      </Card>

      <Card
        title="仓库列表"
        actions={
          <>
            {reposLoading && <Spinner />}
            <Button variant="secondary" onClick={() => void refreshRepos()}>
              刷新
            </Button>
          </>
        }
      >
        {repos.length === 0 && !reposLoading ? (
          <p className="text-sm text-slate-500">尚未导入仓库。</p>
        ) : (
          <ul className="divide-y divide-slate-100">
            {repos.map((repo) => {
              const active = repo.repo_id === repoId;
              return (
                <li key={repo.repo_id}>
                  <button
                    type="button"
                    onClick={() => setRepoId(repo.repo_id)}
                    className={`flex w-full flex-wrap items-center justify-between gap-2 px-2 py-2 text-left text-sm ${
                      active ? "bg-slate-100" : "hover:bg-slate-50"
                    }`}
                  >
                    <span className="font-medium text-slate-900">{repo.name}</span>
                    <span className="font-mono text-xs text-slate-500">{repo.local_path}</span>
                    <span className="flex items-center gap-2">
                      <Badge tone="neutral">{repo.default_branch}</Badge>
                      <Badge tone={repo.status === "ready" ? "green" : "neutral"}>{repo.status}</Badge>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      <Card title="选择提交并创建 changeset" description="基线提交是新文件侧 diff 的对比起点。">
        <div className="space-y-4">
          <div className="flex items-center gap-3 text-sm text-slate-600">
            <span>当前仓库</span>
            <span className="font-mono text-xs">{repoId || "未选择"}</span>
            {commitsLoading && <Spinner label="加载提交…" />}
          </div>

          <div className="grid gap-3 md:grid-cols-2">
            <label className="text-sm">
              <span className="mb-1 block text-slate-600">基线提交（base）</span>
              <input
                className="w-full rounded-md border border-slate-300 px-3 py-1.5 font-mono text-xs"
                placeholder="完整 sha，可从下拉候选选择"
                value={baseSha}
                onChange={(event) => setBaseSha(event.target.value.trim())}
                list="commit-options"
                data-testid="commit-base"
              />
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-slate-600">变更提交（target）</span>
              <input
                className="w-full rounded-md border border-slate-300 px-3 py-1.5 font-mono text-xs"
                placeholder="完整 sha，可从下拉候选选择"
                value={targetSha}
                onChange={(event) => setTargetSha(event.target.value.trim())}
                list="commit-options"
                data-testid="commit-target"
              />
            </label>
          </div>

          <datalist id="commit-options">
            {commits.map((commit) => (
              <option key={commit.sha} value={commit.sha}>
                {commit.short_sha} · {commit.message}
              </option>
            ))}
          </datalist>

          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={() => void handleCreate()} disabled={!canCreate} data-testid="changeset-create">
              {creating ? "创建中…" : "创建 changeset"}
            </Button>
            {!commitsLoading && commits.length < 2 && (
              <span className="text-xs text-slate-500">
                提交候选仅覆盖当前分支历史；可填入其他分支的完整 sha（如 eval/samples/lock.json 中的 sha）。
              </span>
            )}
          </div>
        </div>
      </Card>
    </div>
  );
}