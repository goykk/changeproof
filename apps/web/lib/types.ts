export type HealthOut = {
  status: string;
  storage: string;
  isolation_mode: string;
  llm: string;
};

export type RepositoryOut = {
  repo_id: string;
  name: string;
  local_path: string;
  default_branch: string;
  status: string;
};

export type RepositoryListOut = {
  items: RepositoryOut[];
};

export type CommitOut = {
  repo_id: string;
  sha: string;
  short_sha: string;
  message: string;
  author: string;
  committed_at: string;
};

export type CommitListOut = {
  items: CommitOut[];
};

export type ChangesetOut = {
  changeset_id: string;
  repo_id: string;
  base_sha: string;
  target_sha: string;
  status: string;
};

export type TaskOut = {
  task_id: string;
  changeset_id: string;
  status: string;
  stage: string;
  retry_count: number;
  error_code: string | null;
  error_message: string | null;
  report_available?: boolean;
};

export type ChangedSymbolOut = {
  symbol_id: string;
  kind: string;
  qualified_name: string;
  file_path: string;
  change_type: string;
  diff_ranges: Array<[number, number]>;
  confidence: string;
};

export type ImpactItemOut = {
  symbol_id: string;
  kind: string;
  qualified_name: string;
  file_path: string;
  distance: number;
  rank: number;
  confidence: string;
  analysis_source: string;
  is_direct: boolean;
  path: string[];
};

export type ImpactOut = {
  changed_symbols: ChangedSymbolOut[];
  impacts: ImpactItemOut[];
  truncated: boolean;
  complete: boolean;
};

export type GraphNodeOut = {
  id: string;
  label: string;
  kind: string;
  file_path: string;
  line_start: number;
  analysis_source: string;
};

export type GraphEdgeOut = {
  source: string;
  target: string;
  edge_type: string;
  analysis_source: string;
  confidence: string;
  file_path: string;
  line_start: number;
};

export type GraphOut = {
  nodes: GraphNodeOut[];
  edges: GraphEdgeOut[];
};

export type SelectionReasonOut = {
  source: string;
  detail: string;
  ref_id: string | null;
};

export type SelectionItemOut = {
  test_node_id: string;
  strategy: string;
  reasons: SelectionReasonOut[];
};

export type SelectionsOut = {
  items: SelectionItemOut[];
};

export type RunOut = {
  version_type: string;
  status: string;
  exit_code: number;
  duration_ms: number;
  selected_count: number;
  passed_count: number;
  failed_count: number;
  error_count: number;
  skipped_count: number;
  isolation_mode: string;
  log_path: string;
  coverage_path: string | null;
};

export type RunsOut = {
  items: RunOut[];
};

export type ModelSuggestionOut = {
  test_node_id: string;
  status: string;
  rationale: string;
  analysis_source: string;
};

export type ResidualRiskOut = {
  risk: string;
  reason: string;
  source: string;
};

export type ReportSummaryOut = {
  changed_symbols: number;
  impacted_symbols: number;
  selected_tests: number;
  regression_detected: boolean;
};

export type DynamicFactsOut = {
  coverage_missing: boolean;
  runs: RunOut[];
};

export type ReportOut = {
  task_id: string;
  changeset_id: string;
  generated_at: string;
  summary: ReportSummaryOut;
  static_facts: Record<string, unknown>;
  dynamic_facts: DynamicFactsOut;
  model_suggestions: ModelSuggestionOut[];
  residual_risks: ResidualRiskOut[];
  degraded_steps: string[];
  truncated: boolean;
};

export type ApiErrorBody = {
  code: string;
  message: string;
  detail: unknown;
};

export type CreateRepoBody = {
  name: string;
  local_path: string;
};

export type CreateChangesetBody = {
  repo_id: string;
  base_sha: string;
  target_sha: string;
};

export type AnalyzeBody = {
  generate_tests: boolean;
};

export type StrategyReport = {
  strategy: string;
  samples_total: number;
  recall_at_20: number;
  regression_recall: number;
  median_test_count_reduction: number;
  verified_rate: number;
  per_sample: Array<Record<string, unknown>>;
  generated_at: string;
};