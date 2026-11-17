"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { ImpactGraph } from "@/components/ImpactGraph";
import { SelectionTable } from "@/components/SelectionTable";
import { TaskProgress } from "@/components/TaskProgress";
import { Badge, Button, Card, ErrorBanner, Spinner } from "@/components/ui";
import {
  ApiError,
  analyzeChangeset,
  getGraph,
  getImpact,
  getReport,
  getRuns,
  getSelections,
  getTask,
  retryTask,
} from "@/lib/api";
import type { GraphOut, ImpactOut, RunsOut, SelectionsOut, TaskOut } from "@/lib/types";

const MAX_DEPTH = 3;
const POLL_INTERVAL_MS = 2000;

type Banner = { code: string; message: string };

function toBanner(error: unknown): Banner {
  if (error instanceof ApiError) return { code: error.code, message: error.message };
  if (error instanceof Error) return { code: "unexpected_error", message: error.message };
  return { code: "unexpected_error", message: String(error) };
}

export default function ChangesetPage({ params }: { params: { id: string } }) {
  return (
    <Suspense fallback={<Spinner label="加载中…" />}>
      <ChangesetDetail changesetId={params.id} />
    </Suspense>
  );
}

function ChangesetDetail({ changesetId }: { changesetId: string }) {
  const router = useRouter();
  const search = useSearchParams();

  const [taskId, setTaskId] = useState<string | null>(search.get("task_id"));
  const [task, setTask] = useState<TaskOut | null>(null);
  const [pollToken, setPollToken] = useState(0);
  const [impact, setImpact] = useState<ImpactOut | null>(null);
  const [graph, setGraph] = useState<GraphOut | null>(null);
  const [selections, setSelections] = useState<SelectionsOut | null>(null);
  const [runs, setRuns] = useState<RunsOut | null>(null);
  const [coverageMissing, setCoverageMissing] = useState(false);
  const [artifactsLoading, setArtifactsLoading] = useState(false);
  const [starting, setStarting] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [generateTests, setGenerateTests] = useState(false);
  const [error, setError] = useState<Banner | null>(null);

  const loadArtifacts = useCallback(async () => {
    setArtifactsLoading(true);
    const results = await Promise.allSettled([
      getImpact(changesetId),
      getGraph(changesetId, MAX_DEPTH),
      getSelections(changesetId),
      getRuns(changesetId),
      getReport(changesetId),
    ]);
    const [impactResult, graphResult, selectionsResult, runsResult, reportResult] = results;

    if (impactResult.status === "fulfilled") setImpact(impactResult.value);
    if (graphResult.status === "fulfilled") setGraph(graphResult.value);
    if (selectionsResult.status === "fulfilled") setSelections(selectionsResult.value);
    if (runsResult.status === "fulfilled") setRuns(runsResult.value);
    if (reportResult.status === "fulfilled") setCoverageMissing(reportResult.value.dynamic_facts.coverage_missing);

    const failure = results.find(
      (result): result is PromiseRejectedResult =>
        result.status === "rejected" &&
        !(
          result.reason instanceof ApiError &&
          (result.reason.code === "not_found" || result.reason.code === "report_not_ready")
        ),
    );
    if (failure) setError(toBanner(failure.reason));
    setArtifactsLoading(false);
  }, [changesetId]);

  useEffect(() => {
    if (!taskId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | null = null;

    const poll = async () => {
      try {
        const next = await getTask(taskId);
        if (cancelled) return;
        setTask(next);
        if (next.status === "reported" || next.status === "failed") {
          if (timer) clearInterval(timer);
          timer = null;
          await loadArtifacts();
        }
      } catch (caught) {
        if (!cancelled) setError(toBanner(caught));
      }
    };

    void poll();
    timer = setInterval(() => {
      void poll();
    }, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
  }, [taskId, pollToken, loadArtifacts]);

  async function handleStart() {
    setError(null);
    setStarting(true);
    try {
      const created = await analyzeChangeset(changesetId, { generate_tests: generateTests });
      setTask(created);
      setTaskId(created.task_id);
      router.replace(`/changesets/${changesetId}?task_id=${encodeURIComponent(created.task_id)}`);
    } catch (caught) {
      setError(toBanner(caught));
    } finally {
      setStarting(false);
    }
  }

  async function handleRetry() {
    if (!taskId) return;
    setError(null);
    setRetrying(true);
    try {
      const next = await retryTask(taskId);
      setTask(next);
      setPollToken((token) => token + 1);
    } catch (caught) {
      setError(toBanner(caught));
    } finally {
      setRetrying(false);
    }
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">变更影响详情</h1>
          <p className="mt-1 font-mono text-xs text-slate-500">{changesetId}</p>
        </div>
        <div className="flex items-center gap-2">
          <Link
            href={`/changesets/${changesetId}/report`}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-100"
          >
            证据报告
          </Link>
          <Button variant="secondary" onClick={() => void loadArtifacts()} disabled={artifactsLoading}>
            {artifactsLoading ? "刷新中…" : "刷新产物"}
          </Button>
        </div>
      </header>

      <ErrorBanner error={error} />

      {!taskId ? (
        <Card title="尚未触发分析" description="该 changeset 还没有分析任务。">
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={generateTests}
                onChange={(event) => setGenerateTests(event.target.checked)}
              />
              启用受限测试生成（generate_tests）
            </label>
            <Button onClick={() => void handleStart()} disabled={starting} data-testid="analyze-button">
              {starting ? "触发中…" : "触发分析"}
            </Button>
          </div>
        </Card>
      ) : (
        <Card title="任务进度">
          <div data-testid="task-status">
            <TaskProgress task={task} changesetId={changesetId} onRetry={() => void handleRetry()} retrying={retrying} />
          </div>
        </Card>
      )}

      <Card
        title="影响范围"
        description={impact ? `层级上限 ${MAX_DEPTH}；变更符号 ${impact.changed_symbols.length}，受影响符号 ${impact.impacts.length}` : undefined}
        actions={impact?.truncated ? <Badge tone="orange">truncated</Badge> : null}
      >
        {!impact ? (
          <p className="text-sm text-slate-500">影响结果尚未生成。</p>
        ) : (
          <div className="space-y-4">
            {impact.truncated && (
              <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                影响结果被截断（truncated=true），仅包含部分影响，结论不完整。
              </div>
            )}
            <div>
              <h3 className="mb-2 text-sm font-semibold text-slate-800">变更符号</h3>
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                      <th className="px-3 py-2">符号</th>
                      <th className="px-3 py-2">类型</th>
                      <th className="px-3 py-2">变更</th>
                      <th className="px-3 py-2">位置</th>
                      <th className="px-3 py-2">置信</th>
                    </tr>
                  </thead>
                  <tbody>
                    {impact.changed_symbols.map((symbol) => (
                      <tr key={`${symbol.symbol_id}-${symbol.change_type}`} className="border-b border-slate-100">
                        <td className="px-3 py-2 font-mono text-xs text-slate-800">{symbol.qualified_name}</td>
                        <td className="px-3 py-2">
                          <Badge tone="neutral">{symbol.kind}</Badge>
                        </td>
                        <td className="px-3 py-2">
                          <Badge tone="blue">{symbol.change_type}</Badge>
                        </td>
                        <td className="px-3 py-2 font-mono text-xs text-slate-600">{symbol.file_path}</td>
                        <td className="px-3 py-2">
                          <Badge tone={symbol.confidence === "certain" ? "green" : "orange"}>{symbol.confidence}</Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            <div>
              <h3 className="mb-2 text-sm font-semibold text-slate-800">受影响符号</h3>
              {impact.impacts.length === 0 ? (
                <p className="text-sm text-slate-500">没有传播到其他符号。</p>
              ) : (
                <ul className="space-y-1 text-sm">
                  {impact.impacts.slice(0, 50).map((item) => (
                    <li key={item.symbol_id} className="flex flex-wrap items-center gap-2 border-b border-slate-100 py-1">
                      <Badge tone="neutral">#{item.rank}</Badge>
                      <span className="font-mono text-xs text-slate-800">{item.qualified_name}</span>
                      <span className="font-mono text-xs text-slate-500">{item.file_path}</span>
                      <Badge tone={item.is_direct ? "blue" : "neutral"}>{item.is_direct ? "直接" : "间接"}</Badge>
                      <span className="text-xs text-slate-500">距离 {item.distance}</span>
                      <Badge tone="blue">{item.analysis_source}</Badge>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}
      </Card>

      <Card
        title="影响图"
        description="按 analysis_source 着色：静态事实为蓝色，动态事实为绿色，模型建议为橙色并带“建议”徽标。"
      >
        <div data-testid="impact-graph" data-node-count={graph?.nodes.length ?? 0}>
          <ImpactGraph
            graph={graph}
            maxDepth={MAX_DEPTH}
            truncated={impact?.truncated ?? false}
            complete={impact?.complete ?? false}
          />
        </div>
      </Card>

      <Card title="测试选择" description="每个用例至少一条选择理由，四源并集去重。">
        <SelectionTable items={selections?.items ?? []} />
      </Card>

      <Card
        title="执行结果"
        description={`隔离模式与覆盖率缺失状态${coverageMissing ? "（覆盖率缺失）" : ""}`}
        actions={
          <>
            {coverageMissing && <Badge tone="red">coverage_missing</Badge>}
            {runs?.items.map((run) => (
              <Badge key={run.version_type} tone={run.isolation_mode === "docker" ? "green" : "blue"}>
                {run.version_type} · {run.isolation_mode}
              </Badge>
            ))}
          </>
        }
      >
        {!runs || runs.items.length === 0 ? (
          <p className="text-sm text-slate-500">执行结果尚未生成。</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                  <th className="px-3 py-2">版本</th>
                  <th className="px-3 py-2">状态</th>
                  <th className="px-3 py-2">退出码</th>
                  <th className="px-3 py-2">耗时</th>
                  <th className="px-3 py-2">选定/通过/失败/错误/跳过</th>
                  <th className="px-3 py-2">隔离模式</th>
                  <th className="px-3 py-2">覆盖率文件</th>
                </tr>
              </thead>
              <tbody>
                {runs.items.map((run) => (
                  <tr key={run.version_type} className="border-b border-slate-100">
                    <td className="px-3 py-2">
                      <Badge tone="neutral">{run.version_type}</Badge>
                    </td>
                    <td className="px-3 py-2">
                      <Badge tone={run.status === "passed" ? "green" : "red"}>{run.status}</Badge>
                    </td>
                    <td className="px-3 py-2 font-mono text-xs">{run.exit_code}</td>
                    <td className="px-3 py-2 text-xs">{run.duration_ms} ms</td>
                    <td className="px-3 py-2 font-mono text-xs">
                      {run.selected_count}/{run.passed_count}/{run.failed_count}/{run.error_count}/{run.skipped_count}
                    </td>
                    <td className="px-3 py-2">
                      <Badge tone={run.isolation_mode === "docker" ? "green" : "blue"}>{run.isolation_mode}</Badge>
                    </td>
                    <td className="px-3 py-2 font-mono text-xs text-slate-500">
                      {run.coverage_path ?? (coverageMissing ? "缺失" : "—")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}