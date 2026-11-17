"use client";

import cytoscape from "cytoscape";
import { useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui";
import type { GraphNodeOut, GraphOut } from "@/lib/types";

const STATIC_SOURCES = new Set(["ast", "framework", "git_history"]);
const DYNAMIC_SOURCES = new Set(["coverage", "execution"]);

function nodeClass(source: string): string {
  if (STATIC_SOURCES.has(source)) return "source-static";
  if (DYNAMIC_SOURCES.has(source)) return "source-dynamic";
  if (source === "model") return "source-model";
  return "source-neutral";
}

function edgeClass(source: string): string {
  if (STATIC_SOURCES.has(source)) return "edge-static";
  if (DYNAMIC_SOURCES.has(source)) return "edge-dynamic";
  if (source === "model") return "edge-model";
  return "edge-neutral";
}

type SelectedNode = Pick<GraphNodeOut, "label" | "kind" | "file_path" | "line_start" | "analysis_source">;

const STYLES: cytoscape.StylesheetJson = [
  {
    selector: "node",
    style: {
      label: "data(label)",
      "text-wrap": "wrap",
      "text-max-width": "110",
      "font-size": 8,
      "text-valign": "center",
      "text-halign": "center",
      width: 36,
      height: 36,
      "border-width": 2,
      "border-color": "#cbd5e1",
      "background-color": "#f1f5f9",
      color: "#0f172a",
    },
  },
  {
    selector: "node.source-static",
    style: { "background-color": "#2563eb", "border-color": "#1e3a8a", color: "#ffffff" },
  },
  {
    selector: "node.source-dynamic",
    style: { "background-color": "#16a34a", "border-color": "#166534", color: "#ffffff" },
  },
  {
    selector: "node.source-model",
    style: {
      "background-color": "#ea580c",
      "border-color": "#9a3412",
      "border-width": 3,
      "border-style": "double",
      color: "#ffffff",
    },
  },
  {
    selector: "node.source-neutral",
    style: { "background-color": "#e2e8f0", "border-color": "#94a3b8", color: "#0f172a" },
  },
  {
    selector: "node:selected",
    style: { "border-width": 4, "border-color": "#facc15" },
  },
  {
    selector: "edge",
    style: {
      width: 1,
      "line-color": "#cbd5e1",
      "target-arrow-color": "#cbd5e1",
      "target-arrow-shape": "triangle",
      "curve-style": "bezier",
      label: "data(label)",
      "font-size": 6,
      color: "#64748b",
      "text-rotation": "autorotate",
      "text-background-color": "#ffffff",
      "text-background-opacity": 0.85,
    },
  },
  { selector: "edge.edge-static", style: { "line-color": "#93c5fd", "target-arrow-color": "#93c5fd" } },
  { selector: "edge.edge-dynamic", style: { "line-color": "#86efac", "target-arrow-color": "#86efac" } },
  { selector: "edge.edge-model", style: { "line-color": "#fdba74", "target-arrow-color": "#fdba74" } },
  { selector: "edge.edge-neutral", style: { "line-color": "#cbd5e1", "target-arrow-color": "#cbd5e1" } },
];

export function ImpactGraph({
  graph,
  maxDepth,
  truncated,
  complete,
}: {
  graph: GraphOut | null;
  maxDepth: number;
  truncated: boolean;
  complete: boolean;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [selected, setSelected] = useState<SelectedNode | null>(null);

  useEffect(() => {
    if (!containerRef.current || !graph) return;

    const elements = [
      ...graph.nodes.map((node) => ({
        data: {
          id: node.id,
          label: node.analysis_source === "model" ? `建议 · ${node.label}` : node.label,
          kind: node.kind,
          file_path: node.file_path,
          line_start: node.line_start,
          analysis_source: node.analysis_source,
        },
        classes: nodeClass(node.analysis_source),
      })),
      ...graph.edges.map((edge, index) => ({
        data: {
          id: `e${index}:${edge.source}->${edge.target}`,
          source: edge.source,
          target: edge.target,
          label: `${edge.edge_type} · ${edge.analysis_source}`,
          edge_type: edge.edge_type,
          analysis_source: edge.analysis_source,
          confidence: edge.confidence,
          file_path: edge.file_path,
          line_start: edge.line_start,
        },
        classes: edgeClass(edge.analysis_source),
      })),
    ];

    const cy = cytoscape({
      container: containerRef.current,
      elements,
      style: STYLES,
      layout: { name: "cose", animate: false, padding: 24, nodeRepulsion: () => 8000 },
      wheelSensitivity: 0.15,
    });

    cy.on("select unselect", "node", () => {
      const selection = cy.$("node:selected");
      if (selection.length === 0) {
        setSelected(null);
        return;
      }
      const data = selection[0].data();
      setSelected({
        label: String(data.label ?? ""),
        kind: String(data.kind ?? ""),
        file_path: String(data.file_path ?? ""),
        line_start: Number(data.line_start ?? 0),
        analysis_source: String(data.analysis_source ?? ""),
      });
    });

    return () => {
      cy.destroy();
    };
  }, [graph]);

  const staticCount = graph?.nodes.filter((node) => STATIC_SOURCES.has(node.analysis_source)).length ?? 0;
  const dynamicCount = graph?.nodes.filter((node) => DYNAMIC_SOURCES.has(node.analysis_source)).length ?? 0;
  const modelCount = graph?.nodes.filter((node) => node.analysis_source === "model").length ?? 0;

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-slate-600">
        <Badge tone="blue">静态事实 {staticCount}</Badge>
        <Badge tone="green">动态事实 {dynamicCount}</Badge>
        <Badge tone="orange">模型建议 {modelCount}</Badge>
        <span>层级上限 {maxDepth}</span>
        <span>节点 {graph?.nodes.length ?? 0} · 边 {graph?.edges.length ?? 0}</span>
      </div>

      {truncated && (
        <div className="mb-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          影响结果已截断：仅展示当前层级上限（{maxDepth}）内的部分节点，结论不完整。
        </div>
      )}
      {!truncated && complete && (
        <div className="mb-3 rounded-md border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-700">
          影响传播已完整收敛。
        </div>
      )}

      {!graph || graph.nodes.length === 0 ? (
        <p className="text-sm text-slate-500">暂无影响图数据。</p>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[1fr_260px]">
          <div ref={containerRef} className="h-[480px] w-full rounded-md border border-slate-200 bg-slate-50" />
          <aside className="rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
            {selected ? (
              <div className="space-y-2">
                <div className="font-semibold text-slate-900">{selected.label}</div>
                <div className="flex items-center gap-2">
                  <Badge tone="neutral">{selected.kind}</Badge>
                  <Badge tone={nodeClass(selected.analysis_source) === "source-model" ? "orange" : "blue"}>
                    {selected.analysis_source}
                  </Badge>
                  {selected.analysis_source === "model" && <Badge tone="orange">建议</Badge>}
                </div>
                <div className="font-mono text-xs text-slate-700">
                  {selected.file_path}:{selected.line_start}
                </div>
              </div>
            ) : (
              <p className="text-slate-500">点击节点查看代码位置与事实来源。</p>
            )}
            <div className="mt-4 space-y-1 border-t border-slate-200 pt-3 text-xs text-slate-500">
              <p>蓝 = 静态事实（ast / framework / git_history）</p>
              <p>绿 = 动态事实（coverage / execution）</p>
              <p>橙 = 模型建议（带“建议”徽标）</p>
              <p>边标签 = 边类型 · 事实来源</p>
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}