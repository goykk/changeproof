import { promises as fs } from "node:fs";
import path from "node:path";
import { Badge, Card, Metric, PageHeader } from "@/components/ui";
import type { StrategyReport } from "@/lib/types";

export const dynamic = "force-dynamic";

const STRATEGY_ORDER = ["full", "neighbor", "static", "coverage", "combo"];
const STRATEGY_LABELS: Record<string, string> = {
  full: "全量测试",
  neighbor: "邻域传播",
  static: "静态依赖",
  coverage: "覆盖回溯",
  combo: "组合策略",
};

function evalDirCandidates(): string[] {
  const candidates = [
    process.env.CHANGEPROOF_EVAL_DIR ?? "",
    path.resolve(process.cwd(), "../../var/eval"),
    path.resolve(process.cwd(), "var/eval"),
    path.resolve(process.cwd(), "../var/eval"),
  ];
  return candidates.filter((entry, index) => entry.length > 0 && candidates.indexOf(entry) === index);
}

async function loadReports(): Promise<{ dir: string | null; reports: StrategyReport[]; missing: boolean }> {
  for (const dir of evalDirCandidates()) {
    try {
      const files = (await fs.readdir(dir))
        .filter((file) => file.toLowerCase().endsWith(".json"))
        .filter((file) => file.toLowerCase() !== "summary.json");
      if (files.length === 0) {
        return { dir, reports: [], missing: true };
      }
      const reports: StrategyReport[] = [];
      for (const file of files.sort()) {
        const parsed = JSON.parse(await fs.readFile(path.join(dir, file), "utf-8")) as StrategyReport;
        if (!parsed || typeof parsed.strategy !== "string" || !Array.isArray(parsed.per_sample)) {
          continue;
        }
        reports.push(parsed);
      }
      reports.sort((left, right) => {
        const leftIndex = STRATEGY_ORDER.indexOf(left.strategy);
        const rightIndex = STRATEGY_ORDER.indexOf(right.strategy);
        return (leftIndex < 0 ? STRATEGY_ORDER.length : leftIndex) - (rightIndex < 0 ? STRATEGY_ORDER.length : rightIndex);
      });
      return { dir, reports, missing: false };
    } catch {
      continue;
    }
  }
  return { dir: null, reports: [], missing: true };
}

function percent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
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

export default async function EvalPage() {
  const { dir, reports, missing } = await loadReports();
  const combo = reports.find((report) => report.strategy === "combo") ?? null;
  const generatedAt = reports[0]?.generated_at;

  return (
    <div className="space-y-5">
      <PageHeader
        title="策略评测"
        description="以未检出回归为主口径，对比五种测试选择策略。"
        meta={<span className="cp-mono text-[10px] text-slate-400">{dir ?? "未找到评测目录"}</span>}
      />

      {missing ? (
        <Card title="暂无评测结果">
          <p className="text-[12px] text-slate-500">未找到 var/eval/*.json，请先运行评测脚本生成结果。</p>
        </Card>
      ) : (
        <>
          <section className="cp-panel flex flex-wrap items-center gap-x-7 gap-y-2 border-t-2 border-t-blue-500 px-5 py-3">
            <Metric label="策略" value={reports.length} />
            <Metric label="样本" value={reports[0]?.samples_total ?? 0} />
            {combo && <Metric label="combo recall" value={percent(combo.recall_at_20)} tone="blue" />}
            {combo && <Metric label="combo 回归" value={percent(combo.regression_recall)} tone="green" />}
            {generatedAt && <span className="cp-mono ml-auto text-[10px] text-slate-400">最近生成 {formatTime(generatedAt)}</span>}
          </section>

          <Card
            title="策略对比"
            actions={<span className="cp-mono text-[10px] text-slate-400">{reports.length} 种策略</span>}
            flush
            bodyClassName="overflow-x-auto"
          >
            <table className="min-w-full">
              <thead>
                <tr>
                  <th className="cp-th">策略</th>
                  <th className="cp-th text-right">样本</th>
                  <th className="cp-th text-right">recall@20</th>
                  <th className="cp-th text-right">regression_recall</th>
                  <th className="cp-th text-right">测试数降幅</th>
                  <th className="cp-th text-right">verified_rate</th>
                </tr>
              </thead>
              <tbody>
                {reports.map((report) => {
                  const isDefault = report.strategy === combo?.strategy;
                  return (
                    <tr key={report.strategy} className={isDefault ? "bg-blue-50/40 shadow-[inset_3px_0_0_#2563eb]" : ""}>
                      <td className="cp-td">
                        <div className="flex items-baseline gap-2">
                          <span className={`cp-mono text-[11px] ${isDefault ? "font-semibold text-slate-950" : "text-slate-800"}`}>
                            {report.strategy}
                          </span>
                          <span className="text-[10px] text-slate-400">{STRATEGY_LABELS[report.strategy] ?? "自定义"}</span>
                          {isDefault && <Badge tone="blue">默认</Badge>}
                        </div>
                      </td>
                      <td className="cp-td cp-mono text-right">{report.samples_total}</td>
                      <td className="cp-td cp-mono text-right text-blue-700">{percent(report.recall_at_20)}</td>
                      <td className="cp-td cp-mono text-right text-emerald-700">{percent(report.regression_recall)}</td>
                      <td className="cp-td cp-mono text-right text-amber-700">{percent(report.median_test_count_reduction)}</td>
                      <td className="cp-td cp-mono text-right text-slate-600">{percent(report.verified_rate)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Card>

          <Card title="逐样本明细" description={`${reports.reduce((sum, report) => sum + report.per_sample.length, 0)} 条记录`}>
            <div className="divide-y divide-slate-100">
              {reports.map((report) => (
                <details key={report.strategy} className="group py-2">
                  <summary className="flex cursor-pointer select-none flex-wrap items-center gap-x-3 gap-y-1 text-[11px]">
                    <span className="cp-mono font-medium text-slate-900">{report.strategy}</span>
                    <span className="text-slate-400">{report.per_sample.length} 个样本</span>
                    <span className="text-slate-400">生成于 {formatTime(report.generated_at)}</span>
                  </summary>
                  <pre className="mt-2 max-h-72 overflow-auto rounded-[4px] border border-slate-200 bg-[#f8fafc] px-3 py-2 text-[10px] leading-5 text-slate-600">
                    {JSON.stringify(report.per_sample, null, 2)}
                  </pre>
                </details>
              ))}
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
