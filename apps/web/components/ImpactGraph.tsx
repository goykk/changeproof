"use client";

import cytoscape from "cytoscape";
import { useEffect, useRef, useState } from "react";
import type { GraphNodeOut, GraphOut } from "@/lib/types";

const STATIC_SOURCES = new Set(["ast", "framework", "git_history"]);
const DYNAMIC_SOURCES = new Set(["coverage", "execution"]);

const EDGE_LABELS: Record<string, string> = {
  routes_to: "路由",
  calls: "调用",
  imports: "导入",
  defines: "定义",
  reads: "读取",
  writes: "写入",
  references: "引用",
  tests: "测试",
  related: "关联",
};

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

function shortLabel(value: string): string {
  const base = value.split("/").pop() ?? value;
  const concise = base.replace(/\.py$/, "").replace(/^test_/, "");
  return concise.length > 12 ? `${concise.slice(0, 11)}…` : concise;
}

function edgeLabel(edgeType: string): string {
  return EDGE_LABELS[edgeType] ?? edgeType;
}

type SelectedNode = Pick<GraphNodeOut, "label" | "kind" | "file_path" | "line_start" | "analysis_source">;

const STYLES: cytoscape.StylesheetJson = [
  {
    selector: "node",
    style: {
      label: "data(label)",
      "text-wrap": "wrap",
      "text-max-width": "70",
      "font-size": 11,
      "text-valign": "center",
      "text-halign": "center",
      width: 80,
      height: 80,
      "border-width": 2.5,
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
      width: 2.2,
      "line-color": "#cbd5e1",
      "target-arrow-color": "#cbd5e1",
      "target-arrow-shape": "triangle",
      "arrow-scale": 2,
      "curve-style": "straight",
      label: "data(label)",
      "font-size": 11,
      color: "#1e293b",
      "text-rotation": "none",
      "text-background-color": "#ffffff",
      "text-background-opacity": 0.92,
      "text-background-padding": "1px",
    },
  },
  { selector: "edge.edge-static", style: { "line-color": "#60a5fa", "target-arrow-color": "#1d4ed8" } },
  { selector: "edge.edge-dynamic", style: { "line-color": "#4ade80", "target-arrow-color": "#15803d" } },
  { selector: "edge.edge-model", style: { "line-color": "#fb923c", "target-arrow-color": "#c2410c" } },
  { selector: "edge.edge-neutral", style: { "line-color": "#94a3b8", "target-arrow-color": "#475569" } },
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
    if (!containerRef.current || !graph || graph.nodes.length === 0) return;

    const elements = [
      ...graph.nodes.map((node) => ({
        data: {
          id: node.id,
          label: shortLabel(node.label),
          full_label: node.label,
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
          label: edgeLabel(edge.edge_type),
          label_offset: ((index % 5) - 2) * 7,
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
      layout: {
        name: "cose",
        animate: false,
        fit: false,
        padding: 18,
        nodeRepulsion: () => 7000,
        idealEdgeLength: () => 64,
        edgeElasticity: () => 100,
        componentSpacing: 18,
        nodeOverlap: 12,
        gravity: 0.9,
      },
      wheelSensitivity: 0.15,
    });

    cy.edges().forEach((edge) => {
      edge.style({ "text-margin-y": Number(edge.data("label_offset") ?? 0) });
    });
    const centerNode = cy.nodes().sort((a, b) => b.degree() - a.degree()).first() as cytoscape.NodeSingular;
    centerNode.select();
    cy.zoom(1);
    cy.center(centerNode);

    const showConnectedLabels = (node: cytoscape.NodeSingular) => {
      cy.edges().style({ opacity: 1, width: 2.2, "font-size": 11 });
      node.connectedEdges().style({ opacity: 1, width: 3, "font-size": 12 });
    };

    cy.on("select unselect", "node", () => {
      const selection = cy.$("node:selected");
      if (selection.length === 0) {
        cy.edges().style({ opacity: 1, width: 2.2, "font-size": 11 });
        setSelected(null);
        return;
      }
      const data = selection[0].data();
      setSelected({
        label: String(data.full_label ?? data.label ?? ""),
        kind: String(data.kind ?? ""),
        file_path: String(data.file_path ?? ""),
        line_start: Number(data.line_start ?? 0),
        analysis_source: String(data.analysis_source ?? ""),
      });
      showConnectedLabels(selection[0] as cytoscape.NodeSingular);
    });

    cy.on("mouseover", "edge", (event) => {
      event.target.style({ opacity: 1, width: 3.2, "font-size": 13 });
    });

    cy.on("mouseout", "edge", () => {
      const selection = cy.$("node:selected");
      if (selection.length === 0) {
        cy.edges().style({ opacity: 1, width: 2.2, "font-size": 11 });
        return;
      }
      cy.edges().style({ opacity: 1, width: 2.2, "font-size": 11 });
      selection.connectedEdges().style({ opacity: 1, width: 3, "font-size": 12 });
    });

    const resize = () => {
      cy.resize();
      const selection = cy.$("node:selected");
      if (selection.length > 0) {
        cy.center(selection.first() as cytoscape.NodeSingular);
      } else {
        cy.center();
      }
    };
    window.addEventListener("resize", resize);

    return () => {
      window.removeEventListener("resize", resize);
      cy.destroy();
    };
  }, [graph]);

  const staticCount = graph?.nodes.filter((node) => STATIC_SOURCES.has(node.analysis_source)).length ?? 0;
  const dynamicCount = graph?.nodes.filter((node) => DYNAMIC_SOURCES.has(node.analysis_source)).length ?? 0;
  const modelCount = graph?.nodes.filter((node) => node.analysis_source === "model").length ?? 0;

  return (
    <div>
      {truncated && (
        <div className="mb-3 border-l-2 border-amber-500 bg-amber-50 px-3 py-2 text-[11px] leading-5 text-amber-800">
          结果已截断，仅展示当前层级内的部分节点。
        </div>
      )}

      {!graph || graph.nodes.length === 0 ? (
        <p className="text-[12px] text-slate-500">暂无影响图数据。</p>
      ) : (
        <div className="grid gap-3 md:grid-cols-[1fr_200px]">
          <div ref={containerRef} className="h-[400px] w-full rounded-[4px] border border-slate-200 bg-[#fbfcfd]" />
          <aside className="overflow-y-auto rounded-[4px] border border-slate-200 bg-white px-3 py-2.5 text-[11px]">
            <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-slate-500">
              <span className="inline-flex items-center gap-1"><span className="h-2 w-2 bg-blue-600" />静态 {staticCount}</span>
              <span className="inline-flex items-center gap-1"><span className="h-2 w-2 bg-emerald-600" />动态 {dynamicCount}</span>
              <span className="inline-flex items-center gap-1"><span className="h-2 w-2 bg-orange-500" />建议 {modelCount}</span>
            </div>
            {selected ? (
              <div>
                <div className="break-all font-medium text-slate-900">{selected.label}</div>
                <div className="mt-1 flex flex-wrap items-baseline gap-x-2 gap-y-1 text-[10px] text-slate-500">
                  <span>{selected.kind}</span>
                  <span>{selected.analysis_source}</span>
                  {selected.analysis_source === "model" && <span className="text-amber-700">建议</span>}
                </div>
                <div className="cp-mono mt-2 break-all text-[10px] leading-5 text-slate-600">
                  {selected.file_path}:{selected.line_start}
                </div>
              </div>
            ) : (
              <p className="text-slate-500">点击节点，或悬停箭头，查看对应的箭头注释。</p>
            )}
            <div className="mt-3 space-y-1 border-t border-slate-100 pt-2 text-[10px] leading-5 text-slate-500">
              <p>蓝：静态事实</p>
              <p>绿：动态事实</p>
              <p>橙：模型建议</p>
              <p>箭头注释 = 关系类型</p>
              <p>拖动平移 · 滚轮缩放</p>
              <p className="cp-mono pt-1">节点 {graph.nodes.length} · 边 {graph.edges.length} · 注释 {graph.edges.length} · 层级 {maxDepth}</p>
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}
