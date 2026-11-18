"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Button, ErrorBanner, PageHeader, Spinner, Status } from "@/components/ui";
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

function shortSha(sha: string): string {
  return sha.slice(0, 7);
}

function formatCommitTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export default function HomePage() {
  const router = useRouter();

  const [repos, setRepos] = useState<RepositoryOut[]>([]);
  const [reposLoading, setReposLoading] = useState(true);
  const [error, setError] = useState<Banner | null>(null);
  const [importOpen, setImportOpen] = useState(false);

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
    if (!reposLoading && repos.length === 0) setImportOpen(true);
  }, [repos.length, reposLoading]);

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
      setImportOpen(false);
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

  function handleSwap() {
    setBaseSha(targetSha);
    setTargetSha(baseSha);
  }

  const activeRepo = useMemo(() => repos.find((repo) => repo.repo_id === repoId) ?? null, [repos, repoId]);
  const baseCommit = useMemo(() => commits.find((commit) => commit.sha === baseSha) ?? null, [commits, baseSha]);
  const targetCommit = useMemo(() => commits.find((commit) => commit.sha === targetSha) ?? null, [commits, targetSha]);
  const repoReady = activeRepo?.status === "ready";
  const rangeReady = Boolean(baseSha && targetSha && baseSha !== targetSha);
  const canCreate = Boolean(repoId && rangeReady) && !creating;

  return (
    <div className="space-y-5">
      <PageHeader
        title="新建分析"
        description="选仓库、定提交，直接生成变更影响与回归测试。"
        actions={
          <button
            type="button"
            onClick={() => void refreshRepos()}
            disabled={reposLoading}
            className="text-[11px] font-medium text-blue-700 underline decoration-blue-200 underline-offset-2 hover:text-blue-800 disabled:cursor-not-allowed disabled:text-slate-300"
          >
            {reposLoading ? "刷新中…" : "刷新仓库"}
          </button>
        }
      />

      <ErrorBanner error={error} />

      <section className="cp-panel overflow-hidden border-t-2 border-t-blue-600">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-[#f8fafc] px-5 py-2.5">
          <div className="flex min-w-0 items-center gap-2">
            <span className="text-[10px] font-medium tracking-[0.04em] text-slate-400">当前仓库</span>
            <span className="truncate text-[13px] font-semibold text-slate-900">{activeRepo?.name ?? "未选择"}</span>
            {activeRepo && <span className="cp-mono text-[10px] text-slate-500">{activeRepo.default_branch}</span>}
            {reposLoading && <Spinner />}
          </div>
          <button
            type="button"
            onClick={() => setImportOpen((open) => !open)}
            className="text-[11px] font-medium text-blue-700 hover:text-blue-800"
          >
            {importOpen ? "收起接入" : "接入本地仓库"}
          </button>
        </header>

        {importOpen && (
          <form
            className="grid gap-2 border-b border-slate-200 bg-[#f8fafc] px-5 py-3 sm:grid-cols-[180px_minmax(0,1fr)_auto]"
            onSubmit={handleImport}
          >
            <input
              className="cp-input"
              placeholder="仓库名称，可留空"
              value={name}
              onChange={(event) => setName(event.target.value)}
              aria-label="仓库名称"
            />
            <input
              className="cp-input cp-mono"
              placeholder="本地 Git 仓库路径，如 var/samples/shop_api"
              value={localPath}
              onChange={(event) => setLocalPath(event.target.value)}
              data-testid="repo-path-input"
              aria-label="仓库本地路径"
              required
            />
            <Button type="submit" size="lg" disabled={importing} data-testid="repo-submit">
              {importing ? "接入中…" : "接入"}
            </Button>
          </form>
        )}

        <div className="grid md:grid-cols-[228px_minmax(0,1fr)]">
          <div className="border-b border-slate-200 bg-[#f8fafc] md:border-b-0 md:border-r">
            <div className="flex items-center justify-between border-b border-slate-200 px-4 py-2">
              <span className="text-[11px] font-medium text-slate-500">本地仓库</span>
              <span className="cp-mono text-[10px] text-slate-400">{repos.length}</span>
            </div>

            {repos.length === 0 && !reposLoading ? (
              <div className="px-4 py-4">
                <p className="text-[12px] font-medium text-slate-700">还没有仓库</p>
                <p className="mt-1 text-[11px] leading-5 text-slate-500">接入一个本地 Git 仓库后开始。</p>
                <button
                  type="button"
                  onClick={() => setImportOpen(true)}
                  className="mt-2 text-[11px] font-medium text-blue-700 hover:underline"
                >
                  接入本地仓库
                </button>
              </div>
            ) : (
              <div>
                {repos.map((repo) => {
                  const active = repo.repo_id === repoId;
                  const ready = repo.status === "ready";
                  return (
                    <button
                      key={repo.repo_id}
                      type="button"
                      aria-pressed={active}
                      onClick={() => setRepoId(repo.repo_id)}
                      className={`block w-full border-b border-slate-200 px-4 py-3 text-left last:border-b-0 ${
                        active ? "bg-blue-50/60 shadow-[inset_3px_0_0_#2563eb]" : "hover:bg-white/70"
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className={`truncate text-[12px] ${active ? "font-semibold text-slate-950" : "font-medium text-slate-700"}`}>
                          {repo.name}
                        </span>
                        <span className={`text-[10px] ${ready ? "text-emerald-700" : "text-amber-700"}`}>
                          {ready ? "就绪" : repo.status}
                        </span>
                      </div>
                      <div className="mt-1 flex min-w-0 items-center gap-1.5 text-[10px] text-slate-400">
                        <span className="cp-mono shrink-0">{repo.default_branch}</span>
                        <span className="truncate">{repo.local_path}</span>
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          <div className="min-w-0 p-5">
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <h2 className="text-[13px] font-semibold text-slate-900">提交范围</h2>
                {commitsLoading && <Spinner label="加载提交…" />}
              </div>
              <button
                type="button"
                onClick={handleSwap}
                disabled={!baseSha || !targetSha}
                className="text-[11px] text-blue-700 hover:text-blue-800 disabled:cursor-not-allowed disabled:text-slate-300"
              >
                交换基线 / 变更
              </button>
            </div>

            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <CommitBox
                label="基线提交"
                annotation="对比起点"
                inputId="commit-base"
                testId="commit-base"
                tone="blue"
                sha={baseSha}
                commit={baseCommit}
                onChange={setBaseSha}
              />
              <CommitBox
                label="变更提交"
                annotation="被分析版本"
                inputId="commit-target"
                testId="commit-target"
                tone="green"
                sha={targetSha}
                commit={targetCommit}
                onChange={setTargetSha}
              />
            </div>

            <datalist id="commit-options">
              {commits.map((commit) => (
                <option key={commit.sha} value={commit.sha}>
                  {commit.short_sha} · {commit.message}
                </option>
              ))}
            </datalist>

            <p className="mt-3 text-[10px] leading-4 text-slate-400">
              候选提交来自当前分支；也可以直接填入完整 SHA。
            </p>
          </div>
        </div>

        <footer className="flex flex-col gap-3 border-t border-slate-200 bg-[#fbfcfe] px-5 py-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <Status tone={repoReady ? "green" : "gray"}>{repoReady ? "仓库就绪" : "仓库未就绪"}</Status>
            <Status tone={rangeReady ? "green" : "gray"}>{rangeReady ? "范围有效" : "需要两个不同提交"}</Status>
          </div>
          <div className="flex items-center justify-between gap-4 sm:justify-end">
            <span className="text-[10px] text-slate-400">本地执行 · 约 1–3 分钟</span>
            <Button
              size="lg"
              className="min-w-[120px]"
              onClick={() => void handleCreate()}
              disabled={!canCreate}
              data-testid="changeset-create"
            >
              {creating ? "创建中…" : "创建分析"}
            </Button>
          </div>
        </footer>
      </section>
    </div>
  );
}

function CommitBox({
  label,
  annotation,
  inputId,
  testId,
  tone,
  sha,
  commit,
  onChange,
}: {
  label: string;
  annotation: string;
  inputId: string;
  testId: string;
  tone: "blue" | "green";
  sha: string;
  commit: CommitOut | null;
  onChange: (value: string) => void;
}) {
  return (
    <div
      className={`rounded-[4px] border border-t-2 border-slate-300 bg-white focus-within:border-blue-500 ${
        tone === "blue" ? "border-t-blue-500" : "border-t-emerald-500"
      }`}
    >
      <div className="flex items-center justify-between border-b border-slate-200 px-3 py-1.5">
        <label
          htmlFor={inputId}
          className={`text-[10px] font-medium ${tone === "blue" ? "text-blue-700" : "text-emerald-700"}`}
        >
          {label}
        </label>
        <span className="cp-mono text-[10px] text-slate-400">{sha ? shortSha(sha) : annotation}</span>
      </div>
      <input
        id={inputId}
        className="cp-mono h-10 w-full border-0 bg-transparent px-3 text-[11px] text-slate-900 outline-none placeholder:text-slate-400"
        placeholder="完整 SHA"
        value={sha}
        onChange={(event) => onChange(event.target.value.trim())}
        list="commit-options"
        data-testid={testId}
      />
      <div className="min-h-[46px] border-t border-slate-100 bg-[#fbfcfd] px-3 py-1.5">
        {commit ? (
          <>
            <p className="truncate text-[11px] text-slate-700">{commit.message}</p>
            <p className="mt-0.5 text-[10px] text-slate-400">
              {commit.author} · {formatCommitTime(commit.committed_at)}
            </p>
          </>
        ) : (
          <p className="pt-1 text-[10px] text-slate-400">{sha ? "不在候选列表，按输入 SHA 使用" : "选择候选或直接输入完整 SHA"}</p>
        )}
      </div>
    </div>
  );
}
