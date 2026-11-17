import type {
  AnalyzeBody,
  ApiErrorBody,
  ChangesetOut,
  CommitListOut,
  CreateChangesetBody,
  CreateRepoBody,
  GraphOut,
  HealthOut,
  ImpactOut,
  ReportOut,
  RepositoryListOut,
  RepositoryOut,
  RunsOut,
  SelectionsOut,
  TaskOut,
} from "@/lib/types";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly detail: unknown;

  constructor(code: string, message: string, status: number, detail: unknown = null) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.detail = detail;
  }
}

function parseJson(text: string): unknown {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
  });

  const payload = parseJson(await response.text());

  if (!response.ok) {
    const body = payload as Partial<ApiErrorBody> | null;
    throw new ApiError(
      body?.code ?? `http_${response.status}`,
      body?.message ?? response.statusText,
      response.status,
      body?.detail ?? null,
    );
  }

  return payload as T;
}

function idPath(segment: string, id: string): string {
  return `${segment}/${encodeURIComponent(id)}`;
}

export function getHealth(): Promise<HealthOut> {
  return request("/api/health");
}

export function listRepos(): Promise<RepositoryListOut> {
  return request("/api/repos");
}

export function createRepo(body: CreateRepoBody): Promise<RepositoryOut> {
  return request("/api/repos", { method: "POST", body: JSON.stringify(body) });
}

export function listCommits(repoId: string, limit = 50): Promise<CommitListOut> {
  return request(`${idPath("/api/repos", repoId)}/commits?limit=${limit}`);
}

export function createChangeset(body: CreateChangesetBody): Promise<ChangesetOut> {
  return request("/api/changesets", { method: "POST", body: JSON.stringify(body) });
}

export function analyzeChangeset(id: string, body: AnalyzeBody = { generate_tests: false }): Promise<TaskOut> {
  return request(`${idPath("/api/changesets", id)}/analyze`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getTask(id: string): Promise<TaskOut> {
  return request(idPath("/api/tasks", id));
}

export function retryTask(id: string): Promise<TaskOut> {
  return request(`${idPath("/api/tasks", id)}/retry`, { method: "POST" });
}

export function getImpact(id: string): Promise<ImpactOut> {
  return request(`${idPath("/api/changesets", id)}/impact`);
}

export function getGraph(id: string, maxDepth = 3): Promise<GraphOut> {
  return request(`${idPath("/api/changesets", id)}/graph?max_depth=${maxDepth}`);
}

export function getSelections(id: string): Promise<SelectionsOut> {
  return request(`${idPath("/api/changesets", id)}/selections`);
}

export function getRuns(id: string): Promise<RunsOut> {
  return request(`${idPath("/api/changesets", id)}/runs`);
}

export function getReport(id: string): Promise<ReportOut> {
  return request(`${idPath("/api/changesets", id)}/report`);
}