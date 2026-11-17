"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Badge, Button, Card, ErrorBanner, Spinner } from "@/components/ui";
import { ApiError, getReport } from "@/lib/api";
import type { ReportOut } from "@/lib/types";

type Banner = { code: string; message: string };

function toBanner(error: unknown): Banner {
  if (error instanceof ApiError) return { code: error.code, message: error.message };
  if (error instanceof Error) return { code: "unexpected_error", message: error.message };
  return { code: "unexpected_error", message: String(error) };
}

export default function ReportPage({ params }: { params: { id: string } }) {
  const [report, setReport] = useState<ReportOut | null>(null);
  const [waiting, setWaiting] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Banner | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getReport(params.id);
      setReport(data);
      setWaiting(false);
    } catch (caught) {
      if (caught instanceof ApiError && (caught.code === "report_not_ready" || caught.code === "not_found")) {
        setReport(null);
        setWaiting(true);
      } else {
        setError(toBanner(caught));
      }
    } finally {
      setLoading(false);
    }
  }, [params.id]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">证据报告</h1>
          <p className="mt-1 font-mono text-xs text-slate-500">{params.id}</p>
        </div>
        <div className="flex items-center gap-2">
          <Link
            href={`/changesets/${params.id}`}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-100"
          >
            返回影响页
          </Link>
          <Button variant="secondary" onClick={() => void load()} disabled={loading}>
            {loading ? "刷新中…" : "刷新"}
          </Button>
        </div>
      </header>

      <ErrorBanner error={error} />

      {loading && !report && <Spinner label="加载报告…" />}

      {waiting && !report && (
        <Card title="报告尚未就绪" description="任务仍在执行，报告生成后再刷新查看。">
          <div className="flex items-center gap-3">
            <Badge tone="orange">report_not_ready</Badge>
            <Button variant="secondary" onClick={() => void load()} disabled={loading}>
              重新检查
            </Button>
          </div>
        </Card>
      )}

      {report && (
        <>
          <div data-testid="report-summary">
            <Card title="摘要" description={`生成时间 ${report.generated_at}`}>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
                  <div className="text-xs text-slate-500">变更符号</div>
                  <div className="text-xl font-semibold text-slate-900">{report.summary.changed_symbols}</div>
                </div>
                <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
                  <div className="text-xs text-slate-500">受影响符号</div>
                  <div className="text-xl font-semibold text-slate-900">{report.summary.impacted_symbols}</div>
                </div>
                <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
                  <div className="text-xs text-slate-500">选中测试</div>
                  <div className="text-xl font-semibold text-slate-900">{report.summary.selected_tests}</div>
                </div>
                <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
                  <div className="text-xs text-slate-500">回归检出</div>
                  <div className="mt-1">
                    <Badge tone={report.summary.regression_detected ? "red" : "green"}>
                      {report.summary.regression_detected ? "已检出" : "未检出"}
                    </Badge>
                  </div>
                </div>
              </div>

              <div className="mt-3 flex flex-wrap items-center gap-2">
                {report.truncated && <Badge tone="orange">truncated</Badge>}
                {report.degraded_steps.map((step) => (
                  <Badge key={step} tone="orange">
                    降级步骤：{step}
                  </Badge>
                ))}
              </div>
            </Card>
        </div>

          <Card title="静态事实" description="ast / framework / git_history 来源的确定性结论。">
            <pre className="max-h-80 overflow-auto rounded-md bg-slate-900 p-3 text-xs text-slate-100">
              {JSON.stringify(report.static_facts, null, 2)}
            </pre>
          </Card>

          <Card
            title="动态事实"
            description="coverage / execution 来源的执行结论。"
            actions={report.dynamic_facts.coverage_missing ? <Badge tone="red">coverage_missing</Badge> : null}
          >
            {report.dynamic_facts.runs.length === 0 ? (
              <p className="text-sm text-slate-500">暂无执行记录。</p>
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
                      <th className="px-3 py-2">日志</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.dynamic_facts.runs.map((run) => (
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
                        <td className="px-3 py-2 font-mono text-xs text-slate-500">{run.log_path}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <Card title="模型建议" description="analysis_source=model，仅为建议，不参与确定性分数计算。">
            {report.model_suggestions.length === 0 ? (
              <p className="text-sm text-slate-500">没有模型建议。</p>
            ) : (
              <ul className="space-y-2 text-sm">
                {report.model_suggestions.map((suggestion) => (
                  <li key={suggestion.test_node_id} className="rounded-md border border-orange-200 bg-orange-50 p-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone="orange">建议</Badge>
                      <span className="font-mono text-xs text-slate-800">{suggestion.test_node_id}</span>
                      <Badge tone="neutral">{suggestion.status}</Badge>
                      <Badge tone="orange">{suggestion.analysis_source}</Badge>
                    </div>
                    <p className="mt-1 text-slate-700">{suggestion.rationale}</p>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <div data-testid="report-risks">
              <Card title="残余风险">
              {report.residual_risks.length === 0 ? (
                <p className="text-sm text-slate-500">没有残余风险记录。</p>
              ) : (
                <ul className="space-y-2 text-sm">
                  {report.residual_risks.map((risk, index) => (
                    <li key={`${risk.risk}-${index}`} className="rounded-md border border-red-200 bg-red-50 p-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge tone="red">{risk.risk}</Badge>
                        <Badge tone="neutral">{risk.source}</Badge>
                      </div>
                      <p className="mt-1 text-slate-700">{risk.reason}</p>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
