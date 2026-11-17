import { promises as fs } from "node:fs";
import path from "node:path";
import { Badge, Card } from "@/components/ui";
import type { StrategyReport } from "@/lib/types";

export const dynamic = "force-dynamic";

const STRATEGY_ORDER = ["full", "neighbor", "static", "coverage", "combo"];

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
        // summary.json 等汇总文件不含 strategy/per_sample，必须跳过，避免渲染期异常。
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

function MetricBar({ value, tone }: { value: number; tone: string }) {
  const width = Math.min(100, Math.max(0, Math.round(value * 100)));
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 w-24 overflow-hidden rounded-full bg-slate-200">
        <div className={`h-full rounded-full ${tone}`} style={{ width: `${width}%` }} />
      </div>
      <span className="font-mono text-xs text-slate-600">{percent(value)}</span>
    </div>
  );
}

export default async function EvalPage() {
  const { dir, reports, missing } = await loadReports();

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900">策略评测</h1>
        <p className="mt-1 text-sm text-slate-500">
          读取 <code className="font-mono text-xs">var/eval/*.json</code>，对比 full / neighbor / static / coverage / combo 五策略。
        </p>
        {dir && <p className="mt-1 font-mono text-xs text-slate-400">{dir}</p>}
      </header>

      {missing ? (
        <Card title="暂无评测结果">
          <p className="text-sm text-slate-500">
            未找到 <code className="font-mono text-xs">var/eval/*.json</code>，请先运行评测脚本生成结果。
          </p>
        </Card>
      ) : (
        <>
          <Card title="策略对比" description="统一口径指标：recall@20、regression_recall、中位测试数降幅、verified_rate。">
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                    <th className="px-3 py-2">策略</th>
                    <th className="px-3 py-2">样本数</th>
                    <th className="px-3 py-2">recall@20</th>
                    <th className="px-3 py-2">regression_recall</th>
                    <th className="px-3 py-2">中位测试数降幅</th>
                    <th className="px-3 py-2">verified_rate</th>
                    <th className="px-3 py-2">生成时间</th>
                  </tr>
                </thead>
                <tbody>
                  {reports.map((report) => (
                    <tr key={report.strategy} className="border-b border-slate-100 align-middle">
                      <td className="px-3 py-2">
                        <Badge tone="blue">{report.strategy}</Badge>
                      </td>
                      <td className="px-3 py-2 font-mono text-xs">{report.samples_total}</td>
                      <td className="px-3 py-2">
                        <MetricBar value={report.recall_at_20} tone="bg-blue-500" />
                      </td>
                      <td className="px-3 py-2">
                        <MetricBar value={report.regression_recall} tone="bg-green-500" />
                      </td>
                      <td className="px-3 py-2">
                        <MetricBar value={report.median_test_count_reduction} tone="bg-purple-500" />
                      </td>
                      <td className="px-3 py-2">
                        <MetricBar value={report.verified_rate} tone="bg-orange-500" />
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-slate-500">{report.generated_at}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <Card title="逐样本明细">
            <div className="space-y-3">
              {reports.map((report) => (
                <details key={report.strategy} className="rounded-md border border-slate-200 bg-slate-50 p-3">
                  <summary className="cursor-pointer text-sm font-medium text-slate-800">
                    {report.strategy} · {report.per_sample.length} 个样本
                  </summary>
                  <pre className="mt-2 max-h-72 overflow-auto rounded-md bg-slate-900 p-3 text-xs text-slate-100">
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