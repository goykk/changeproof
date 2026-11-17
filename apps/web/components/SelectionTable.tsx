import { Badge, type BadgeTone } from "@/components/ui";
import type { SelectionItemOut } from "@/lib/types";

const REASON_TONES: Record<string, BadgeTone> = {
  covered_change: "green",
  covered_impact: "green",
  static_dependency: "blue",
  high_risk_guard: "purple",
};

export function SelectionTable({ items }: { items: SelectionItemOut[] }) {
  if (items.length === 0) {
    return <p className="text-sm text-slate-500">暂无测试选择结果。</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-sm">
        <thead>
          <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
            <th className="px-3 py-2">测试节点</th>
            <th className="px-3 py-2">策略</th>
            <th className="px-3 py-2">选择理由</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.test_node_id} className="border-b border-slate-100 align-top">
              <td className="px-3 py-2 font-mono text-xs text-slate-800">{item.test_node_id}</td>
              <td className="px-3 py-2">
                <Badge tone="neutral">{item.strategy}</Badge>
              </td>
              <td className="px-3 py-2">
                <ul className="space-y-1">
                  {item.reasons.map((reason, index) => (
                    <li key={`${reason.source}-${reason.ref_id ?? index}`} className="flex flex-wrap items-center gap-1">
                      <Badge tone={REASON_TONES[reason.source] ?? "neutral"}>{reason.source}</Badge>
                      <span className="text-slate-700">{reason.detail}</span>
                      {reason.ref_id && <span className="font-mono text-xs text-slate-400">{reason.ref_id}</span>}
                    </li>
                  ))}
                </ul>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}