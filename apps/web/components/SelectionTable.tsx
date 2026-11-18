import type { SelectionItemOut } from "@/lib/types";

const REASON_LABELS: Record<string, string> = {
  covered_change: "覆盖变更",
  covered_impact: "覆盖影响",
  static_dependency: "静态依赖",
  high_risk_guard: "高风险",
};

export function SelectionTable({ items }: { items: SelectionItemOut[] }) {
  if (items.length === 0) {
    return <p className="text-[12px] text-slate-500">暂无测试选择结果。</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[620px] table-fixed">
        <colgroup>
          <col className="w-[50%]" />
          <col className="w-[10%]" />
          <col className="w-[40%]" />
        </colgroup>
        <thead>
          <tr>
            <th className="cp-th">测试节点</th>
            <th className="cp-th">策略</th>
            <th className="cp-th">选择理由</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.test_node_id}>
              <td className="cp-td cp-mono break-all text-slate-900">{item.test_node_id}</td>
              <td className="cp-td"><span className="text-blue-700">{item.strategy}</span></td>
              <td className="cp-td">
                <ul className="space-y-1">
                  {item.reasons.map((reason, index) => (
                    <li key={`${reason.source}-${reason.ref_id ?? index}`} className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                      <span className="text-[10px] font-medium text-slate-500">{REASON_LABELS[reason.source] ?? reason.source}</span>
                      <span className="text-[11px] text-slate-600">{reason.detail}</span>
                      {reason.ref_id && <span className="cp-mono text-[10px] text-slate-400">{reason.ref_id}</span>}
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
