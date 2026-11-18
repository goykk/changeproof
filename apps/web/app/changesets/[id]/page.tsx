"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { ImpactGraph } from "@/components/ImpactGraph";
import { SelectionTable } from "@/components/SelectionTable";
import { Tabs } from "@/components/Tabs";
import { TaskProgress } from "@/components/TaskProgress";
import { Badge, Button, ErrorBanner, Metric, PageHeader, Spinner } from "@/components/ui";
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
import type { GraphOut, ImpactOut, ReportOut, RunsOut, SelectionsOut, TaskOut } from "@/lib/types";

const MAX_DEPTH = 3;
const POLL_INTERVAL_MS = 2000;

type Banner = { code: string; message: string };
type DetailTab = "impact" | "graph" | "tests" | "runs";

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
  const [reportSummary, setReportSummary] = useState<ReportOut["summary"] | null>(null);
  const [coverageMissing, setCoverageMissing] = useState(false);
  const [artifactsLoading, setArtifactsLoading] = useState(false);
  const [starting, setStarting] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [generateTests, setGenerateTests] = useState(false);
  const [error, setError] = useState<Banner | null>(null);
  const [tab, setTab] = useState<DetailTab>("impact");

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
    if (reportResult.status === "fulfilled") {
      setCoverageMissing(reportResult.value.dynamic_facts.coverage_missing);
      setReportSummary(reportResult.value.summary);
    }

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

  const changedCount = impact?.changed_symbols.length ?? 0;
  const impactedCount = impact?.impacts.length ?? 0;
  const selectedCount = selections?.items.length ?? 0;
  const regression = reportSummary ? (reportSummary.regression_detected ? "已检出" : "未检出") : "—";
  const hasArtifacts =
    Boolean(impact && (impact.changed_symbols.length > 0 || impact.impacts.length > 0)) ||
    Boolean(graph && graph.nodes.length > 0) ||
    Boolean(selections && selections.items.length > 0) ||
    Boolean(runs && runs.items.length > 0);
  const failedRun = runs?.items.some((run) => run.status !== "passed") ?? false;

  const tabItems = [
    { key: "impact", label: "影响范围", badge: `${changedCount}/${impactedCount}`, tone: "blue" as const },
    { key: "graph", label: "影响图", badge: graph?.nodes.length ?? 0, tone: "blue" as const },
    { key: "tests", label: "测试选择", badge: selectedCount, tone: "green" as const },
    { key: "runs", label: "执行结果", badge: runs?.items.length ?? 0, tone: failedRun ? ("red" as const) : ("green" as const) },
  ];

  return (
    <div className="space-y-4">
      <PageHeader
        title="变更影响"
        description="定位提交之间的影响范围、测试选择与双版本执行结果。"
        meta={<span className="cp-mono text-[10px] text-slate-500">{changesetId}</span>}
        actions={
          <>
            <Link
              href={`/changesets/${changesetId}/report`}
              className="inline-flex h-7 items-center text-[11px] font-medium text-blue-700 hover:text-blue-800"
            >
              证据报告
            </Link>
            <Button variant="secondary" size="sm" onClick={() => void loadArtifacts()} disabled={artifactsLoading}>
              {artifactsLoading ? "刷新中…" : "刷新"}
            </Button>
          </>
        }
      />

      <ErrorBanner error={error} />

      {!taskId ? (
        <section className="cp-panel flex flex-col gap-4 border-l-[3px] border-l-blue-500 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-[13px] font-semibold text-slate-900">尚未分析</h2>
              <Badge>未运行</Badge>
            </div>
            <p className="mt-1 text-[11px] text-slate-500">运行一次分析，生成影响范围、影响图、测试选择与执行结果。</p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-[11px] text-slate-600">
              <input
                type="checkbox"
                className="h-3.5 w-3.5 rounded-none accent-blue-600"
                checked={generateTests}
                onChange={(event) => setGenerateTests(event.target.checked)}
              />
              生成受限测试
            </label>
            <Button size="lg" className="min-w-[104px]" onClick={() => void handleStart()} disabled={starting} data-testid="analyze-button">
              {starting ? "启动中…" : "开始分析"}
            </Button>
          </div>
        </section>
      ) : (
        <section className="cp-panel px-5 py-4" data-testid="task-status">
          <TaskProgress task={task} changesetId={changesetId} onRetry={() => void handleRetry()} retrying={retrying} />
        </section>
      )}

      {hasArtifacts && (
        <>
          {impact && (
            <section className="cp-panel flex flex-wrap items-center gap-x-6 gap-y-2 border-t-2 border-t-blue-500 px-5 py-2.5">
              <Metric label="变更符号" value={changedCount} tone="blue" />
              <Metric label="受影响符号" value={impactedCount} tone="amber" />
              <Metric label="选中测试" value={selectedCount} tone="green" />
              <Metric label="回归检出" value={regression} tone={reportSummary?.regression_detected ? "red" : "green"} />
              <span className="cp-mono ml-auto text-[10px] text-slate-400">层级上限 {MAX_DEPTH}</span>
            </section>
          )}

          <section className="cp-panel overflow-hidden">
            <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-[#f8fafc] px-5 py-3">
              <div>
                <h2 className="text-[13px] font-semibold leading-5 text-slate-900">分析结果</h2>
                <p className="mt-0.5 text-[11px] leading-4 text-slate-500">按影响范围、影响图、测试与执行分组。</p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {impact?.truncated && <Badge tone="orange">结果截断</Badge>}
                {coverageMissing && <Badge tone="red">覆盖率缺失</Badge>}
              </div>
            </header>

            <Tabs items={tabItems} active={tab} onChange={(key) => setTab(key as DetailTab)} />

            <div className={tab === "graph" ? "max-h-[440px] overflow-y-auto p-5" : "max-h-[430px] overflow-y-auto p-5"}>
              {tab === "impact" && (
                <div className="space-y-5">
                  {!impact ? (
                    <p className="text-[12px] text-slate-500">分析完成后生成变更符号与传播范围。</p>
                  ) : (
                    <>
                      {impact.truncated && (
                        <div className="border-l-2 border-amber-500 bg-amber-50 px-3 py-2 text-[11px] leading-5 text-amber-800">
                          结果被截断，仅展示当前层级内的部分影响，结论不完整。
                        </div>
                      )}

                      <div>
                        <div className="mb-2 flex items-center gap-2">
                          <h3 className="text-[11px] font-semibold text-slate-800">变更符号</h3>
                          <Badge tone="blue">{changedCount}</Badge>
                        </div>
                        {impact.changed_symbols.length === 0 ? (
                          <p className="text-[12px] text-slate-500">未识别到变更符号。</p>
                        ) : (
                          <div className="overflow-x-auto rounded-[4px] border border-slate-200">
                            <table className="min-w-full">
                              <thead>
                                <tr>
                                  <th className="cp-th">符号</th>
                                  <th className="cp-th">类型</th>
                                  <th className="cp-th">变更</th>
                                  <th className="cp-th">位置</th>
                                  <th className="cp-th">置信</th>
                                </tr>
                              </thead>
                              <tbody>
                                {impact.changed_symbols.map((symbol) => (
                                  <tr key={`${symbol.symbol_id}-${symbol.change_type}`}>
                                    <td className="cp-td cp-mono text-slate-900">{symbol.qualified_name}</td>
                                    <td className="cp-td">{symbol.kind}</td>
                                    <td className="cp-td text-amber-700">{symbol.change_type}</td>
                                    <td className="cp-td cp-mono text-slate-500">{symbol.file_path}</td>
                                    <td className="cp-td">{symbol.confidence}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        )}
                      </div>

                      <div>
                        <div className="mb-2 flex items-center gap-2">
                          <h3 className="text-[11px] font-semibold text-slate-800">受影响符号</h3>
                          <Badge tone="blue">{impactedCount}</Badge>
                        </div>
                        {impact.impacts.length === 0 ? (
                          <p className="text-[12px] text-slate-500">没有传播到其他符号。</p>
                        ) : (
                          <div className="overflow-x-auto rounded-[4px] border border-slate-200">
                            <table className="min-w-[600px] w-full">
                              <thead>
                                <tr>
                                  <th className="cp-th sticky top-0 z-10">#</th>
                                  <th className="cp-th sticky top-0 z-10">符号</th>
                                  <th className="cp-th sticky top-0 z-10">文件</th>
                                  <th className="cp-th sticky top-0 z-10">关系</th>
                                  <th className="cp-th sticky top-0 z-10">来源</th>
                                </tr>
                              </thead>
                              <tbody>
                                {impact.impacts.slice(0, 50).map((item) => (
                                  <tr key={item.symbol_id}>
                                    <td className="cp-td cp-mono text-slate-400">{item.rank}</td>
                                    <td className="cp-td cp-mono text-slate-900">{item.qualified_name}</td>
                                    <td className="cp-td cp-mono text-[10px] text-slate-500">{item.file_path}</td>
                                    <td className="cp-td whitespace-nowrap">
                                      <span className={item.is_direct ? "text-blue-700" : "text-slate-500"}>
                                        {item.is_direct ? "直接" : "间接"}
                                      </span>
                                      <span className="ml-1 text-[10px] text-slate-400">距离 {item.distance}</span>
                                    </td>
                                    <td className="cp-td cp-mono text-[10px] text-slate-400">{item.analysis_source}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        )}
                      </div>
                    </>
                  )}
                </div>
              )}

              {tab === "graph" && (
                <div>
                  {!graph ? (
                    <p className="text-[12px] text-slate-500">分析完成后生成影响图。</p>
                  ) : (
                    <div data-testid="impact-graph" data-node-count={graph.nodes.length}>
                      <ImpactGraph
                        graph={graph}
                        maxDepth={MAX_DEPTH}
                        truncated={impact?.truncated ?? false}
                        complete={impact?.complete ?? false}
                      />
                    </div>
                  )}
                </div>
              )}

              {tab === "tests" && (
                <div className="max-h-[380px] overflow-auto rounded-[4px] border border-slate-200">
                  <SelectionTable items={selections?.items ?? []} />
                </div>
              )}

              {tab === "runs" && (
                <div className="space-y-3">
                  {coverageMissing && (
                    <div className="border-l-2 border-red-400 bg-red-50 px-3 py-2 text-[11px] text-red-800">
                      覆盖率文件缺失，动态执行结论可能不完整。
                    </div>
                  )}
                  {!runs || runs.items.length === 0 ? (
                    <p className="text-[12px] text-slate-500">执行结果尚未生成。</p>
                  ) : (
                    <div className="overflow-x-auto rounded-[4px] border border-slate-200">
                      <table className="min-w-[700px] w-full">
                        <thead>
                          <tr>
                            <th className="cp-th">版本</th>
                            <th className="cp-th">状态</th>
                            <th className="cp-th">退出码</th>
                            <th className="cp-th">耗时</th>
                            <th className="cp-th">选定 / 通过 / 失败 / 错误 / 跳过</th>
                            <th className="cp-th">隔离</th>
                            <th className="cp-th">覆盖率文件</th>
                          </tr>
                        </thead>
                        <tbody>
                          {runs.items.map((run) => (
                            <tr key={run.version_type}>
                              <td className="cp-td">{run.version_type}</td>
                              <td className="cp-td">
                                <span className={run.status === "passed" ? "text-emerald-700" : "text-red-700"}>{run.status}</span>
                              </td>
                              <td className="cp-td cp-mono">{run.exit_code}</td>
                              <td className="cp-td">{run.duration_ms} ms</td>
                              <td className="cp-td cp-mono">
                                {run.selected_count}/{run.passed_count}/{run.failed_count}/{run.error_count}/{run.skipped_count}
                              </td>
                              <td className="cp-td">{run.isolation_mode}</td>
                              <td className="cp-td cp-mono text-slate-500">{run.coverage_path ?? (coverageMissing ? "缺失" : "—")}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}
            </div>
          </section>
        </>
      )}
    </div>
  );
}
