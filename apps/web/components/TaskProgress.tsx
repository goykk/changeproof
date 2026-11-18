"use client";

import Link from "next/link";
import { Badge, Button, Spinner } from "@/components/ui";
import type { TaskOut } from "@/lib/types";

const STAGES = [
  "created",
  "baseline_preparing",
  "indexing",
  "analyzing",
  "test_selecting",
  "test_running",
  "test_generating",
  "validating",
  "reported",
];

const STAGE_LABELS: Record<string, string> = {
  created: "已创建",
  baseline_preparing: "基线准备",
  indexing: "代码索引",
  analyzing: "影响分析",
  test_selecting: "测试选择",
  test_running: "测试执行",
  test_generating: "测试生成",
  validating: "双版本验证",
  reported: "已报告",
  failed: "失败",
};

const STATUS_LABELS: Record<string, string> = {
  created: "已创建",
  running: "执行中",
  reported: "已完成",
  failed: "失败",
};

function statusTone(status: string): "neutral" | "blue" | "green" | "red" {
  if (status === "reported") return "green";
  if (status === "failed") return "red";
  if (status === "created") return "neutral";
  return "blue";
}

function progressPercent(task: TaskOut): number {
  if (task.status === "reported") return 100;
  const index = STAGES.indexOf(task.stage);
  if (index < 0) return 0;
  return Math.round((index / (STAGES.length - 1)) * 100);
}

export function TaskProgress({
  task,
  changesetId,
  onRetry,
  retrying = false,
}: {
  task: TaskOut | null;
  changesetId: string;
  onRetry?: () => void;
  retrying?: boolean;
}) {
  if (!task) {
    return <Spinner label="等待任务信息…" />;
  }

  const percent = progressPercent(task);
  const finished = task.status === "reported" || task.status === "failed";

  return (
    <div className="space-y-2.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <Badge tone={statusTone(task.status)}>{STATUS_LABELS[task.status] ?? task.status}</Badge>
        {task.status !== "reported" && (
          <span className="text-[11px] font-medium text-slate-700">{STAGE_LABELS[task.stage] ?? task.stage}</span>
        )}
        {task.status !== "reported" && (
          <span className="cp-mono text-[10px] text-slate-400">阶段 {Math.max(0, STAGES.indexOf(task.stage) + 1)}/{STAGES.length}</span>
        )}
        {task.retry_count > 0 && <span className="text-[10px] text-slate-400">重试 {task.retry_count}</span>}
        {!finished && <Spinner label="运行中" />}
        {task.report_available && (
          <Link href={`/changesets/${changesetId}/report`} className="text-[11px] text-blue-700 hover:underline">
            查看证据报告
          </Link>
        )}
      </div>

      {!finished && (
        <div className="h-[3px] w-full bg-slate-200">
          <div className="h-full bg-slate-800" style={{ width: `${percent}%` }} />
        </div>
      )}

      {(task.error_code || task.error_message) && (
        <div className="border-l-2 border-red-500 bg-red-50 px-3 py-2 text-[11px] leading-5 text-red-800">
          <span className="cp-mono text-[10px] font-semibold">{task.error_code ?? "error"}</span>
          {task.error_message && <span className="ml-2">{task.error_message}</span>}
        </div>
      )}

      {task.status === "failed" && onRetry && (
        <Button variant="secondary" size="sm" onClick={onRetry} disabled={retrying}>
          {retrying ? "重试中…" : "重试分析"}
        </Button>
      )}
    </div>
  );
}
