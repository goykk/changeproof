# 1. Requirements

## 1.1 Requirement Areas

### R1 仓库与版本管理
系统应支持导入本地 Git 仓库、列出提交、选择基线与变更版本，并以只读方式读取文件。
- When 用户提交一个本地仓库路径, the system shall 创建 Repository 记录并返回可选择的提交列表。
- While 仓库已导入, the system shall 只以只读快照或受控工作副本读取文件，不修改原始仓库。
- If 路径不是 Git 仓库, the system shall 返回 422 与错误码 `not_a_git_repository`。

### R2 基线准备与覆盖率采集
系统应在隔离执行器中运行基线测试并采集覆盖率映射。
- When 分析任务进入 `baseline_preparing`, the system shall 在该版本快照上运行指定的 pytest 命令，保存日志与 `coverage.json`（含测试上下文）。
- If 命令超时或退出码非零, the system shall 记录退出状态与日志路径；无法产出覆盖率时 shall 以 `coverage_missing` 记录并继续选择阶段。

### R3 代码索引
系统应解析文件、类、函数、方法、导入、FastAPI 路由、SQLAlchemy 模型字段与 pytest 测试节点，生成带证据的节点与依赖边。
- When 索引阶段运行, the system shall 为 base 与 target 两个 commit 各生成一份 Symbol 与 DependencyEdge 集合。
- The system shall 为每条边记录 `file_path`、`line_start`、`line_end`、`analysis_source`、`confidence_level`。

### R4 Diff 映射
系统应把 Git 行级变更映射到具体符号与字段。
- When 提供 base 与 target 提交, the system shall 输出 ChangedSymbol 列表，含变更类型与 diff 行区间。
- If 某文件的行变更无法映射到任何符号, the system shall 保留文件级 ChangedSymbol 并将置信级别标记为 `inferred`。

### R5 影响分析
系统应沿反向依赖边计算直接与间接影响，并保留可核对的路径证据。
- When 存在 ChangedSymbol, the system shall 输出影响结果，每条含依赖路径、距离、排名与置信级别。
- The system shall 限制传播层级（默认 3）与结果数量（默认 200），并在被截断时于报告中标注 `truncated`。

### R6 影响图与代码定位
系统应展示可交互的影响图，支持按类型筛选、路径展开与代码位置查看。
- When 用户打开某个 changeset, the system shall 展示变更节点、影响节点与边，并标注每条边的事实来源。
- While 影响结果被截断, the system shall 在图上显示截断提示与当前层级上限。

### R7 测试选择
系统应按多来源并集选择已有 pytest 用例，并为每个用例给出选择理由。
- When 影响结果已生成, the system shall 按"覆盖变更行 ∪ 覆盖受影响符号 ∪ 静态依赖直连 ∪ 高风险保底"选取测试。
- While 覆盖率缺失, the system shall 仍执行静态与保底策略，不返回空选择集（当仓库存在测试时）。

### R8 隔离执行
系统应在隔离执行器中分别运行变更版本与基线版本的选定测试。
- When 任务进入 `test_running`, the system shall 执行选定测试并保存 `exit_code`、状态、耗时与日志路径。
- While Docker 执行器不可用, the system shall 使用本地进程执行器，并在报告的 `isolation_mode` 字段标注 `local`。

### R9 证据报告
系统应输出影响范围、测试选择理由、执行结果与残余风险，并区分静态事实、动态事实与模型建议。
- When 任务进入 `reported`, the system shall 生成结构化报告，每条结论带 `analysis_source`。
- The system shall 将模型建议与静态事实分字段存放，不参与静态确定性分数计算。

### R10 受限测试生成与双版本验证
系统应针对指定类型的回归变更生成 pytest 建议并执行双版本验证。
- When 存在未被覆盖的变更符号且启用生成, the system shall 生成测试建议并标记为 `pending_review`。
- While 建议测试在原版本通过、在变更版本因目标回归失败且重复执行结果一致, the system shall 标记为 `verified`；否则 shall 保持 `pending_review` 或 `rejected`。

### R11 自动评测
系统应管理带版本的变更样本，并对多种选择策略计算统一口径指标。
- When 评测运行, the system shall 计算 `recall_at_20`、`regression_recall`、`median_test_count_reduction`、`verified_rate` 并输出 JSON 报告。
- The system shall 只使用人工标注或受控故障注入产生的标准答案，不使用预测结果反向生成真值。

### R12 可运行与可复现
系统应支持在干净环境初始化并启动，且记录影响结论所依赖的版本信息。
- The system shall 通过一条命令初始化数据库与产物目录并启动 API 与 Web。
- The system shall 在报告与执行记录中保存 commit sha、执行器模式与覆盖率文件路径。

# 2. Technical Design

## 2.1 Architecture and Implementation Boundaries

**形态**：单仓库（monorepo），Python 3.12+（本机 3.13 兼容）后端 + Next.js 前端。控制平面不执行被分析仓库代码；执行平面由 `Executor` 实现承担。

```text
changeproof/
  pyproject.toml        core/  services/  apps/web/  samples/  eval/  scripts/  infra/
  core/schema           纯数据类型、枚举、符号 ID、仓储 Protocol（无 DB、无 IO 依赖）
  core/store            SQLAlchemy ORM、表结构、仓储实现、init_schema（唯一持久化所有者）
  core/repo             Git 操作：仓库校验、提交列表、快照导出、diff、文件读取
  core/indexer          AST/FastAPI/SQLAlchemy/pytest → Symbol 与 DependencyEdge
  core/impact           diff → ChangedSymbol；反向传播 → ImpactResult
  core/coverage         coverage.json 解析、覆盖映射、四源测试选择
  core/generation       受限测试生成与静态检查（待人工审核默认态）
  core/validation       双版本验证与稳定性判定
  core/evaluation       样本加载、策略执行、指标计算
  services/api          FastAPI 路由 + Pydantic 出参 + 状态机控制
  services/worker       编排：阶段推进、失败重试、报告聚合
  services/runner       Executor 实现：本地进程（默认）/ Docker（可选）
  apps/web              Next.js 14 App Router + TS strict + Tailwind + Cytoscape
```

**已定技术决策（唯一选项，不允许并行方案）**：
1. 存储首版只实现 SQLite（`sqlite:///./var/changeproof.db`），通过 `DatabaseSettings.url` 注入；PostgreSQL 仅保留连接串入口，不实现方言差异。
2. 产物落本地目录 `var/artifacts/{task_id}/`，路径记录在 DB；不实现 MinIO。
3. 编排为确定性状态机 + 同步阶段执行；不引入 Celery/Redis/LangGraph。
4. LLM 通过 `ModelAdapter` 注入，默认 `MockModelAdapter`（离线确定性），`OllamaModelAdapter` 由 `CHANGEPROOF_LLM=ollama` 启用；模型输出只写入 `analysis_source=model` 字段。
5. 执行器默认 `LocalProcessExecutor`；`DockerExecutor` 在 `docker` daemon 不可用时抛 `RunnerUnavailableError`，由编排降级并标注 `isolation_mode=local`。
6. `core/**` 不得 import `services/**`；`services/api` 只依赖 `core.store`、`core.schema`、`core.*` 的公开函数；`core/**` 之间只依赖 `core.schema` 与对方公开函数。
7. 前端只通过 `apps/web/lib/api.ts` 访问后端；JSON 字段保持 snake_case。

**数据分层（不得混算）**：静态事实 `ast|framework|git_history`；动态事实 `coverage|execution`；模型建议 `model`；人工真值仅存在于 `eval/samples/*.json`，不进入运行库。

**不实现（首版）**：多语言、自动修改业务代码、多 Agent、K8s、生产数据库连接、PR 机器人、跨仓库分析。

## 2.2 Interface Contracts

### C-SCHEMA@1: 共享类型、符号 ID 与仓储协议
- Provider / Consumers: `core/schema`（T1） / 全部任务; Requirements: R3–R9
- Public surface (exact):
  - `core/schema/types.py`
    - `class SymbolKind(str, Enum)`: `FILE="file"`, `CLASS="class"`, `FUNCTION="function"`, `METHOD="method"`, `ROUTE="route"`, `FIELD="field"`, `TEST="test"`, `IMPORT="import"`
    - `class EdgeType(str, Enum)`: `CALLS="calls"`, `IMPORTS="imports"`, `INHERITS="inherits"`, `DEFINES="defines"`, `USES_FIELD="uses_field"`, `ROUTES_TO="routes_to"`, `DEPENDS_ON="depends_on"`
    - `class AnalysisSource(str, Enum)`: `AST="ast"`, `FRAMEWORK="framework"`, `COVERAGE="coverage"`, `GIT_HISTORY="git_history"`, `MODEL="model"`, `EXECUTION="execution"`
    - `class Confidence(str, Enum)`: `CERTAIN="certain"`, `INFERRED="inferred"`, `SUGGESTED="suggested"`
    - `class ChangeType(str, Enum)`: `ADD="add"`, `MODIFY="modify"`, `DELETE="delete"`, `RENAME="rename"`
    - `class RunStatus(str, Enum)`: `PASSED="passed"`, `FAILED="failed"`, `ERROR="error"`, `TIMEOUT="timeout"`
    - `@dataclass(frozen=True) Symbol`: `symbol_id: str`, `repo_id: str`, `commit_sha: str`, `kind: SymbolKind`, `qualified_name: str`, `file_path: str`, `line_start: int`, `line_end: int`, `parent_id: str | None = None`, `metadata: dict[str, Any] = field(default_factory=dict)`
    - `@dataclass(frozen=True) DependencyEdge`: `repo_id: str`, `commit_sha: str`, `source_id: str`, `target_id: str`, `edge_type: EdgeType`, `analysis_source: AnalysisSource`, `confidence: Confidence`, `file_path: str`, `line_start: int`, `line_end: int`
    - `@dataclass(frozen=True) FileDiff`: `file_path: str`, `status: str`（`added|modified|deleted|renamed`）, `old_path: str | None`, `hunks: list[tuple[int, int]]`（新文件侧行区间）
    - `@dataclass(frozen=True) ChangedSymbol`: `changeset_id: str`, `symbol_id: str`, `kind: SymbolKind`, `change_type: ChangeType`, `diff_ranges: list[tuple[int, int]]`, `qualified_name: str`, `file_path: str`, `confidence: Confidence`
    - `@dataclass(frozen=True) ImpactResult`: `changeset_id: str`, `symbol_id: str`, `path: list[str]`（从变更符号到该符号的 symbol_id 序列，首个为变更符号）, `distance: int`, `rank: int`, `confidence: Confidence`, `analysis_source: AnalysisSource`, `is_direct: bool`
    - `@dataclass(frozen=True) TestCaseInfo`: `repo_id: str`, `commit_sha: str`, `node_id: str`, `file_path: str`, `name: str`, `markers: list[str] = field(default_factory=list)`
    - `@dataclass(frozen=True) CoverageRecord`: `commit_sha: str`, `test_node_id: str`, `file_path: str`, `lines: list[int]`
    - `@dataclass(frozen=True) SelectionReason`: `source: str`（`covered_change|covered_impact|static_dependency|high_risk_guard`）, `detail: str`, `ref_id: str | None = None`
    - `@dataclass(frozen=True) TestSelectionItem`: `changeset_id: str`, `test_node_id: str`, `strategy: str`, `reasons: list[SelectionReason]`
    - `@dataclass(frozen=True) RunResult`: `exit_code: int`, `status: RunStatus`, `duration_ms: int`, `log_path: str`, `isolation_mode: str`（`local|docker`）, `selected_count: int`, `passed_count: int`, `failed_count: int`, `error_count: int`, `skipped_count: int`, `timed_out: bool`, `coverage_path: str | None`
  - `core/schema/ids.py`：纯函数，无 IO
    - `def make_symbol_id(kind: SymbolKind, *parts: str) -> str`：拼接规则见下
    - `def file_symbol_id(file_path: str) -> str` → `file_path`
    - `def qualname_symbol_id(file_path: str, qualname: str) -> str` → `f"{file_path}::{qualname}"`
    - `def route_symbol_id(method: str, path: str) -> str` → `f"{method.upper()} {path}"`
    - `def field_symbol_id(file_path: str, model: str, field_name: str) -> str` → `f"{file_path}::{model}.{field_name}"`
    - `def test_symbol_id(file_path: str, test_name: str) -> str` → `f"{file_path}::{test_name}"`
  - `core/schema/protocols.py`（`typing.Protocol`，仅签名）：
    - `SymbolRepository`: `upsert_symbols(symbols: Sequence[Symbol]) -> int`; `list_symbols(repo_id: str, commit_sha: str, kinds: Sequence[SymbolKind] | None = None) -> list[Symbol]`; `get_symbol(symbol_id: str) -> Symbol | None`; `find_by_qualified_name(repo_id: str, commit_sha: str, qualified_name: str) -> Symbol | None`
    - `EdgeRepository`: `upsert_edges(edges: Sequence[DependencyEdge]) -> int`; `parents(symbol_id: str, edge_types: Sequence[EdgeType] | None = None) -> list[DependencyEdge]`（反向：谁依赖我）; `children(...)` 同参; `edges_for_commit(repo_id: str, commit_sha: str) -> list[DependencyEdge]`
    - `CoverageRepository`: `replace_coverage(commit_sha: str, records: Sequence[CoverageRecord]) -> int`; `tests_covering_lines(commit_sha: str, file_path: str, lines: Sequence[int]) -> list[str]`; `tests_covering_symbol(commit_sha: str, symbol: Symbol) -> list[str]`
    - `TestRepository`: `upsert_tests(tests: Sequence[TestCaseInfo]) -> int`; `list_tests(repo_id: str, commit_sha: str) -> list[TestCaseInfo]`
    - `TaskRepository`: `create_task(changeset_id: str) -> TaskRecord`; `get_task(task_id: str) -> TaskRecord | None`; `list_tasks(changeset_id: str) -> list[TaskRecord]`; `update_task(task_id: str, **fields: Any) -> TaskRecord`; `append_event(task_id: str, from_status: str | None, to_status: str, stage: str, message: str) -> None`; `active_task_for_changeset(changeset_id: str) -> TaskRecord | None`
    - `@dataclass TaskRecord`: `task_id: str`, `changeset_id: str`, `status: str`, `stage: str`, `resume_status: str | None`, `retry_count: int`, `error_code: str | None`, `error_message: str | None`, `report_path: str | None`
    - `@dataclass RepositoryRecord`: `repo_id: str`, `name: str`, `local_path: str`, `default_branch: str`, `status: str`
    - `@dataclass CommitRecord`: `repo_id: str`, `sha: str`, `short_sha: str`, `message: str`, `author: str`, `committed_at: str`（ISO8601）
    - `@dataclass ChangesetRecord`: `changeset_id: str`, `repo_id: str`, `base_sha: str`, `target_sha: str`, `status: str`
- Ownership: provider 写 `core/schema/**`；每个 consumer 只写自己的写集，不得修改 `core/schema`。
- Failure semantics: 类型/枚举在导入期即校验；`ValueError` 用于非法 ID 构造；不做运行时兜底转换。
- Interaction semantics / status: 所有 dataclass `frozen=True`；枚举字符串值即落库值与 API 值；`path` 顺序为「变更符号 → … → 受影响符号」。**READY**

### C-STORE@1: 持久化表结构与仓储实现
- Provider / Consumers: `core/store`（T2） / T8 API、T9 worker、T11 eval; Requirements: R1–R11
- Public surface (exact):
  - `core/store/db.py`：`def create_engine_and_session(db_url: str) -> tuple[Engine, sessionmaker[Session]]`；`def init_schema(engine: Engine) -> None`（`Base.metadata.create_all`）
  - `core/store/models.py`：ORM 类 `RepositoryRow`、`CommitRow`、`ChangesetRow`、`SymbolRow`、`DependencyEdgeRow`、`TestCaseRow`、`CoverageRow`、`TestSelectionRow`、`TestRunRow`、`AnalysisTaskRow`、`TaskEventRow`、`ReportRow`
  - 表名与唯一键（逐列定义见 implement，列名必须一致）：
    - `repositories`: `repo_id PK, name, local_path UNIQUE, default_branch, status, created_at`
    - `commits`: `id PK auto, repo_id, sha, message, author, committed_at`；`UNIQUE(repo_id, sha)`
    - `changesets`: `changeset_id PK, repo_id, base_sha, target_sha, status, created_at`；`UNIQUE(repo_id, base_sha, target_sha)`
    - `symbols`: `symbol_id PK, repo_id, commit_sha, kind, qualified_name, file_path, line_start, line_end, parent_id, metadata_json`；`UNIQUE(repo_id, commit_sha, symbol_id)`
    - `dependency_edges`: `id PK auto, repo_id, commit_sha, source_id, target_id, edge_type, analysis_source, confidence, file_path, line_start, line_end`；`UNIQUE(repo_id, commit_sha, source_id, target_id, edge_type)`
    - `test_cases`: `id PK auto, repo_id, commit_sha, node_id, file_path, name, markers_json`；`UNIQUE(repo_id, commit_sha, node_id)`
    - `coverage_records`: `id PK auto, commit_sha, test_node_id, file_path, lines_json`；`UNIQUE(commit_sha, test_node_id, file_path)`
    - `test_selections`: `id PK auto, changeset_id, test_node_id, strategy, reasons_json`；`UNIQUE(changeset_id, test_node_id)`
    - `test_runs`: `id PK auto, changeset_id, version_type`（`base|target`）, `status, exit_code, duration_ms, log_path, isolation_mode, selected_count, passed_count, failed_count, error_count, skipped_count, coverage_path`；`UNIQUE(changeset_id, version_type)`
    - `analysis_tasks`: `task_id PK, changeset_id, status, stage, resume_status, retry_count, error_code, error_message, report_path, created_at, updated_at`
    - `task_events`: `id PK auto, task_id, from_status, to_status, stage, message, created_at`
    - `reports`: `id PK auto, task_id, changeset_id, payload_json, created_at`；`UNIQUE(task_id)`
    - `changed_symbols`: `id PK auto, changeset_id, symbol_id, kind, change_type, diff_ranges_json, qualified_name, file_path, confidence`；`UNIQUE(changeset_id, symbol_id)`
    - `impact_results`: `id PK auto, changeset_id, symbol_id, path_json, distance, rank, confidence, analysis_source, is_direct`；`UNIQUE(changeset_id, symbol_id)`
  - `class SqliteStore`：`def __init__(self, db_url: str) -> None`；属性 `.repositories .commits .changesets .symbols .edges .coverage .tests .tasks .reports .selections .runs .changed_symbols .impacts`，各自实现 C-SCHEMA 中对应 Protocol（另含 `RepositoryStore`/`CommitStore`/`ChangesetStore`/`SelectionStore`/`RunStore` 同名方法：`upsert_repository(RepositoryRecord) -> RepositoryRecord`、`get_repository(repo_id)`、`list_repositories()`、`upsert_commits(Sequence[CommitRecord]) -> int`、`list_commits(repo_id, limit=50)`、`get_or_create_changeset(ChangesetRecord) -> ChangesetRecord`、`get_changeset(changeset_id)`、`replace_selections(changeset_id, items) -> int`、`list_selections(changeset_id) -> list[TestSelectionItem]`、`upsert_run(changeset_id, version_type, RunResult) -> None`、`list_runs(changeset_id) -> list[tuple[str, RunResult]]`（元素为 `(version_type, result)`，按 `version_type` 字典序）、`save_report(task_id, changeset_id, payload: dict) -> str`（返回 payload 落盘路径）、`get_report(changeset_id) -> dict | None`）
  - `.changed_symbols` 实现的是 `ChangedSymbolStore`：`replace_changed_symbols(changeset_id: str, items: Sequence[ChangedSymbol]) -> int`、`list_changed_symbols(changeset_id: str) -> list[ChangedSymbol]`（按 `file_path, symbol_id` 排序）
  - `.impacts` 实现的是 `ImpactStore`：`replace_impacts(changeset_id: str, items: Sequence[ImpactResult]) -> int`、`list_impacts(changeset_id: str) -> list[ImpactResult]`（按 `rank` 升序）
  - `core/store/__init__.py` 导出 `SqliteStore`、`init_schema`、`create_engine_and_session`
- Ownership: provider 写 `core/store/**`；consumer 写自己的写集。
- Failure semantics: 写冲突走唯一键 upsert（`ON CONFLICT DO UPDATE`）；仓储方法在记录不存在时返回 `None`，不抛异常；`init_schema` 幂等可重复调用。
- Interaction semantics / status: 所有写方法幂等，重复调用不产生重复行；`replace_coverage(commit_sha, records)` 先按 commit 删除旧记录再插入，同一事务完成；`replace_selections` 同理按 changeset 全量替换。**READY**

### C-STATE@1: 分析任务状态机
- Provider / Consumers: `core/schema/state.py`(T1) / T8 `services/api`、T9 `services/worker`; Requirements: R2, R8, R9
- Public surface (exact): `core/schema/state.py`
  - `class TaskStatus(str, Enum)`: `CREATED="created"`, `BASELINE_PREPARING="baseline_preparing"`, `INDEXING="indexing"`, `ANALYZING="analyzing"`, `TEST_SELECTING="test_selecting"`, `TEST_RUNNING="test_running"`, `TEST_GENERATING="test_generating"`, `VALIDATING="validating"`, `REPORTED="reported"`, `FAILED="failed"`
  - `ALLOWED_TRANSITIONS: dict[str, frozenset[str]]`，内容为：`created→{baseline_preparing,failed}`；`baseline_preparing→{indexing,failed}`；`indexing→{analyzing,failed}`；`analyzing→{test_selecting,failed}`；`test_selecting→{test_running,failed}`；`test_running→{test_generating,reported,failed}`；`test_generating→{validating,reported,failed}`；`validating→{reported,failed}`；`reported→set()`；`failed→{baseline_preparing,indexing,analyzing,test_selecting,test_running,test_generating,validating}`
  - `MAX_RETRIES: int = 3`
  - `TERMINAL_STATUSES = frozenset({TaskStatus.REPORTED.value})`
  - `def assert_transition(current: str, target: str) -> None`（非法迁移抛 `InvalidTransitionError(current, target)`）
  - `class InvalidTransitionError(ValueError)`：属性 `current`, `target`
- Interaction semantics / status:
  - 每次迁移必须调用 `tasks.update_task(task_id, status=..., stage=..., resume_status=..., error_code=..., error_message=...)` 并 `append_event(...)`，事件顺序即状态顺序。
  - `failed` 迁移必须写 `error_code`、`error_message`，并把迁移前的状态写入 `resume_status`；从 `failed` 重试时先 `retry_count += 1`，超过 `MAX_RETRIES` 则拒绝重试并返回 409 `retry_exhausted`。
  - 模型相关失败（`test_generating|validating`）不得把任务标记为 `reported` 成功；降级时 `reported` 报告必须含 `degraded_steps: ["generation"]`。
  - 任务推进只由 `services/worker` 执行；`services/api` 只创建任务（`created`）与读取状态，不直接写中间状态。
  - `POST /api/changesets/{id}/analyze` 幂等：若存在非终态任务则直接返回该任务（HTTP 200），不新建。**READY**
### C-CORE@1: core 分析函数签名与语义
- Provider / Consumers: `core/indexer`(T4)、`core/impact`(T5)、`core/coverage`(T6)、`core/repo`(T3) / T9 worker、T11 eval; Requirements: R2–R7
- Public surface (exact):
  - `core/repo/git_repo.py`
    - `@dataclass RepoInfo`: `local_path: str`, `name: str`, `default_branch: str`
    - `def validate_repository(local_path: str) -> RepoInfo`（非 Git 仓库抛 `NotAGitRepositoryError`，带 `code="not_a_git_repository"`）
    - `def list_commits(local_path: str, limit: int = 50) -> list[CommitRecord]`
    - `def export_snapshot(local_path: str, commit_sha: str, target_dir: str) -> str`（`git archive` 导出，返回目标目录绝对路径）
    - `def read_file_at(local_path: str, commit_sha: str, rel_path: str) -> str | None`
    - `def list_python_files(snapshot_dir: str, include_globs: Sequence[str] = ("**/*.py",)) -> list[str]`（返回相对路径，排除 `.venv`、`build`、`dist`、`tests` 之外的第三方目录）
    - `def diff_commits(local_path: str, base_sha: str, target_sha: str) -> list[FileDiff]`
    - `class NotAGitRepositoryError(ValueError)`: `code: str`, `message: str`
  - `core/indexer/indexer.py`
    - `@dataclass IndexResult`: `symbols: list[Symbol]`, `edges: list[DependencyEdge]`, `tests: list[TestCaseInfo]`, `warnings: list[str]`
    - `def index_repository(snapshot_dir: str, repo_id: str, commit_sha: str) -> IndexResult`
    - 解析范围：模块/类/函数/方法（`ast`）、`import`/`from import`（`IMPORTS` 边）、类继承（`INHERITS`）、本仓库内函数调用（`CALLS`，仅解析 `Name`/`Attribute` 能唯一匹配的调用）、`app.include_router` 与 `@router.get/post/put/delete`（`ROUTE` 符号 + `ROUTES_TO` 边）、SQLAlchemy `Column`/`mapped_column` 字段（`FIELD`）+ 同文件模型引用（`USES_FIELD`）、`test_*.py` 中的 `def test_*`（`TEST` 符号 + `DEFINES` 边）。无法确定的调用不建边，写入 `warnings`。
  - `core/impact/mapper.py`
    - `def map_changes(file_diffs: Sequence[FileDiff], base_symbols: Sequence[Symbol], target_symbols: Sequence[Symbol], changeset_id: str) -> list[ChangedSymbol]`
    - 规则：行区间落在符号 `[line_start, line_end]` 内即映射该符号；文件级兜底 `kind=FILE`、`confidence=INFERRED`；删除符号用 base 侧符号；重命名需 `qualified_name` 变化且同文件命中。
  - `core/impact/propagation.py`
    - `@dataclass ImpactOutput`: `items: list[ImpactResult]`, `truncated: bool`, `max_depth: int`, `max_results: int`
    - `def compute_impact(changed: Sequence[ChangedSymbol], edges: Sequence[DependencyEdge], symbols_by_id: Mapping[str, Symbol], changeset_id: str, max_depth: int = 3, max_results: int = 200) -> ImpactOutput`
    - 排序：`is_direct` 优先 → `distance` 升序 → 边类型权重（`ROUTES_TO=5, CALLS=4, USES_FIELD=3, INHERITS=2, IMPORTS=1`）→ `symbol_id` 字典序；`rank` 从 1 连续编号；同一 symbol 仅保留最短路径。
  - `core/coverage/mapper.py`
    - `def parse_coverage_json(path: str) -> dict[str, dict[str, list[int]]]`（返回 `{file_path: {test_node_id: lines}}`；无 `contexts` 时返回 `{file_path: {"": lines}}`）
    - `def build_coverage_records(parsed: Mapping[str, Mapping[str, Sequence[int]]], commit_sha: str) -> list[CoverageRecord]`
    - `def coverage_recall(parsed, target_symbols: Sequence[Symbol]) -> float`（被任一测试覆盖的变更符号比例，公式：命中符号数 / 变更符号数，分母为 0 时返回 0.0）
  - `core/coverage/selector.py`
    - `def select_tests(changed: Sequence[ChangedSymbol], impacts: Sequence[ImpactResult], symbols: Sequence[Symbol], tests: Sequence[TestCaseInfo], coverage: Sequence[CoverageRecord], high_risk_globs: Sequence[str] = ("app/core/**",), strategy: str = "combo") -> list[TestSelectionItem]`
    - 规则：①`covered_change`：测试覆盖了任一 ChangedSymbol 的变更行；②`covered_impact`：测试覆盖了任一受影响符号的行；③`static_dependency`：测试文件与受影响文件存在 `ROUTES_TO`/`CALLS` 边或同目录邻近；④`high_risk_guard`：测试文件匹配 `high_risk_globs` 或测试名含保底标记 `@pytest.mark.high_risk`。返回按 `test_node_id` 去重；`reasons` 至少 1 条；同一测试命中多源时合并全部理由。
- Ownership: provider 各自写 `core/repo/**`、`core/indexer/**`、`core/impact/**`、`core/coverage/**`；consumer 不写 provider 目录。
- Failure semantics: 解析单文件异常记入 `IndexResult.warnings` 并继续；`parse_coverage_json` 文件不存在抛 `FileNotFoundError`，编排层捕获后以 `coverage_missing` 降级；`export_snapshot` 失败抛 `GitCommandError(code="git_command_failed")`。
- Interaction semantics / status: 所有函数纯输入输出（除 `export_snapshot` 写目标目录），不读环境变量、不连数据库；`select_tests` 当输入为空时返回 `[]` 而非抛异常。**READY**

### C-RUNNER@1: 隔离执行器协议
- Provider / Consumers: `services/runner`(T7) / T9 worker; Requirements: R2, R8, R10
- Public surface (exact): `services/runner/executor.py`
  - `@dataclass RunnerJob`: `job_id: str`, `snapshot_dir: str`, `command: list[str]`, `cwd: str = "."`, `timeout_s: int = 600`, `max_output_bytes: int = 5_000_000`, `network: bool = False`, `artifact_dir: str | None = None`, `env_allowlist: list[str] = ["PATH", "PYTHONPATH", "HOME", "LANG", "TMPDIR", "SYSTEMROOT", "CHANGEPROOF_*"]`
  - `class Executor(Protocol)`: `def run(self, job: RunnerJob) -> RunResult`; `@property def mode(self) -> str`
  - `class LocalProcessExecutor`: `def __init__(self, max_concurrency: int = 1) -> None`；`mode == "local"`；以 `subprocess.Popen(shell=False)` 执行，超时 kill 进程树并把 `status` 置 `TIMEOUT`、`timed_out=True`；stdout/stderr 截断到 `max_output_bytes`；日志写入 `{artifact_dir}/{job_id}.log`（未给则写 `var/artifacts/{job_id}/run.log`）；`network=False` 时本地模式**只记录** `network_enforced=False`（在日志首行写 `network_enforced=false`），不伪造隔离能力。
  - `class DockerExecutor`: `def __init__(self, image: str = "changeproof-runner:latest") -> None`；`mode == "docker"`；daemon 不可达抛 `RunnerUnavailableError(code="docker_unavailable")`；以 `--network=none --cpus=1 --memory=512m --pids-limit=128 --user 65534:65534` 运行，只读挂载 `snapshot_dir:/work:ro`、可写挂载 `artifact_dir:/artifacts`。
  - `class RunnerUnavailableError(RuntimeError)`: `code: str`
  - `def parse_pytest_summary(log_text: str) -> dict[str, int]`（返回键 `passed, failed, error, skipped, selected`；解析失败返回全 0）
  - `def build_pytest_command(test_node_ids: Sequence[str], coverage_json_path: str | None) -> list[str]`（`["python","-m","pytest","-p","no:cacheprovider","-q"] + node_ids + ["--cov=.","--cov-context=test","--cov-report=json:{path}"]`，无覆盖率路径时省略后三个参数）
- Ownership: provider 写 `services/runner/**`；consumer 只调用，不修改。
- Failure semantics: 命令不存在或快照目录缺失抛 `RunnerUnavailableError(code="bad_job")`；执行异常一律转换为 `RunStatus.ERROR` 而不是抛给编排层（除 `RunnerUnavailableError`）。
- Interaction semantics / status: `run()` 幂等键为 `job_id`，同一 `job_id` 的产物目录内容被覆盖写；不修改 `snapshot_dir`；不注入业务密钥；`artifact_dir` 不存在时自动创建。**READY**

### C-ORCH@1: 分析任务编排
- Provider / Consumers: `services/worker`(T9) / T8 API 触发; Requirements: R2, R8, R9, R10
- Public surface (exact): `services/worker/orchestrator.py`
  - `def analyze_changeset(store, executor, changeset_id: str, options: AnalyzeOptions) -> TaskRecord`
  - `@dataclass AnalyzeOptions`: `repo_local_path: str`, `base_sha: str`, `target_sha: str`, `pytest_command: list[str] | None = None`（默认 `["python","-m","pytest","-q"]`）, `max_depth: int = 3`, `max_results: int = 200`, `generate_tests: bool = False`, `high_risk_globs: tuple[str, ...] = ("app/core/**",)`, `artifacts_root: str = "var/artifacts"`
  - `def run_generation_stage(store, executor, task_id: str, options: AnalyzeOptions, model_adapter) -> dict[str, Any]`
- Interaction semantics / status:
  - 顺序固定：`created → baseline_preparing`（导出 base 快照、跑基线 pytest + coverage）→ `indexing`（base/target 各索引一次）→ `analyzing`（diff→changed→impact）→ `test_selecting` → `test_running`（先 target 再 base）→ 可选 `test_generating → validating` → `reported`。
  - 每阶段产物写 `{artifacts_root}/{task_id}/{stage}/`：`snapshot/`、`baseline.log`、`coverage.json`、`index_base.json`、`index_target.json`、`impact.json`、`selections.json`、`run_target.log`、`run_base.log`、`report.json`。
  - **权威数据源**：`analyzing` 阶段除写 `impact.json`（留档证据）外，必须同时调用 `store.changed_symbols.replace_changed_symbols(changeset_id, changed)` 与 `store.impacts.replace_impacts(changeset_id, output.items)`；`test_selecting` 阶段必须调用 `store.selections.replace_selections(changeset_id, items)`；`test_running` 必须对 base/target 各调用一次 `store.runs.upsert_run(changeset_id, version_type, run_result)`。API 读取一律以库表为准，不以产物文件为准。
  - `impact.json` 的固定结构（仅作证据留档）：`{"changed_symbols": [...], "impacts": [...], "truncated": bool, "max_depth": int, "max_results": int}`。
  - 失败即转 `failed`（C-STATE@1），不吞异常；覆盖率缺失记 `warning` 继续；`generate_tests=False` 时不进入生成阶段。
  - `reported` 必须写 `reports.payload_json` 与 `report_path`，并 `upsert_run` 两条记录（`base`、`target`）。
  - 幂等：同一 `changeset_id` 已有非终态任务时 `analyze_changeset` 直接返回该任务。**READY**

### C-API@1: HTTP 资源与出参
- Provider / Consumers: `services/api`(T8) / `apps/web`(T10)、T11 eval; Requirements: R1–R11
- Public surface (exact): `services/api/main.py` 暴露 `app`；所有响应字段 snake_case；错误体 `{"code": str, "message": str, "detail": Any | None}`。
  - `GET /api/health` → `{status:"ok", storage:"sqlite", isolation_mode:"local|docker", llm:"mock|ollama"}`
  - `POST /api/repos` body `{name: str, local_path: str}` → 201 `RepositoryOut{repo_id, name, local_path, default_branch, status}`；非 Git 仓库 → 422 `not_a_git_repository`
  - `GET /api/repos` → `{items: RepositoryOut[]}`
  - `GET /api/repos/{repo_id}/commits?limit=50` → `{items: CommitOut{repo_id, sha, short_sha, message, author, committed_at}}`；未知 repo → 404 `not_found`
  - `POST /api/changesets` body `{repo_id: str, base_sha: str, target_sha: str}` → 201 `ChangesetOut{changeset_id, repo_id, base_sha, target_sha, status}`；重复提交返回既有记录（200）
  - `POST /api/changesets/{changeset_id}/analyze` body `{generate_tests: bool = false}` → 202 `TaskOut{task_id, changeset_id, status, stage, retry_count, error_code, error_message}`
  - `GET /api/tasks/{task_id}` → `TaskOut{task_id, changeset_id, status, stage, retry_count, error_code, error_message, report_available: bool}`
  - `POST /api/tasks/{task_id}/retry` → `TaskOut`；超过上限 → 409 `retry_exhausted`
  - `GET /api/changesets/{changeset_id}/impact` → `{changed_symbols: [{symbol_id, kind, qualified_name, file_path, change_type, diff_ranges, confidence}], impacts: [{symbol_id, kind, qualified_name, file_path, distance, rank, confidence, analysis_source, is_direct, path}], truncated: bool, complete: bool}`
    - 数据来源（唯一）：`store.changed_symbols.list_changed_symbols(changeset_id)` 与 `store.impacts.list_impacts(changeset_id)`；**不允许读取 `impact.json` 产物文件**。`impacts[].kind`/`file_path` 由 `store.symbols.get_symbol(symbol_id)` 补全，symbol 不存在时 `kind="file"`、`file_path=""` 且 `complete=false`；`truncated` 取 `reports.payload_json.static_facts.truncated`，无报告时为 `false`。
  - `GET /api/changesets/{changeset_id}/graph?max_depth=3` → `{nodes: [{id, label, kind, file_path, line_start, analysis_source}], edges: [{source, target, edge_type, analysis_source, confidence, file_path, line_start}]}`
    - 节点集合 = 该 changeset 的 changed_symbols ∪ impacts 的 symbol_id，属性取自 `store.symbols.get_symbol`；边取自 `store.edges.edges_for_commit(repo_id, target_sha)` 并只保留两端都在节点集合内的边。
  - `GET /api/changesets/{changeset_id}/selections` → `{items: [{test_node_id, strategy, reasons: [{source, detail, ref_id}]}]}`
  - `GET /api/changesets/{changeset_id}/runs` → `{items: [{version_type, status, exit_code, duration_ms, selected_count, passed_count, failed_count, error_count, skipped_count, isolation_mode, log_path, coverage_path}]}`
  - `GET /api/changesets/{changeset_id}/report` → `{task_id, changeset_id, generated_at, summary{changed_symbols, impacted_symbols, selected_tests, regression_detected}, static_facts{...}, dynamic_facts{coverage_missing, runs:[...]}, model_suggestions:[{test_node_id, status, rationale, analysis_source:"model"}], residual_risks:[{risk, reason, source}], degraded_steps:[str], truncated: bool}`；无报告 → 404 `report_not_ready`
- Ownership: provider 写 `services/api/**`；consumer 写 `apps/web/lib/api.ts` 与其页面。
- Failure semantics: 参数校验失败 422 `invalid_request`（Pydantic 默认错误映射）；未知资源 404 `not_found`；内部异常 500 `internal_error` 并写日志，不泄露堆栈到响应体。
- Interaction semantics / status: `services/api` 进程内以 `BackgroundTasks` 调用 `services.worker.orchestrator.analyze_changeset`（同步执行，单并发），不依赖外部队列；所有 GET 只读不写库；`analyze` 幂等见 C-STATE@1。**READY**

### C-SAMPLE@1: 受控样例仓库与标注样本
- Provider / Consumers: `scripts/bootstrap_samples.py`(T11) / T11 eval、T9 worker 演示、T10 演示流程; Requirements: R11, R12
- Public surface (exact):
  - `scripts/bootstrap_samples.py` 生成 `var/samples/shop_api/`（一个可运行的 FastAPI 仓库，含 `app/`、`app/core/`、`tests/`、`pyproject.toml`）并初始化 git，产出两个提交：
    - 提交 A（基线）：`app/models.py::Order` 有 `total`；`app/services/order.py::OrderService.calculate_total` 读取 `order.total`；`app/api/orders.py` 暴露 `GET /orders/{order_id}`；`tests/test_orders.py` 含 `test_order_total` 与 `test_order_list`。
    - 提交 B（注入回归）：`Order.total` 改名为 `total_amount`，但 `app/services/order.py` 仍访问 `order.total`（`AttributeError`），路由与测试未同步。
  - 写入 `eval/samples/shop_api_rename_q1.json`：`{sample_id, repo_name:"shop_api", repo_path_marker:"var/samples/shop_api", base_ref:"v1-baseline", target_ref:"v2-regression", expected_impact_files:["app/models.py","app/services/order.py","app/api/orders.py"], expected_impact_symbols:["app/models.py::Order.total","app/services/order.py::OrderService.calculate_total"], expected_failing_tests:["tests/test_orders.py::test_order_total"], high_risk_globs:["app/core/**"]}`
  - 写入 `eval/samples/lock.json`：`{"shop_api": {"base_sha": str, "target_sha": str}}`（脚本运行后生成，供评测使用）
  - 仓库默认测试命令：`["python","-m","pytest","-q","tests"]`（`app/core/` 中放一个高风险纯函数模块与其测试 `tests/test_core_pricing.py`，用于保底策略）。
- Ownership: provider 写 `samples/**`（生成物为 `var/samples/**`）、`eval/samples/*.json`、`scripts/bootstrap_samples.py`；consumer 只读取 JSON 与仓库路径。
- Failure semantics: 重复运行脚本时删除并重建 `var/samples/shop_api`（幂等）；git 不可用抛 `RuntimeError("git_unavailable")`。
- Interaction semantics / status: 提交 sha 每次重建都会变化，因此**禁止**把 sha 写死在任何代码或契约中，一律从 `eval/samples/lock.json` 读取。**READY**

### C-EVAL@1: 评测指标口径与入口
- Provider / Consumers: `core/evaluation` + `scripts/run_eval.py`(T11) / T11 报告、T10 评测页; Requirements: R11
- Public surface (exact): `core/evaluation/metrics.py`
  - `def recall_at_20(expected_files: Sequence[str], ranked_files: Sequence[str]) -> float`：分母 `len(expected_files)`；命中数 = 前 20 个 `ranked_files` 与预期文件的交集；空预期返回 1.0。
  - `def regression_recall(expected_failing_tests: Sequence[str], selected_tests: Sequence[str]) -> float`：交集 / 预期数；空预期返回 1.0。
  - `def median_test_count_reduction(selected_counts: Sequence[int], total_counts: Sequence[int]) -> float`：逐样本 `1 - selected/total`（total 为 0 时该样本跳过），返回中位数；无有效样本返回 0.0。
  - `def verified_rate(generated: Sequence[dict]) -> float`：`status=="verified"` 数量 / 总数；总数 0 返回 0.0。
  - `@dataclass StrategyReport`: `strategy: str`, `samples_total: int`, `recall_at_20: float`, `regression_recall: float`, `median_test_count_reduction: float`, `verified_rate: float`, `per_sample: list[dict]`, `generated_at: str`
  - `def build_report(strategy: str, per_sample: Sequence[dict]) -> StrategyReport`
  - `scripts/run_eval.py --strategy {full|neighbor|static|coverage|combo} --samples eval/samples --out var/eval`：输出 `var/eval/{strategy}.json`（`StrategyReport` 序列化，snake_case）。
- Ownership: provider 写 `core/evaluation/**`、`scripts/run_eval.py`；consumer 只读 JSON。
- Failure semantics: 单个样本执行失败记入 `per_sample[i].error` 并在指标中按未命中计，不中断整轮评测；样本缺 `lock.json` sha 时跳过并记 `skipped_missing_sha`。
- Interaction semantics / status: 评测只读运行库之外的独立标注文件；**禁止**用运行期预测结果写入 `eval/samples/*.json`。**READY**

### C-WEB@1: 前端页面与数据绑定
- Provider / Consumers: `apps/web`(T10) / 演示与验证; Requirements: R6, R9
- Public surface (exact):
  - 路由：`/`（仓库列表 + 导入表单 + 提交选择 + 创建 changeset）、`/changesets/[id]`（任务进度、影响图、测试选择、执行结果）、`/changesets/[id]/report`（证据报告）、`/eval`（策略对比）
  - `apps/web/lib/api.ts`：`const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000"`；导出 `listRepos()`, `createRepo(body)`, `listCommits(repoId, limit)`, `createChangeset(body)`, `analyzeChangeset(id, body)`, `getTask(id)`, `getImpact(id)`, `getGraph(id, maxDepth)`, `getSelections(id)`, `getRuns(id)`, `getReport(id)`；返回类型与 C-API@1 出参字段一一对应（snake_case，不转换大小写）。
  - 组件：`components/ui.tsx` 导出 `Button`、`Card`、`Badge`、`ErrorBanner`、`Spinner`；`components/ImpactGraph.tsx` 使用 `cytoscape`，节点颜色按 `analysis_source` 区分（`ast/framework/git_history` 蓝、`coverage/execution` 绿、`model` 橙），模型节点必须有 `建议` 徽标。
  - 影响页必须显示：`truncated` 提示、每条边的 `analysis_source`、选中节点代码位置（`file_path:line_start`）、执行结果区 `isolation_mode` 与 `coverage_missing` 标识。
- Ownership: provider 写 `apps/web/**`；不得修改后端接口。
- Failure semantics: 任何请求失败显示 `ErrorBanner`（含 `code` 与 `message`），不静默吞错；报告未就绪（404 `report_not_ready`）显示等待态而非错误态。
- Interaction semantics / status: 前端不缓存写操作结果；任务轮询间隔固定 2000ms，`status ∈ {reported, failed}` 时停止。**READY**
## 2.3 Service Interaction Matrix & Open Decisions

### 2.3.1 跨任务调用矩阵（唯一权威来源）

| Caller | Callee | Method | 事务边界 | 锁顺序 | 幂等 / 边界语义 |
|---|---|---|---|---|---|
| `apps/web` | `services/api` | C-API@1 全部 HTTP 端点 | 无（HTTP 边界） | 无 | 读端点只读；`POST /repos`、`POST /changesets` 重复提交返回既有记录；`POST /analyze` 返回既有非终态任务 |
| `services/api` | `core/store` | `repositories/commits/changesets/tasks` 读写 | 每请求一个 `Session`，先读后写，异常整请求回滚 | `repositories → commits → changesets → analysis_tasks` | 唯一键 upsert；`create_task` 前先查 `active_task_for_changeset` |
| `services/api` | `services/worker` | `analyze_changeset(...)` | **不共享事务**：api 先提交任务创建，worker 自开 `Session` | worker 内部顺序：`analysis_tasks → symbols → dependency_edges → test_cases → coverage_records → test_selections → test_runs → reports` | 以 `changeset_id` 查非终态任务，存在即不重复执行 |
| `services/worker` | `core/repo` | `validate/list/export/read/diff/index` | 无 DB 事务（文件/Git 操作） | 无 | `export_snapshot` 覆盖写目标目录；不改原始仓库 |
| `services/worker` | `core/indexer` / `core/impact` / `core/coverage` | C-CORE@1 纯函数 | 无 | 无 | 纯函数；输入相同则输出确定（不读环境变量） |
| `services/worker` | `services/runner` | `Executor.run(RunnerJob)` | 无（跨进程/容器边界） | 无 | `job_id` 决定产物目录，重复执行覆盖写 |
| `services/worker` | `core/generation` / `core/validation` / `ModelAdapter` | 生成与验证 | 无 | 无 | 模型失败记 `degraded_steps`，任务仍可 `reported`；生成物默认 `pending_review` |
| `scripts/run_eval.py` | `core/evaluation` + `core/store` | `build_report` / 只读仓储 | 只读库 + 写 `var/eval/*.json` | 无 | 每策略一个文件，重复运行覆盖；样本 sha 只从 `eval/samples/lock.json` 读取 |

**跨边界不变量**：
1. `core/**` 永不 import `services/**`；反向依赖只允许 `services → core`。
2. 只有 `core/store` 写数据库；`core/indexer/impact/coverage` 只返回内存对象。
3. 只有 `services/runner` 执行外部进程；其余模块不得调用 `subprocess`。
4. 模型调用只出现在 `core/generation` 与其 `ModelAdapter` 注入点。

### 2.3.2 已收敛决策（无未决项）

| 决策点 | 收敛结果 | 影响契约 |
|---|---|---|
| 存储 | 首版只实现 SQLite；PostgreSQL 仅保留连接串入口 | C-STORE@1 |
| 产物存储 | 本地 `var/artifacts/{task_id}/`；不实现 MinIO | C-ORCH@1 |
| 编排 | 确定性状态机 + 同步执行；不引入 Celery/Redis/LangGraph | C-STATE@1, C-ORCH@1 |
| 执行隔离 | 默认本地进程执行器并如实标注 `isolation_mode=local`；Docker 可选 | C-RUNNER@1 |
| LLM | 默认 `MockModelAdapter`；`CHANGEPROOF_LLM=ollama` 切换 | C-CORE@1, C-ORCH@1 |
| 覆盖率上下文 | `coverage.json` 必须由 `--cov-context=test` 产生，context 为 pytest node id | C-CORE@1, C-RUNNER@1 |
| 样例 sha | 运行时生成，只存 `eval/samples/lock.json`，代码与契约不写死 sha | C-SAMPLE@1 |
| 前端字段 | 一律 snake_case，不做 camelCase 转换 | C-API@1, C-WEB@1 |

### 2.3.3 运行环境约束（影响实现方式，非开放决策）

- 本机 Docker daemon 当前不可达 → `DockerExecutor` 必须在不可用时可降级，且**不得**成为主链路硬依赖。
- 本机 Ollama 无可用模型 → `MockModelAdapter` 必须是默认且能产出确定性建议；`OllamaModelAdapter` 走 HTTP 调用，失败即降级。
- Windows 环境 → 进程终止使用 `taskkill /T /F` 或 `process.kill()` 兜底；路径处理一律使用 `pathlib`，不拼接字符串分隔符。
- 端口占用：后端默认 `8000`，前端默认 `3000`（通过 `NEXT_PUBLIC_API_BASE` 与 `PORT` 可改）。
## 2.4 实现期契约澄清（向后兼容，已随实现落地）

1. **C-STORE@1 `symbols` 主键**：`symbol_id` 不含 commit，同一符号跨版本会重名，故主键改为自增 `id`，`UNIQUE(repo_id, commit_sha, symbol_id)`；`get_symbol(symbol_id)` 返回任一匹配 commit 的记录（按 `commit_sha, id` 升序取首条）。
2. **C-STORE@1 `test_runs`**：新增 `timed_out` 列，使 `RunResult` 可完整还原。
3. **C-CORE@1 覆盖率格式**：coverage.py 的 JSON `contexts` 为 `{line: [context, ...]}`（非 `{context: [line]}`）；解析时兼容两种方向，并归一化 pytest-cov 的 `<nodeid>|run` 后缀。
4. **C-ORCH@1 覆盖率兜底**：若整份覆盖率文件没有任何测试级上下文，编排层对 base 快照逐测试运行采集映射（上限 `MAX_PER_TEST_COVERAGE_RUNS = 40`），超出时追加 warning `coverage_per_test_capped` 并退回静态+保底来源。
5. **C-ORCH@1 产物路径**：`artifacts_root` 一律解析为绝对路径；执行器以快照目录为 cwd，相对路径会导致覆盖率与日志落入快照内部。
6. **C-EVAL@1 combo 策略**：保留四源全部理由，不过滤来源（`static` 只看静态源，`coverage` 只看覆盖源）。
## 2.5 第二轮契约（垂直切片之后）

环境更替（2026-11-14 第二轮）：Docker daemon 可用（29.9.1，本地已有 `python:3.13-slim`）；Ollama 可用模型为 `gemma4:12b`、`qwen3.5:4b`；本机 `8000/3000` 被其他项目占用，联调固定使用 `8100/3100`。

### C-GEN@1: 受限测试生成与双版本验证
- Provider / Consumers: `core/generation`、`core/validation`、`services/worker`（T14） / `services/api` 报告出参、T16 评测; Requirements: R10
- Public surface (exact):
  - `core/generation/model_adapter.py`：`ModelAdapter`（`suggest(request: GenerationRequest) -> list[ModelSuggestion]`）、`ModelSuggestion{test_name: str, content: str, rationale: str}`、`MockModelAdapter`（确定性）、`OllamaModelAdapter`（HTTP `/api/generate`，失败抛 `ModelUnavailableError(code="model_unavailable")`）、`default_model_adapter()` 按 `CHANGEPROOF_LLM` 选择。
  - `core/generation/generator.py`：`build_generation_request(changed, symbols, existing_tests, style_samples) -> GenerationRequest`；`static_check(content: str) -> list[str]`（返回违规项，空表示通过；禁止 `exec/eval/open/subprocess/socket/requests/os.system`，必须含至少一个 `def test_`）；`generate_test_suggestions(request, adapter) -> list[GeneratedTest]`。
  - `GeneratedTest{test_node_id: str, content: str, status: str, rationale: str, analysis_source: str = "model"}`；`status ∈ {pending_review, verified, rejected}`。
  - `core/validation/validator.py`：`validate_generated_tests(tests, base_snapshot, target_snapshot, job_factory, repeats: int = 2) -> list[ValidationOutcome]`；`ValidationOutcome{test_node_id, base_passed: bool, target_failed: bool, stable: bool, status, evidence: dict}`。
- Interaction semantics / status:
  - **verified 唯一判定**：`base_passed ∧ target_failed ∧ stable`（重复 `repeats` 次结果一致）；任一不满足 → `pending_review`；`base` 失败 → `rejected`。禁止用 target 结果反推 base。
  - 仅当 `AnalyzeOptions.generate_tests=True` 且存在未覆盖变更符号时进入 `test_generating → validating`。
  - 模型不可用或静态检查不通过 → `degraded_steps=['generation']`，任务仍可 `reported`。
  - 生成物写入报告 `model_suggestions`，且 `analysis_source="model"`；不得进入静态事实字段。
  - `verified` 结果写 `reports.payload_json.model_suggestions[].status` 与 `validation_evidence`。**READY**

### C-SAMPLE-V2@1: 多样本评测语料
- Provider / Consumers: `samples/shop_api_seed/**`、`scripts/bootstrap_samples.py`（T15） / T16 评测、T14 生成验证样例; Requirements: R11, R12
- Public surface (exact):
  - `samples/shop_api_seed/`：种子源码，≥20 个 `*.py`、≥15 个 pytest 用例，含 `app/core/`（高风险保底目录）、`app/models.py`（≥3 个模型）、`app/services/`（≥4 个模块）、`app/api/`（≥3 个路由文件）、`tests/`。
  - `scripts/bootstrap_samples.py`：幂等重建 `var/samples/shop_api`（`git init` → 基线提交打 tag `v1-baseline` → 每个样本从基线建分支做一次变更提交并打 tag `sample/<sample_id>`）；并写 `eval/samples/<sample_id>.json` 与 `eval/samples/lock.json`。
  - 样本 JSON 字段（全部必填）：`sample_id`（`^[a-z0-9_]+$`）、`repo_name`（固定 `shop_api`）、`base_ref`（固定 `v1-baseline`）、`target_ref`（`sample/<sample_id>`）、`change_kind`（`rename_field|rename_function|signature_change|route_param|shared_util|config|comment_only|cross_module`）、`expected_impact_files`、`expected_impact_symbols`、`expected_failing_tests`、`expected_no_behavior_change`（bool）、`high_risk_globs`。
  - `eval/samples/lock.json`：`{"shop_api": {"<sample_id>": {"base_sha": str, "target_sha": str}}}`。
  - 覆盖率要求：样本总数 ≥40；**八类** `change_kind`（枚举全部取值）每类 ≥5 组；`comment_only` 组 `expected_no_behavior_change=true` 且 `expected_failing_tests=[]`。
- Interaction semantics / status:
  - 真值只来自脚本中显式声明的注入点与人工核对，禁止由运行期预测结果回填。
  - 每次重建 sha 都变，代码与样本禁止写死 sha；一律通过 `lock.json` 读取。
  - 生成后 `var/samples/shop_api` 必须能在基线版本 `pytest` 全通过，且每个非 `comment_only` 样本的 `expected_failing_tests` 在 target 上真实失败。**READY**

### C-EVAL-V2@1: 批量评测与汇总
- Provider / Consumers: `scripts/run_eval.py`、`core/evaluation`（T16） / T17 演示、Web `/eval` 页; Requirements: R11
- Public surface (exact):
  - `scripts/run_eval.py --strategies full,neighbor,static,coverage,combo --samples eval/samples --out var/eval --refresh`
  - 缓存：每个样本的 artifacts 构建一次并存 `var/eval/cache/<sample_id>.json`（`--refresh` 重算）；五个策略复用同一 artifacts。
  - 输出：`var/eval/<strategy>.json`（`StrategyReport`，字段沿用 C-EVAL@1）与 `var/eval/summary.json`：
    `{"generated_at": str, "samples_total": int, "cache_hits": int, "strategies": [{"strategy","samples_total","recall_at_20","regression_recall","median_test_count_reduction","verified_rate"}], "subsets": {"regression_only": {...}, "no_behavior_change": {...}}}`。
  - 指标口径严格沿用 C-EVAL@1：`recall_at_20` 与 `regression_recall` 空预期返回 1.0；`median_test_count_reduction` 对 `total<=0` 的样本跳过。
- Interaction semantics / status:
  - 单样本异常写 `per_sample[].error` 并按未命中计，不中断整轮；缺 sha 记 `skipped_missing_sha`。
  - `verified_rate` 来自样本 artifacts 中的 `generated`（由 T14 的生成链路产生），无生成样本时为 0.0。**READY**

### C-DEVUP@1: 联调启动与冒烟
- Provider / Consumers: `scripts/dev_smoke.py`、`Makefile`（T17） / 演示与验收; Requirements: R12
- Public surface (exact):
  - `scripts/dev_smoke.py --api-port 8100 --web-port 3100 --timeout 300`：以子进程启动 `uvicorn services.api.main:app --port {api_port}`，轮询 `GET /api/health` 至 `status=="ok"`；用 `httpx` 依次 `POST /api/repos`（`var/samples/shop_api`）→ `POST /api/changesets`（sha 取 `eval/samples/lock.json` 第一组）→ `POST /api/changesets/{id}/analyze` → 轮询 `GET /api/tasks/{id}` 至 `reported|failed` → `GET /api/changesets/{id}/report`；再启动 `npm --prefix apps/web run dev -- -p {web_port}` 并轮询 `http://127.0.0.1:{web_port}/` 返回 200；打印 `SMOKE PASS|FAIL` 与每步耗时，退出码 0/1，并终止全部子进程。
  - `Makefile` 新增 `smoke`、`eval-all`、`docker-runner` 目标。
  - `docs/DEMO.md`：5～8 分钟演示脚本。
- Interaction semantics / status:
  - 端口默认 8100/3100，避开本机已占用的 8000/3000；不得修改 `services/api` 默认端口。
  - 冒烟使用真实 `uvicorn` 进程（BackgroundTasks 真实执行），不使用 `TestClient`。
  - 失败时保留 `var/smoke/` 下日志与最后一次任务 id。**READY**

### C-RUNNER-DOCKER@1: 容器执行启用
- Provider / Consumers: `services/runner`、`infra`（T18） / T17 冒烟、编排; Requirements: R8
- Public surface (exact):
  - 镜像 `changeproof-runner:latest`：`python:3.13-slim` + pytest + coverage + pytest-cov，`USER 65534:65534`，`WORKDIR /work`，`/artifacts` 可写。
  - `DockerExecutor.run(job)`：`--rm --network=none --cpus=1 --memory=512m --pids-limit=128 --user 65534:65534 -v {snapshot}:/work:ro -v {artifact_dir}:/artifacts`；容器内 cwd 固定 `/work`。
  - 覆盖率路径映射：宿主 `--cov-report=json:<host abs>` 改写为容器内 `/artifacts/<name>`，执行后从容器回读宿主 `artifact_dir`；`RunResult.log_path` 指向宿主日志，`isolation_mode="docker"`。
- Interaction semantics / status:
  - `docker info` 不可用时仍抛 `RunnerUnavailableError(code="docker_unavailable")`，编排降级本地。
  - 容器内 stdout 首行写 `network_enforced=true`（与本地模式的 `false` 区分）。
  - 超时以 `docker stop` + `RunStatus.TIMEOUT` 处理，不残留容器。**READY**
### C-SELECT@2: 选测来源收紧与路由变更映射（修订 C-CORE@1）
- Provider / Consumers: `core/indexer`、`core/impact`、`core/coverage`（T19） / `services/worker`、T16 评测; Requirements: R4, R5, R7
- Public surface: 沿用 C-CORE@1 的函数签名，不新增公开符号。
- 交互语义（唯一规则，替换 C-CORE@1 中同名描述）：
  1. `ROUTE` 符号的 `line_start/line_end` 必须同时覆盖装饰器行与处理函数 `def` 行；当 Diff 命中装饰器行（含路径/查询参数变化）时，`map_changes` 必须产出该 `ROUTE` 符号的 `ChangedSymbol`，其 `kind="route"`。
  2. `static_dependency` 仅在两种情形成立：测试文件 `tests/test_<stem>.py` 的 `stem` 等于受影响文件 basename（去扩展名）；或受影响文件路径与该测试文件路径相同。**不得**以"同目录"或"目录包含"作为条件。
  3. `high_risk_guard` 仅在受影响符号的 `file_path` 匹配 `high_risk_globs`，且测试 `stem` 包含该文件 basename（或 basename 包含 `stem`）时成立；覆盖率缺失时同样适用。**不得**无条件选中全部测试。
  4. 同一测试对同一 `source` 只保留一条理由（`detail` 最短者优先），避免界面重复。
  5. 覆盖率存在时 `covered_change` / `covered_impact` 优先展示，但三源并集结果不变。
- Ownership: provider 写 `core/indexer/**`、`core/impact/**`、`core/coverage/**`、`core/evaluation/strategies.py`；consumer 不改。
- 验收口径（由评测产出）：40 组语料上 `combo` 的 `regression_recall = 1.0`，`median_test_count_reduction ≥ 0.5`，`recall_at_20 ≥ 0.9`。**READY**
### C-ORCH@2: 任务推进与重试澄清（修订 C-ORCH@1 / C-STATE@1）
- Provider / Consumers: `services/worker`、`services/api`（实现期修正） / 全部触发方; Requirements: R2, R8, R9
- 交互语义（唯一实现）：
  1. `analyze_changeset(store, executor, changeset_id, options, task_id: str | None = None)`：传入 `task_id` 时直接推进该任务；未传时若存在 `status == "created"` 的任务则**继续推进该任务**（不得直接返回），仅当存在非 `created` 的非终态任务时才复用并返回。
  2. 重试：`POST /api/tasks/{id}/retry` 将 `status`/`stage` 置回 `created`，`resume_status` 保留原失败阶段用于展示，`retry_count += 1`，随后从头重跑全部阶段（各阶段产物按目录覆盖写，结果确定）。
  3. `ALLOWED_TRANSITIONS` 中 `failed → created` 合法；`failed` 的重试上限仍为 `MAX_RETRIES = 3`。
  4. `schedule_analysis(background, changeset_id, task_id, options)` 必须把 `task_id` 传给后台任务，避免后台再次创建任务。
- 验收：`POST /analyze` 后任务在真实 uvicorn 下从 `created` 推进到 `reported`，`GET /report` 返回完整载荷。**READY**
### C-EVAL-V3@1: 覆盖率逐测试兜底与生成验证接入
- Provider / Consumers: `scripts/run_eval.py`、`core/evaluation`（T20） / 演示、Web `/eval`、验收报告; Requirements: R7, R10, R11
- Public surface (exact):
  - 新增参数：`--coverage-per-test/--no-coverage-per-test`（默认开启）、`--with-generation/--no-generation`（默认开启）、`--coverage-cache var/eval/coverage_cache`、`--coverage-cache-version v1`。
  - 覆盖率兜底：当基线 `coverage.json` 解析后无测试级上下文时，对每个测试 `node_id` 单独执行
    `["python", "-m", "pytest", "-p", "no:cacheprovider", "-q", node_id, "--cov=.", "--cov-report=json:<abs>"]`（cwd=base 快照），
    读取 `executed_lines` 生成 `CoverageRecord(commit_sha=base_sha, test_node_id=node_id, file_path=posix 相对路径, lines)`；
    上限 `MAX_PER_TEST_COVERAGE_RUNS = 60`，超出记 warning `coverage_per_test_capped`。
  - 覆盖率缓存：键 `(base_sha, cache_version)`，文件 `var/eval/coverage_cache/<base_sha>.json`，内容 `{"cache_version":"v1","base_sha":...,"source":"per_test|contexts","records":[...]}`；40 组共享同一 base_sha 时只采集一次。
  - 生成验证：对 `expected_failing_tests` 非空的样本，在 artifacts 构建后调用 `build_generation_request(...)` → `generate_test_suggestions(request, default_model_adapter())` → `validate_generated_tests(tests, base_snap, target_snap, job_factory, repeats=2)`；结果写入 artifacts 的 `generated: [{test_node_id, status, rationale, analysis_source, validation_evidence}]`；模型不可用或静态检查不通过时 `generated=[]` 且 `degraded=["generation"]`，不中断评测。
  - `summary.json` 新增：`coverage_context_source`（`contexts|per_test|missing`）、`generated_samples`（实际产出 verified 的样本数）、`verification_failures`（未 verified 的样本数）。
- Interaction semantics / status:
  - 指标口径不变（沿用 C-EVAL@1）；`verified_rate` 由 `generated` 计算。
  - 逐测试覆盖率与生成验证都必须可被 `--no-*` 关闭；关闭时输出与 C-EVAL-V2@1 完全一致。
  - 样本级缓存命中时不得重复执行 pytest 生成或覆盖率采集。
- 验收：40 组语料默认参数下单次运行 ≤15 分钟；`coverage` 策略 `regression_recall ≥ 0.8`；回归子集 `verified_rate ≥ 0.8`；`combo` 三项指标不下降。**READY**

### C-WEB-E2E@1: 前端端到端用例与演示门禁
- Provider / Consumers: `apps/web`、`Makefile`、`docs/DEMO.md`（T21） / 验收; Requirements: R6, R9, R12
- Public surface (exact):
  - `apps/web/playwright.config.ts`：`testDir: "e2e"`、`baseURL: http://127.0.0.1:3100`、`webServer: {command: "npm run dev -- -p 3100", url: "http://127.0.0.1:3100", reuseExistingServer: true, timeout: 120000}`、`use: { headless: true }`。
  - `apps/web/e2e/analysis.spec.ts`：单条用例，前置 API 已在 `http://127.0.0.1:8100` 运行（由 `make e2e` 启动）：打开 `/` → 填写仓库路径 `var/samples/shop_api` 并提交 → 选择 base/target 提交 → 创建 changeset → 点击分析 → 等待 `task-status` 出现 `reported`（120s）→ 断言 `impact-graph` 可见且节点数 > 0 → 打开报告页断言 `report-summary` 与 `report-risks` 非空。
  - 前端 `data-testid` 约定：`repo-path-input`、`repo-submit`、`commit-base`、`commit-target`、`changeset-create`、`analyze-button`、`task-status`、`impact-graph`、`report-summary`、`report-risks`。
  - `Makefile` 新增 `e2e`：启动 uvicorn（8100，独立 `var/e2e/` 库）→ 在 `apps/web` 执行 `npx playwright test` → 清理子进程；依赖安装 `npm --prefix apps/web i -D @playwright/test`，浏览器安装 `PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright npx playwright install chromium`。
  - `scripts/e2e_http.py`：浏览器不可用时的等价断言（httpx 拉取 `/`、`/changesets/{id}`、`/changesets/{id}/report` HTML 并断言关键文案），输出 `HTTP E2E PASS|FAIL`。
- Interaction semantics / status:
  - 浏览器未安装时 `analysis.spec.ts` 必须 `test.skip()` 而非失败，`make e2e` 回退执行 `scripts/e2e_http.py`。
  - 用例只读 `eval/samples/lock.json` 的 sha，不改样本与数据库；每次运行使用独立 `var/e2e/` 目录。
- 验收：`make e2e` 在浏览器可用时输出 1 passed；不可用时输出 `HTTP E2E PASS`。**READY**
### C-CORE-DATA@1: 提交枚举与仓库导入幂等（实现期澄清）
- Provider / Consumers: `core/repo`、`services/api/routes_repos`（实现期修正） / 前端、评测、冒烟; Requirements: R1
- 交互语义（唯一实现）：
  1. `list_commits(local_path, limit)` 必须跨全部 ref 枚举（`git rev-list --all` 等价语义），因为样例仓库的变更提交位于各 `sample/*` 分支；只遍历 HEAD 会让前端与评测看不到 `lock.json` 中的 target sha。
  2. `POST /api/repos` 的查重与落库必须使用同一路径形态：先 `Path(local_path).expanduser().resolve()`，再查重；重复导入返回 200 与既有 `repo_id`，不得触发 `repositories.local_path` 唯一键冲突。
- 验收：`var/samples/shop_api` 导入后 `GET /api/repos/{id}/commits?limit=100` 返回 ≥40 条；同一相对路径重复导入返回 200 且 `repo_id` 不变。**READY**
### C-GEN@2: 生成模板扩展（修订 C-GEN@1）
- Provider / Consumers: `core/generation/**`、`scripts/run_eval.py`、`services/worker/orchestrator.py`（T22） / 评测、报告; Requirements: R10
- 交互语义（唯一实现）：
  1. 生成输入为"未被任何测试覆盖的变更符号全集"，不得只取目标版本中消失的符号。
  2. 模板必须可判定（base 通过、target 因目标变更失败）：
     - `existence`：`hasattr`/属性访问存在性（rename、delete）。
     - `constant_value`：从 base 快照源码提取常量或默认值字面量，断言运行期值等于 base 值（config、shared_util）。
     - `signature_contract`：从 base 源码提取函数参数名，用 `inspect.signature` 断言参数仍在（signature_change）。
     - `route_contract`：从 base 快照提取路由方法/路径/响应字段，用 `TestClient` 断言仍返回 200 且含原字段（route_param）。
     - `import_contract`：断言模块可导入且目标符号存在（cross_module）。
  3. 静态白名单：仅允许 `pytest`、`inspect`、`json`、`fastapi.testclient`、目标模块导入；禁止文件/网络/子进程/`exec`/`eval`。
  4. 状态判定仍按 C-GEN@1（base 通过 ∧ target 失败 ∧ 重复稳定 → `verified`）。
- 验收：40 组语料中 `verification_failures ≤ 10`（≥25 组产出 verified）；`combo` 三项指标不回退；总评测 ≤15 分钟。**READY**

### C-SELECT@3: 覆盖来源收敛（修订 C-SELECT@2）
- Provider / Consumers: `core/coverage/selector.py`（T23） / 编排、评测; Requirements: R7
- 交互语义：
  1. `covered_impact` 仅对 `distance == 1` 的受影响符号成立；`distance > 1` 的覆盖证据只用于报告展示，不作为选中理由。
  2. 若某测试的唯一理由是 `covered_impact` 且 `distance > 1` → 不选中该测试。
  3. `covered_change`、`static_dependency`、`high_risk_guard` 规则不变。
- 验收：`combo` 的 `regression_recall = 1.0` 且 `median_test_count_reduction ≥ 0.55`；`coverage` 策略 `regression_recall ≥ 0.8`。**READY**

### C-GATE@1: 一键门禁
- Provider / Consumers: `scripts/gate.py`、`Makefile`（T24） / 交付验收; Requirements: R12
- Public surface (exact): `python scripts/gate.py [--skip-docker] [--skip-e2e] [--refresh]` 顺序执行
  1. `compile`：`python -m compileall -q core services scripts`
  2. `eval`：`scripts/run_eval.py`（默认五策略，缓存；`--refresh` 透传）
  3. `smoke`：`scripts/dev_smoke.py --api-port 8100 --web-port 3100`
  4. `e2e`：`apps/web` 下 `npx playwright test`，浏览器不可用回退 `scripts/e2e_http.py`
  5. `docker-runner`：`infra/runner/verify_isolation.py`（`--skip-docker` 跳过）
- Interaction semantics / status:
  - 每步输出 `PASS|FAIL|SKIP` 与耗时，汇总写 `var/gate/summary.json`；任一 FAIL 退出码 1。
  - 默认不重建样本、不删主库；仅使用各自独立 `var/` 子目录。
  - `make gate` 作为等价入口；总时长目标 ≤8 分钟（样本缓存命中）。
- 验收：单次 `python scripts/gate.py` 退出码 0，五步全部 PASS（docker 不可用时 SKIP）。**READY**
### C-SELECT@4: 定义行覆盖不做入选依据（修订 C-SELECT@3）
- Provider / Consumers: `core/coverage/selector.py`（T23） / 编排、评测; Requirements: R7
- 依据：`FILE`/`FIELD`/`CLASS` 定义行在 import 时被执行，几乎被全部测试覆盖，`covered_change` 对这些符号没有区分度（40 组实测：禁用 `covered_impact` 后选中数无变化，膨胀来自 `covered_change`）。
- 交互语义：
  1. `covered_change` 仅对 `kind ∈ {FUNCTION, METHOD, ROUTE, TEST}` 的变更符号产生选中理由。
  2. 变更符号为 `FILE`/`FIELD`/`CLASS` 时，覆盖率证据只写入报告（`SelectionReason.source="coverage_evidence"`，不参与入选判定）。
  3. 若收紧后某样本选中集为空，必须由 `static_dependency`/`high_risk_guard` 兜底，不得返回空集（仓库存在测试时）。
  4. `covered_impact` 仍按 C-SELECT@3 限制在 `distance == 1`。
- 验收：`combo.regression_recall = 1.0` 且 `combo.median_test_count_reduction ≥ 0.55`；`coverage.regression_recall ≥ 0.8`；若无法同时满足，必须上报实测数据，不得自行放宽阈值。**READY**
### C-SELECT@5: 静态直连改为精确同名（修订 C-SELECT@4）
- Provider / Consumers: `core/coverage/selector.py`（T26） / 编排、评测; Requirements: R7
- 依据：40 组实测中，`cross_module_service_fee_line` 与 `signature_change_apply_tax_floor` 的选中集合 25/52 全部来自 `static_dependency`，其匹配是"测试 stem 包含受影响文件 basename"（如 `test_api_orders` → `orders`），导致 5 个模块 × 5 个测试的并集。
- 交互语义：
  1. `static_dependency` 仅在测试 stem **完全等于**受影响文件 basename（去扩展名）时成立；禁止子串/包含匹配。
  2. 受影响文件位于 `tests/` 目录（即测试文件自身）时不产生 `static_dependency` 理由；测试文件不作为选测依据。
  3. `high_risk_guard` 的匹配同样改为"完全等于"（stem 等于高风险文件 basename），不得使用包含匹配。
  4. `covered_change`（C-SELECT@4）、`covered_impact`（C-SELECT@3）规则不变。
- 验收：`combo.regression_recall = 1.0` 且 `combo.median_test_count_reduction ≥ 0.55`；`coverage.regression_recall ≥ 0.8`。若召回出现下降，必须回退本条中导致漏选的子项并上报数据，不得同时放宽降幅阈值。**READY**
### C-SELECT@6: 基于依赖边的静态选测（修订 C-SELECT@5）
- Provider / Consumers: `core/coverage/selector.py`、`core/indexer/indexer.py`（T27） / `services/worker`、`scripts/run_eval.py`; Requirements: R7, R5
- Public surface (exact): `select_tests(changed, impacts, symbols, tests, coverage, high_risk_globs=("app/core/**",), strategy="combo", edges: Sequence[DependencyEdge] | None = None)`（新增末尾可选参数，默认 `None`，向后兼容）。
- 交互语义：
  1. 传 `edges` 时优先使用依赖图：把每条边映射为 `(source_file, target_file)`（端点的文件路径由 `symbols` 中 `symbol_id` 反查；无法反查时用边的 `file_path` 作为 source 侧）。
  2. 若测试文件到受影响文件存在长度 ≤2 的路径（边类型仅 `IMPORTS|DEPENDS_ON|CALLS|ROUTES_TO`），该测试获得 `static_dependency` 理由（`detail` 含路径长度）。
  3. 无 `edges` 或未命中时，回退 C-SELECT@5 的"测试 stem 完全等于受影响文件 basename"规则。
  4. 受影响文件位于 `tests/` 时仍不计入静态源（保持 C-SELECT@5 第 2 条）。
- 索引器要求：每个测试文件到其 import 的产品模块必须有模块级 `IMPORTS` 边；`TestClient` 调用的路由保留 T19 的 `DEPENDS_ON` 边。
- 调用方要求：`services/worker/orchestrator.py` 与 `scripts/run_eval.py` 必须把当前 commit 的 `edges` 传入 `select_tests`。
- 验收：`static.regression_recall ≥ 0.8`；`combo.regression_recall = 1.0`、`combo.median_test_count_reduction ≥ 0.50`、`recall_at_20 ≥ 0.9`。**READY**
- 补认（实现期）：`core/evaluation/strategies.py::apply_strategy` 新增可选 `edges` 参数并透传给 `select_tests`；这是评测链路唯一的间接调用点，属本契约的必要配套改动。

### C-GEN@3: 边界类变更的生成模板（修订 C-GEN@2）
- Provider / Consumers: `core/generation/**`（T28） / 评测、报告; Requirements: R10
- 新增模板（断言一律取 base 事实，target 仅用于定位改动）：
  1. `env_contract`：从 base 源码提取 `os.getenv("KEY")` / `os.environ["KEY"]` 的键名字面量；若模块存在同名常量（`*_ENV`、`ENV_*`、`*_KEY`）则断言其值等于 base 键名；否则以 `inspect.getsource(module)` 含该键名做弱断言，并在 `rationale` 标注 `weak:source-scan`。
  2. `state_contract`：从 base 源码推断目标类构造所需参数与字面量默认值，构造最小实例后断言属性或方法返回值等于 base 表达式结果（无法构造时不得生成）。
  3. `guard_contract`：从 base 源码提取比较常量（`> N`、`< N`、`>= N`、`<= N`），对 `N-1 / N / N+1` 断言行为（抛异常或返回值）与 base 语义一致。
- 约束：每样本最多 8 条建议；无法确定期望值的变更**不得生成**（返回空而不是伪造）；静态白名单沿用 C-GEN@2。
- 验收：`verification_failures ≤ 3`；`combo` 三项指标不回退。**READY**
### C-IMPACT@1: 语义等价映射（根治注释假阳性）
- Provider / Consumers: `core/impact`、`core/evaluation/metrics`、`scripts/run_eval.py`（T29） / 编排、评测、前端; Requirements: R4, R5
- 交互语义（唯一实现）：
  1. 对每个候选符号（`kind != file`），取 base 与 target 中该符号行区间内的源码，用 `ast.parse` + `ast.dump`（`include_attributes=False`）比较；比较前把模块/类/函数的第一条字符串语句（docstring）归一化为固定占位符。
  2. AST 等价 → **不产生**该符号的 `ChangedSymbol`（注释、空白、纯格式变化不算变更）。
  3. 文件内所有符号等价且文件级 AST 也等价 → 不产生文件级兜底符号，artifacts 记 `semantics_unchanged=true`。
  4. 源码无法解析时保守处理：仍视为变更，`confidence=inferred`，记 `warning=ast_unavailable`。
- 指标：`core/evaluation/metrics.py` 新增 `no_behavior_change_summary(per_sample) -> {"samples": int, "affected_symbols": int}`；`summary.json` 的 `subsets.no_behavior_change` 含该字段。
- 验收：5 个 `comment_only` 样本的 `changed_symbols` 总数为 **0**，其 `selections` 为空或仅剩保底来源；`combo` 三项指标不回退。**READY**

### C-SELECT@7: 覆盖来源 kind 口径统一（修订 C-SELECT@6）
- Provider / Consumers: `core/coverage/selector.py`、`core/evaluation/strategies.py`（T30） / 编排、评测; Requirements: R7
- 交互语义：
  1. 单一常量 `_COVERAGE_SELECTABLE_KINDS = {function, method, route, test}` **同时**约束 `covered_change` 与 `covered_impact`；`FILE`/`FIELD`/`CLASS` 不产生任何覆盖入选理由（只写 `coverage_evidence` 展示）。
  2. `static_dependency` 与 `high_risk_guard` 规则不变（C-SELECT@6 / @5）。
  3. FILE 级（整文件）影响只能通过静态依赖或高风险保底入选。
- 验收：`config_tax_rate_default` 选中数 ≤ 25（当前 34）；`combo.regression_recall = 1.0`、`combo.median_test_count_reduction ≥ 0.50`、`coverage.regression_recall ≥ 0.8`。**READY**

### C-RUNNER@2: 执行计数单一来源（修订 C-RUNNER@1）
- Provider / Consumers: `services/runner`、`services/worker`、`infra/runner`（T31） / 编排、冒烟、门禁; Requirements: R8
- 交互语义：
  1. 执行器统一负责计数：除非命令已含 `--junitxml`，否则追加 `--junitxml <artifact_dir>/<job_id>.junit.xml`，解析后填 `selected/passed/failed/error/skipped`。
  2. 无 junit 文件时计数全 0 并在日志追加 `# counts_missing=true`（**不得**回退到覆盖率上下文或 stdout 解析）。
  3. 删除 `orchestrator` 的 `_junit_counts`/`_with_counts`/`_with_junit` 与日志解析；删除 `executor` 的 `_coverage_test_count` 回退；`infra/runner/verify_isolation.py` 只读 `RunResult`。
  4. `parse_pytest_summary` 保留为纯工具，但不再参与 `RunResult` 计数。
- 验收：新增 `tests/test_runner_counts.py` 覆盖正常 / junit 缺失 / 超时三态；`make smoke` 的 target run 显示 `failed_count ≥ 1`。**READY**

### C-GATE@2: 门禁纳入指标断言（修订 C-GATE@1）
- Provider / Consumers: `scripts/gate.py`（T32） / 交付验收; Requirements: R12
- 交互语义：`eval` 步结束后读 `var/eval/summary.json` 并断言，任一不满足即该步 FAIL、退出码 1：
  1. `combo.regression_recall == 1.0`
  2. `combo.median_test_count_reduction ≥ 0.50`
  3. `combo.recall_at_20 ≥ 0.90`
  4. `verification_failures ≤ 3`
  5. `static.regression_recall ≥ 0.80`
  6. `subsets.no_behavior_change.affected_symbols == 0`
- 断言失败时把期望值、实际值与样本清单写入 `var/gate/summary.json` 该步 `detail`。
- 验收：指标达标时退出码 0；用临时副本篡改阈值/summary 后该步 FAIL（一次性验证，不写入仓库）。**READY**
### 第六轮根治的实现澄清与实测
- **C-IMPACT@1 归一化策略**：`_normalized_ast` 采用**删除 docstring 语句**（而非替换内容），覆盖"新增/修改/删除 docstring"三种情形；并在符号级判定前先做**整文件 AST 等价快速判断**，避免按符号行切片不完整导致的误判。
- **影响传播补充**：`compute_impact` 对 `kind=FILE` 的变更符号做**种子展开**（等价于该文件全部符号）。原因：模块级 import/call 边的 target 是具体函数/字段符号，只从 FILE 符号出发会让反向传播整条断链（实测曾漏掉 `app/api/health.py`）。
- **C-SAMPLE-V2@1 标注修正**：5 个 `comment_only` 样本的 `expected_impact_files`/`expected_impact_symbols` 改为空列表——无行为变更的样本不应带预期影响。生成器（`scripts/bootstrap_samples.py`）与 JSON 已同步修改。
- **C-RUNNER@2 计数**：执行器统一追加 `--junitxml`，缺失时计数全 0 且日志记 `counts_missing=true`；orchestrator 不再自行解析日志或覆盖率。
- **实测（2026-11-16，`cache_hits=0/40`）**：`recall_at_20=1.000`；`combo.regression_recall=1.000`、`median_test_count_reduction=0.538`、`verified_rate=0.667`；`static.regression_recall=1.000`；`coverage.regression_recall=0.967`；`verification_failures=1`；`no_behavior_change.affected_symbols=0`。`python scripts/gate.py` 退出码 0（58.6s，docker-runner SKIP）。新增 `tests/` 12 个不变式用例全部通过。
## 2.6 P0 收尾契约

### C-SECOND-REPO@1: 第二示例仓库与泛化验证
- Provider / Consumers: `samples/blog_api_seed/**`、`scripts/bootstrap_blog.py`、`eval/blog/**`（T33） / 泛化评测、验收文档; Requirements: R11, R12
- 形态：FastAPI 应用，**包式布局**（`src/blog/`）、SQLAlchemy `relationship`、FastAPI `Depends` 依赖注入、多路由文件；与 `shop_api` 的结构差异必须明显。≥8 个源文件、≥8 个 pytest 用例。
- 样本：≥5 组变更（`rename_field`、`signature_change`、`route_param`、`shared_util`、`comment_only`），真值人工标注于 `eval/blog/<sample_id>.json`；`eval/blog/lock.json` 记录 `{repo: {sample_id: {base_sha, target_sha}}}`。
- 评测：`scripts/run_blog_eval.py` 复用 `core.repo/indexer/impact/coverage` 与 `core.evaluation`，输出 `var/eval_blog/summary.json`（指标口径同 C-EVAL@1）。
- 验收：第二仓库索引无异常；5 组样本 base 全通过、target 失败集与标注一致；`combo.regression_recall = 1.0`。**READY**

### C-AUTH@1: 最小权限控制（默认关闭）
- Provider / Consumers: `services/api/auth.py`、`services/api/deps.py`（T34） / 全部 API 端点、前端; Requirements: R12
- 交互语义：
  1. 默认 **关闭**（不改变现有演示与门禁行为）；`CHANGEPROOF_AUTH=token` 开启。
  2. 开启后，除 `GET /api/health` 外的所有 `/api/*` 请求必须携带 `Authorization: Bearer <token>`；token→角色映射来自 `CHANGEPROOF_API_TOKENS`（格式 `token:role,token:role`，角色仅 `viewer|operator`）。
  3. 缺失或未知 token → 401 `unauthorized`；`viewer` 仅允许 GET，`operator` 允许全部；角色不足 → 403 `forbidden`。
- 验收：`tests/test_auth.py` 覆盖 关闭放行 / 无 token 401 / viewer 只读（GET 200、POST 403）/ operator 可写（POST 201）四态；`make smoke` 在默认关闭下行为不变。**READY**

### C-FAULT@1: 故障注入与降级
- Provider / Consumers: `scripts/fault_injection.py`、`tests/test_fault_injection.py`（T35） / 验收; Requirements: R2, R8, R10, R12
- 五类故障及期望：
  1. 超时：`python -c "import time;time.sleep(8)"`、`timeout_s=1` → `RunStatus.TIMEOUT`。
  2. 依赖缺失：快照中制造 import 失败 → 任务 `failed`，错误码与日志路径保留。
  3. Runner 失联：daemon 不可用 → `RunnerUnavailableError(code="docker_unavailable")`，编排降级 `isolation_mode=local` 且报告含 `degraded_steps`。
  4. 覆盖率缺失：无覆盖率路径 → `coverage_missing=true`，选测仍有静态/保底理由。
  5. 模型失败：`CHANGEPROOF_LLM=ollama` + 不可达地址 → `degraded_steps=["generation"]`，任务仍 `reported`。
- 验收：`tests/test_fault_injection.py` 全通过；`scripts/fault_injection.py` 输出 5/5 PASS。**READY**

### C-ACCEPT@1: 验收文档
- Provider / Consumers: `docs/ACCEPTANCE.md`、`README.md`（T36） / 交付; Requirements: R12
- 内容：P0 十四项逐项对照（实现位置 + 复现命令 + 实测值）、P1/P2 未完成项清单、启动与门禁命令、已知边界（coverage 召回 0.967、verified_rate 口径、剩余 1 组生成验证）。
- 约束：文档中的每条命令与端口必须与当前实现一致（`python scripts/gate.py`、`scripts/dev_smoke.py --api-port 8111 --web-port 3111`、`scripts/run_eval.py --refresh`），不得虚构。**READY**
### P0 收尾实测（2026-11-16，实现已完成）
- **T33 第二示例仓库**：`var/samples/blog_api`（包式布局 `src/blog/`、SQLAlchemy `relationship`、FastAPI `Depends`），5 组样本实测 `combo.recall_at_20=1.000`、`regression_recall=1.000`、降幅 0.556；`comment_only` 组 selected=0。
- **T34 最小权限控制**：`services/api/auth.py` 中间件（默认关闭，`CHANGEPROOF_AUTH=token` 开启），`tests/test_auth.py` 四态 4 passed。
- **T35 故障注入**：`scripts/fault_injection.py` 输出 5/5 PASS（超时/依赖缺失/Runner 失联/覆盖率缺失/模型失败）。
- **T36 验收文档**：`docs/ACCEPTANCE.md`（P0 十四项对照 + 实测值 + 已知边界）与 `README.md`（启动/门禁/评测命令）已更新。
- **全量回归**：`tests/` 21 个用例全部通过；`python scripts/gate.py` 退出码 0（61.8s）。
- **追踪器状态**：T33–T36 因平台 429 限流无法派发验收 agent，状态仍为 pending；实现与实测已完成，产物可按上述命令复核。
### P0 收尾复验修复（2026-11-17，验收差异全部闭合）
- **T33 根因与修复**：`samples/blog_api_seed/src/blog/db.py` 由 `sqlite:///:memory:`（每连接独立库）改为 `StaticPool + check_same_thread=False`，修复 TestClient 请求线程拿不到种子数据的问题（base 曾 2 failed）。已重建 `v1-baseline` 与各 `sample/*` tag 到修复后的代码；`eval/blog/*.json` 标注按实测修正（`blog_rename_post_title` 真实失败 7 个，`blog_shared_slugify_underscore` 移除不会失败的 `test_list_posts`）；`scripts/run_blog_eval.py` 新增"实际失败集 == 标注"硬校验（不一致输出 `[annotation_mismatch]` 并退出码 1），防止只按标注自证。复验输出 `[ok] 5 组样本的 base 全通过、target 失败集与标注一致`，`combo.recall_at_20=1.000`、`regression_recall=1.000`、降幅 0.556。
- **T34 契约差异**：`services/api/auth.py` 对 `/api/health` 仅豁免 GET/HEAD（原全方法豁免）；`tests/test_auth.py` 的 operator 改用真实仓库路径断言 200/201，并新增 POST health 鉴权用例。实测 5 passed。
- **T35 编排级断言**：`DockerExecutor.__init__` 构造时探测 daemon，使 `services/api/deps.get_executor()` 真正降级为 `LocalProcessExecutor`（故障用例同时断言 `docker_unavailable` 与 `degraded_local=True`）；模型失败断言任务 `reported` 且 `degraded_steps=["generation"]`；依赖缺失断言任务 `failed` 且 `error_code="bad_job"`。实测 `FAULT INJECTION 5/5 PASS`。
- **T36 文档**：`docs/ACCEPTANCE.md` 删除 P1 清单中的"T33 进行中"条目，新增"第二示例仓库（泛化验证）"小节（含实测值）。
- **全量回归**：`tests/` 22 个用例全部通过；`python scripts/gate.py` 退出码 0（59.9s，docker-runner PASS）。
- **追踪器**：T33–T36 均为 completed（与修复后的产物一致）。
## 2.7 P1 补全与真实仓库适配

### C-SELECT@8: 目录镜像与空集保底（修订 C-SELECT@7）
- `_static_relation` 新增第三种静态关系：目录镜像（`tests/**/test_x.py` ↔ `app/**/x.py`，即 `tests/` → `app/` 镜像并去掉 `test_` 前缀后与受影响文件相等）。真实仓库常用该布局。
- `fallback_full_selection(changeset_id, tests, detail="空集保底：全量测试")`：仅在**编排层**、且"存在变更符号且仓库有测试"时，`select_tests` 返回空集才调用；结果标记 `strategy="fallback_full"`、理由 `high_risk_guard`。评测基线（`run_eval`/`run_blog_eval`）**不调用**该保底，保证策略对比不被掩盖。
- 验收：真实仓库删除型变更下 `selected_total > 0`；纯非 Python 变更（`changed=0`）不触发保底。

### C-REAL-REPO@1: 真实开源仓库适配
- `scripts/run_real_eval.py --repo <path> --base <sha> --target <sha>`：在完整真实仓库上跑"快照 → 索引 → diff → 变更映射 → 影响传播 → 测试选择"，输出 `var/eval_real/summary.json`。
- 索引器 `_resolve_module` 支持"源码根不在仓库根"的唯一后缀回退（如 `app.core.db` → `backend/app/core/db.py`；歧义时返回 `None`，不猜）。
- 实测（`tiangolo/full-stack-fastapi-template`，`f8dd304^..f8dd304`）：索引 234/224 符号、220 条边、58 个测试、7 个变更符号、5 条影响、选测 58（空集保底）；**执行阶段未启用**（真实仓库测试依赖 PostgreSQL），summary 中 `execution_note` 明确标注。
- 验收：`tests/test_real_repo.py`（目录镜像 2 例、保底 1 例、真实仓库 1 例；未克隆时 skip）。

### C-CI@1: CI 门禁
- `.github/workflows/gate.yml`：install backend/frontend → bootstrap samples → `pytest tests` → `fault_injection.py` → `gate.py --skip-docker --skip-e2e`。
- e2e 与 docker-runner 由本地或定期任务执行（CI 无 daemon，且浏览器安装脆弱），不作为 CI 硬门禁。
- 验收：YAML 可解析（9 steps），命令与本地脚本一致。
### C-DI@1: FastAPI 依赖注入图
- `core/indexer` 在处理函数参数默认值中提取 `Depends(x)`（精确，`Confidence.CERTAIN`），并对每个文件做 `ast.walk` 扫描收集 `Depends(x)`（覆盖 `Annotated[..., Depends(x)]` 别名，`Confidence.INFERRED`）。
- 每个路由处理器（handler）向依赖函数建 `DEPENDS_ON` 边（`analysis_source=framework`），使"改依赖 → 相关路由受影响"可传播。
- 实测（真实仓库）：边数 220 → 351。
- 已知边界：模块级变量引用（如 `engine`）没有独立边类型，`core/db.py` 的改动不会经 `engine` 传播到使用方；这是本轮真实仓库影响面仍为 5 条的原因，需新增 `USES_GLOBAL` 边才能闭合。

### C-MULTILANG@1: TypeScript 索引级支持（P2 部分）
- `scripts/ts_index.cjs` 使用 `apps/web/node_modules/typescript` 解析 `.ts/.tsx`（无需新增依赖），输出 `{symbols:[{id,kind,name,file,line}], edges:[{source,target,type,line}]}`。
- `core/indexer/typescript.py::index_typescript` 转换为共享 `Symbol`/`DependencyEdge`（`metadata={"language":"typescript"}`，ID 规则 `<file>::<name>`）。
- `index_repository(..., include_typescript: bool = False)` 默认关闭，开启时合并；不可用时写入 `typescript_unavailable` warning，不失败。
- **范围声明**：只做索引级支持（函数/类/箭头函数/导入/测试文件），**不执行 TypeScript 测试**，因此 TS 变更只产出符号与影响，不进入执行验证。
- 实测（真实仓库）：总符号 575（Python 224 + TS 351），TS 边 242。
- 验收：`tests/test_real_repo.py::test_typescript_index_in_real_repo`（工具链或真实仓库缺失时 skip）。
## 2.8 模块级全局引用与容错解析（真实仓库影响面闭合）

### C-IMPACT@2: USES_GLOBAL 边与降级解析
- **枚举扩展（向后兼容）**：`EdgeType` 新增 `USES_GLOBAL="uses_global"`；落库值为字符串，旧数据与既有边不受影响。
- **`_collect_global_uses`**：对每个函数/方法，收集函数体中 `Load` 上下文的 `Name`；若该名字属于**同文件模块级变量**（`Assign`/`AnnAssign` 目标）、且不是参数也不是函数内局部赋值，则建边 `函数符号 → 文件 FILE 符号`（`analysis_source=ast`、`confidence=inferred`）。模块级变量变更落在 FILE 符号上，该边让"改全局常量/engine → 使用它的函数"可被反向传播命中。
- **范围声明**：跨文件的全局引用仍由 `IMPORTS` 边承担；该边只补"同文件内使用"这一段。
- **容错解析 `_parse_lenient`**：`ast.parse` 语法不兼容时只保留顶格 `import`/`from` 行构造最小模块，**并保留该文件**。关键修复：`index_repository` 不再因"有 warning"而 `continue` 丢弃降级结果（此前会把整片导入边断掉）。
- 实测（真实仓库，`f8dd304^..f8dd304`）：`impacts` **5 → 12**（新增 `backend/app/api/deps.py` d=1、`backend/app/api/routes/items.py` d=2），`selected` **58 → 48**；`api/deps.py` 此前因 `except A, B` 语法整文件失败，现已降级保留导入边。
- 验收：`tests/test_uses_global.py`（USES_GLOBAL 指向模块 FILE 符号；降级解析保留 FILE 符号/导入边/告警）2 例通过。
## 2.9 全链路异常注入（P2 收尾）

### C-FAULT@2: 七类故障注入
- 在 C-FAULT@1 的五类基础上新增两类，共 **7 类**，同一组场景由 `scripts/fault_injection.py` 与 `tests/test_fault_injection.py` 复用：
  1. `timeout`：超时 → `TIMEOUT` + `timed_out=True`。
  2. `dependency_missing`：pytest 命令不存在 → 任务 `failed` 且 `error_code="bad_job"`。
  3. `runner_unavailable`：daemon 失联 → `docker_unavailable`，且 `deps.get_executor()` 降级为本地（`degraded_local=True`）。
  4. `coverage_missing`：无覆盖率 → `coverage_missing=true` 且选测非空。
  5. `model_failure`：模型不可达 → 任务仍 `reported`，`degraded_steps=["generation"]`。
  6. **`snapshot_missing`（新增）**：快照目录不存在 → `RunnerUnavailableError(code="bad_job")`，不得静默成功。
  7. **`git_command_failed`（新增）**：非法提交导出快照 → `GitCommandError(code="git_command_failed")`。
- **并发幂等不在故障注入中重复模拟**：同一 changeset 的重复触发由 API 层 `active_task_for_changeset`（复用非终态任务）保证，属 C-ORCH@2 范围。
- 实测：`FAULT INJECTION 7/7 PASS`；`tests/test_fault_injection.py` **7 passed**。
- CI 的 `gate.yml` 已包含 `scripts/fault_injection.py`（7 类全跑）。
## 2.10 testmon 参考基线对照（P1）

### C-TESTMON@1: 与 pytest-testmon 的对照
- `scripts/run_testmon_eval.py`：对每个样本在同一工作副本中 checkout base → 全量建 testmon 基线（清 `.testmondata`）→ checkout target → `pytest --testmon` 只跑受影响测试；解析 junit 得到执行集合，计算召回与降幅，输出 `var/eval_testmon/summary.json` 并与 `var/eval/summary.json` 的 combo 对照。
- 实测（40 组，2026-11-17）：
  - testmon：`regression_recall=0.881`、`median_test_count_reduction=0.904`、选测总数 **267**。
  - combo：`regression_recall=1.000`、`median_test_count_reduction=0.538`。
- testmon 召回缺口（7 组）：`config_tax_rate_default` 0.25、`config_free_shipping_threshold` 0.50、`config_default_currency` 0.67、`rename_field_product_price` 0.33、`route_param_order_by_id_prefix` 0.50、`cross_module_order_item_default` 0.00、`shop_api_rename_q1` 0.00。
- **结论**：testmon 以更高降幅（90.4%）换取召回下降（88.1%）；本项目按"宁可冗余不可漏"选择 100% 召回 + 53.8% 降幅。属设计取舍，不是工具优劣。
- **约束**：testmon 对照是离线基准，不进入 `gate`（避免与主评测争用样例仓库工作区）；脚本结束强制 `git checkout -f main`。