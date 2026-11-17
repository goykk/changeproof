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
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={statusTone(task.status)}>{task.status}</Badge>
        <Badge tone="neutral">{STAGE_LABELS[task.stage] ?? task.stage}</Badge>
        <span className="text-xs text-slate-500">重试次数 {task.retry_count}</span>
        {!finished && <Spinner label="轮询中（2000ms）" />}
        {task.report_available && (
          <Link href={`/changesets/${changesetId}/report`} className="text-sm text-blue-600 hover:underline">
            查看证据报告
          </Link>
        )}
      </div>

      <div className="h-2 w-full overflow-hidden rounded-full bg-slate-200">
        <div
          className={`h-full rounded-full transition-all ${task.status === "failed" ? "bg-red-500" : "bg-blue-500"}`}
          style={{ width: `${percent}%` }}
        />
      </div>

      {(task.error_code || task.error_message) && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          <span className="font-mono text-xs font-semibold">{task.error_code ?? "error"}</span>
          {task.error_message && <span className="ml-2">{task.error_message}</span>}
        </div>
      )}

      {task.status === "failed" && onRetry && (
        <Button variant="secondary" onClick={onRetry} disabled={retrying}>
          {retrying ? "重试中…" : "重试分析"}
        </Button>
      )}
    </div>
  );
}