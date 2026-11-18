"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Tabs } from "@/components/Tabs";
import { Badge, Button, ErrorBanner, Metric, PageHeader, Spinner } from "@/components/ui";
import { ApiError, getReport } from "@/lib/api";
import type { ReportOut } from "@/lib/types";

type Banner = { code: string; message: string };
type ReportTab = "overview" | "static" | "risks";

function toBanner(error: unknown): Banner {
  if (error instanceof ApiError) return { code: error.code, message: error.message };
  if (error instanceof Error) return { code: "unexpected_error", message: error.message };
  return { code: "unexpected_error", message: String(error) };
}

function formatTime(value: string): string {
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

export default function ReportPage({ params }: { params: { id: string } }) {
  const [report, setReport] = useState<ReportOut | null>(null);
  const [waiting, setWaiting] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Banner | null>(null);
  const [tab, setTab] = useState<ReportTab>("overview");

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

  const tabItems = report
    ? [
        { key: "overview", label: "执行概览", badge: report.dynamic_facts.runs.length, tone: "blue" as const },
        { key: "static", label: "静态事实", badge: "JSON", tone: "blue" as const },
        { key: "risks", label: "残余风险", badge: report.residual_risks.length, tone: "red" as const },
      ]
    : [];

  return (
    <div className="space-y-4">
      <PageHeader
        title="证据报告"
        description="确定性事实、动态执行结果与残余风险分开呈现。"
        meta={<span className="cp-mono text-[10px] text-slate-500">{params.id}</span>}
        actions={
          <>
            <Link
              href={`/changesets/${params.id}`}
              className="inline-flex h-7 items-center text-[11px] font-medium text-blue-700 hover:text-blue-800"
            >
              返回影响页
            </Link>
            <Button variant="secondary" size="sm" onClick={() => void load()} disabled={loading}>
              {loading ? "刷新中…" : "刷新"}
            </Button>
          </>
        }
      />

      <ErrorBanner error={error} />

      {loading && !report && <Spinner label="加载报告…" />}

      {waiting && !report && (
        <section className="cp-panel flex flex-col gap-3 border-l-[3px] border-l-amber-500 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-3">
            <Badge tone="orange">未就绪</Badge>
            <p className="text-[12px] text-slate-500">任务仍在执行，报告生成后再刷新。</p>
          </div>
          <Button variant="secondary" size="sm" onClick={() => void load()} disabled={loading}>
            重新检查
          </Button>
        </section>
      )}

      {report && (
        <>
          <section className="cp-panel border-t-2 border-t-blue-500 px-5 py-3" data-testid="report-summary">
            <div className="flex flex-wrap items-center gap-x-8 gap-y-2">
              <Metric label="变更符号" value={report.summary.changed_symbols} tone="blue" />
              <Metric label="受影响符号" value={report.summary.impacted_symbols} tone="amber" />
              <Metric label="选中测试" value={report.summary.selected_tests} tone="green" />
              <Metric
                label="回归检出"
                value={report.summary.regression_detected ? "已检出" : "未检出"}
                tone={report.summary.regression_detected ? "red" : "green"}
              />
              <span className="cp-mono ml-auto text-[10px] text-slate-400">生成于 {formatTime(report.generated_at)}</span>
            </div>
            {(report.truncated || report.degraded_steps.length > 0) && (
              <div className="mt-2 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-2">
                {report.truncated && <Badge tone="orange">结果截断</Badge>}
                {report.degraded_steps.map((step) => (
                  <Badge key={step} tone="orange">
                    降级 · {step}
                  </Badge>
                ))}
              </div>
            )}
          </section>

          <section className="cp-panel overflow-hidden">
            <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-[#f8fafc] px-5 py-3">
              <div>
                <h2 className="text-[13px] font-semibold leading-5 text-slate-900">报告内容</h2>
                <p className="mt-0.5 text-[11px] leading-4 text-slate-500">确定性事实、动态执行与残余风险分组。</p>
              </div>
              {report.dynamic_facts.coverage_missing && <Badge tone="red">覆盖率缺失</Badge>}
            </header>

            <Tabs items={tabItems} active={tab} onChange={(key) => setTab(key as ReportTab)} />

            <div className="p-5">
              {tab === "overview" && (
                <div className="space-y-5">
                  <div>
                    <div className="mb-2 flex items-center gap-2">
                      <h3 className="text-[11px] font-semibold text-slate-800">动态事实</h3>
                      <Badge tone="blue">{report.dynamic_facts.runs.length}</Badge>
                    </div>
                    {report.dynamic_facts.runs.length === 0 ? (
                      <p className="text-[12px] text-slate-500">暂无执行记录。</p>
                    ) : (
                      <div className="overflow-x-auto rounded-[4px] border border-slate-200">
                        <table className="min-w-[620px] w-full">
                          <thead>
                            <tr>
                              <th className="cp-th">版本</th>
                              <th className="cp-th">状态</th>
                              <th className="cp-th">退出码</th>
                              <th className="cp-th">耗时</th>
                              <th className="cp-th">选定 / 通过 / 失败 / 错误 / 跳过</th>
                              <th className="cp-th">隔离</th>
                            </tr>
                          </thead>
                          <tbody>
                            {report.dynamic_facts.runs.map((run) => (
                              <tr key={run.version_type}>
                                <td className="cp-td">{run.version_type}</td>
                                <td className="cp-td whitespace-nowrap">
                                  <span className={run.status === "passed" ? "text-emerald-700" : "text-red-700"}>{run.status}</span>
                                </td>
                                <td className="cp-td cp-mono whitespace-nowrap">{run.exit_code}</td>
                                <td className="cp-td whitespace-nowrap">{run.duration_ms} ms</td>
                                <td className="cp-td cp-mono whitespace-nowrap">
                                  {run.selected_count}/{run.passed_count}/{run.failed_count}/{run.error_count}/{run.skipped_count}
                                </td>
                                <td className="cp-td whitespace-nowrap">{run.isolation_mode}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>

                  <div>
                    <div className="mb-2 flex items-center gap-2">
                      <h3 className="text-[11px] font-semibold text-slate-800">模型建议</h3>
                      <span className="text-[10px] text-slate-400">仅作补充，不参与确定性评分</span>
                    </div>
                    {report.model_suggestions.length === 0 ? (
                      <p className="text-[12px] text-slate-500">没有模型建议。</p>
                    ) : (
                      <ul className="space-y-2">
                        {report.model_suggestions.map((suggestion) => (
                          <li key={suggestion.test_node_id} className="border-l-2 border-amber-400 pl-3">
                            <div className="flex flex-wrap items-baseline gap-2">
                              <span className="cp-mono text-[11px] text-slate-800">{suggestion.test_node_id}</span>
                              <span className="text-[10px] text-slate-400">{suggestion.status}</span>
                            </div>
                            <p className="mt-1 text-[11px] leading-5 text-slate-600">{suggestion.rationale}</p>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </div>
              )}

              {tab === "static" && (
                <div>
                  <div className="mb-2 flex items-center gap-2">
                    <h3 className="text-[11px] font-semibold text-slate-800">静态事实</h3>
                    <span className="text-[10px] text-slate-400">AST、框架与 Git 历史来源的确定性结论</span>
                  </div>
                  <pre className="max-h-[360px] overflow-auto rounded-[4px] border border-slate-200 bg-[#f8fafc] px-3 py-2 text-[10px] leading-5 text-slate-600">
                    {JSON.stringify(report.static_facts, null, 2)}
                  </pre>
                </div>
              )}

              {tab === "risks" && (
                <div>
                  <div className="mb-2 flex items-center gap-2">
                    <h3 className="text-[11px] font-semibold text-slate-800">残余风险</h3>
                    <Badge tone="red">{report.residual_risks.length}</Badge>
                  </div>
                  {report.residual_risks.length === 0 ? (
                    <p className="text-[12px] text-slate-500">没有残余风险记录。</p>
                  ) : (
                    <ul className="max-h-[370px] space-y-3 overflow-y-auto pr-1">
                      {report.residual_risks.map((risk, index) => (
                        <li key={`${risk.risk}-${index}`} className="border-l-2 border-red-400 pl-3">
                          <div className="flex flex-wrap items-baseline gap-2">
                            <span className="text-[11px] font-medium text-red-700">{risk.risk}</span>
                            <span className="text-[10px] text-slate-400">{risk.source}</span>
                          </div>
                          <p className="mt-1 text-[11px] leading-5 text-slate-600">{risk.reason}</p>
                        </li>
                      ))}
                    </ul>
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
