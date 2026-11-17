# ChangeProof 任务清单（垂直切片 v1）

项目根：`D:\bin\changeproof`。契约见同目录 `design.md` §2.2，全部为 READY 且已冻结。

## T1: 工程骨架与共享契约层

- **Objective**: 建立可安装的 Python 工程骨架，并落地全部共享类型、符号 ID 规则、仓储协议与任务状态机定义。
- **Requirements**: R3, R4, R5, R7, R9
- **Write set**: `pyproject.toml`, `README.md`, `.env.example`, `.gitignore`, `core/__init__.py`, `services/__init__.py`, `core/schema/__init__.py`, `core/schema/types.py`, `core/schema/ids.py`, `core/schema/protocols.py`, `core/schema/state.py`
- **Provides**: C-SCHEMA@1, C-STATE@1 (design.md §2.2)
- **Consumes**: none
- **Blocked by**: none
- **Deliverable**: 可 `pip install -e .` 的 `pyproject.toml`（包 `core`、`services`，依赖 fastapi/uvicorn/sqlalchemy/gitpython/pytest/coverage/httpx/pydantic）；`core.schema` 导出 `SymbolKind`/`EdgeType`/`AnalysisSource`/`Confidence`/`ChangeType`/`RunStatus` 枚举、`Symbol`/`DependencyEdge`/`FileDiff`/`ChangedSymbol`/`ImpactResult`/`TestCaseInfo`/`CoverageRecord`/`SelectionReason`/`TestSelectionItem`/`RunResult`/`TaskRecord`/`RepositoryRecord`/`CommitRecord`/`ChangesetRecord` 数据类、`make_symbol_id` 及各 ID 函数、全部仓储 Protocol、`TaskStatus`/`ALLOWED_TRANSITIONS`/`MAX_RETRIES`/`assert_transition`/`InvalidTransitionError`。

## T2: 持久化层（SQLite ORM 与仓储实现）

- **Objective**: 实现 SQLite 表结构与幂等仓储，作为全系统唯一持久化所有者。
- **Requirements**: R1, R3, R5, R7, R8, R9
- **Write set**: `core/store/__init__.py`, `core/store/db.py`, `core/store/models.py`, `core/store/repository.py`
- **Provides**: C-STORE@1 (design.md §2.2)
- **Consumes**: C-SCHEMA@1
- **Blocked by**: none
- **Deliverable**: `core.store` 导出 `SqliteStore`（属性 `repositories/commits/changesets/symbols/edges/coverage/tests/tasks/selections/runs/reports`）、`init_schema(engine)`、`create_engine_and_session(db_url)`；ORM 行类与契约列名一致；唯一键 upsert、`replace_coverage`/`replace_selections` 全量替换、`save_report` 落盘并返回路径、`active_task_for_changeset` 查询非终态任务。

## T3: Git 仓库层

- **Objective**: 实现仓库校验、提交枚举、只读快照导出与双版本 diff。
- **Requirements**: R1, R4
- **Write set**: `core/repo/__init__.py`, `core/repo/git_repo.py`
- **Provides**: C-CORE@1（`core/repo` 部分）(design.md §2.2)
- **Consumes**: C-SCHEMA@1
- **Blocked by**: none
- **Deliverable**: `core.repo` 导出 `validate_repository`、`list_commits`、`export_snapshot`、`read_file_at`、`list_python_files`、`diff_commits`、`RepoInfo`、`NotAGitRepositoryError(code="not_a_git_repository")`；快照导出不修改原始仓库，diff 返回 `FileDiff` 且行区间为新文件侧。

## T4: 代码索引器

- **Objective**: 用 AST 与框架约定解析符号与带证据依赖边。
- **Requirements**: R3
- **Write set**: `core/indexer/__init__.py`, `core/indexer/indexer.py`, `core/indexer/framework.py`
- **Provides**: C-CORE@1（`index_repository`）(design.md §2.2)
- **Consumes**: C-SCHEMA@1
- **Blocked by**: none
- **Deliverable**: `core.indexer` 导出 `index_repository(snapshot_dir, repo_id, commit_sha) -> IndexResult` 与 `IndexResult`；覆盖模块/类/函数/方法/导入/继承/本仓库内可解析调用/FastAPI 路由（含 `include_router` 前缀）/SQLAlchemy 字段/pytest 测试节点；每条边带 `analysis_source`、`confidence`、`file_path`、`line_start`、`line_end`；不可判定项进入 `warnings`。

## T5: Diff 映射与影响传播

- **Objective**: 把行级变更映射为变更符号，并计算带路径证据的直接/间接影响。
- **Requirements**: R4, R5
- **Write set**: `core/impact/__init__.py`, `core/impact/mapper.py`, `core/impact/propagation.py`
- **Provides**: C-CORE@1（`map_changes`、`compute_impact`）(design.md §2.2)
- **Consumes**: C-SCHEMA@1
- **Blocked by**: none
- **Deliverable**: `core.impact` 导出 `map_changes` 与 `compute_impact`、`ImpactOutput`；映射支持新增/修改/删除/重命名与文件级兜底（`confidence=INFERRED`）；传播限制层级与数量、同符号保留最短路径、按契约权重排序并连续编号 `rank`，截断时 `truncated=True`。

## T6: 覆盖率映射与多源测试选择

- **Objective**: 解析覆盖率上下文并实现四源并集测试选择与理由输出。
- **Requirements**: R7, R9
- **Write set**: `core/coverage/__init__.py`, `core/coverage/mapper.py`, `core/coverage/selector.py`
- **Provides**: C-CORE@1（`parse_coverage_json`、`build_coverage_records`、`coverage_recall`、`select_tests`）(design.md §2.2)
- **Consumes**: C-SCHEMA@1
- **Blocked by**: none
- **Deliverable**: `core.coverage` 导出上述四个函数；选测输出 `TestSelectionItem` 且每条含 ≥1 条 `SelectionReason`（`covered_change|covered_impact|static_dependency|high_risk_guard`），四源并集去重合并理由，`strategy` 参数贯穿结果，覆盖率缺失时仍由静态与保底源产出结果。

## T7: 隔离执行器

- **Objective**: 提供本地进程与 Docker 两种执行实现，统一 `RunResult` 语义。
- **Requirements**: R2, R8
- **Write set**: `services/runner/__init__.py`, `services/runner/executor.py`, `services/runner/pytest_support.py`
- **Provides**: C-RUNNER@1 (design.md §2.2)
- **Consumes**: C-SCHEMA@1
- **Blocked by**: none
- **Deliverable**: `services.runner` 导出 `Executor` 协议、`LocalProcessExecutor`、`DockerExecutor`、`RunnerJob`、`RunnerUnavailableError`、`parse_pytest_summary`、`build_pytest_command`；本地执行器以 `shell=False` 运行、超时终止进程树、截断输出、写日志并在首行标注 `network_enforced=false`；Docker 执行器 daemon 不可达时抛 `RunnerUnavailableError(code="docker_unavailable")`。

## T8: 平台 API 与状态机端点

- **Objective**: 实现全部 REST 端点、Pydantic 出参、错误码与任务触发幂等。
- **Requirements**: R1, R6, R9, R12
- **Write set**: `services/api/__init__.py`, `services/api/main.py`, `services/api/schemas.py`, `services/api/deps.py`, `services/api/routes_repos.py`, `services/api/routes_changesets.py`, `services/api/routes_tasks.py`, `services/api/routes_report.py`, `services/api/settings.py`
- **Provides**: C-API@1 (design.md §2.2)
- **Consumes**: C-SCHEMA@1, C-STATE@1, C-STORE@1, C-ORCH@1
- **Blocked by**: none
- **Deliverable**: `services.api.main:app` 提供 `GET /api/health`、`POST/GET /api/repos`、`GET /api/repos/{id}/commits`、`POST /api/changesets`、`POST /api/changesets/{id}/analyze`、`GET /api/tasks/{id}`、`POST /api/tasks/{id}/retry`、`GET /api/changesets/{id}/impact|graph|selections|runs|report`；响应字段 snake_case、错误体 `{code,message,detail}`；`analyze` 复用非终态任务，`retry` 超限返回 409 `retry_exhausted`。

## T9: 分析编排

- **Objective**: 实现状态机驱动的阶段编排、产物落盘、失败处理与报告聚合。
- **Requirements**: R2, R5, R8, R9, R10
- **Write set**: `services/worker/__init__.py`, `services/worker/orchestrator.py`, `services/worker/reporting.py`, `core/generation/__init__.py`, `core/generation/generator.py`, `core/generation/model_adapter.py`, `core/validation/__init__.py`, `core/validation/validator.py`
- **Provides**: C-ORCH@1 (design.md §2.2)
- **Consumes**: C-SCHEMA@1, C-STATE@1, C-STORE@1, C-CORE@1, C-RUNNER@1
- **Blocked by**: none
- **Deliverable**: `services.worker.orchestrator.analyze_changeset(store, executor, changeset_id, options)` 按契约顺序推进全部阶段并把产物写入 `{artifacts_root}/{task_id}/{stage}/`；`AnalyzeOptions` 含默认 pytest 命令与关卡参数；失败转 `failed` 并记录 `resume_status`/错误码；`run_generation_stage` 通过 `ModelAdapter` 生成建议并交 `core.validation` 双版本判定，默认 `MockModelAdapter` 产出确定性建议，模型不可用时报告 `degraded_steps=["generation"]`；`reporting` 生成契约结构的报告载荷并写入 `reports`。

## T10: 前端应用

- **Objective**: 提供仓库/变更、影响图、报告与评测四个页面并完成 API 绑定。
- **Requirements**: R6, R9, R11
- **Write set**: `apps/web/package.json`, `apps/web/next.config.mjs`, `apps/web/tsconfig.json`, `apps/web/postcss.config.mjs`, `apps/web/tailwind.config.ts`, `apps/web/app/layout.tsx`, `apps/web/app/globals.css`, `apps/web/app/page.tsx`, `apps/web/app/changesets/[id]/page.tsx`, `apps/web/app/changesets/[id]/report/page.tsx`, `apps/web/app/eval/page.tsx`, `apps/web/lib/api.ts`, `apps/web/lib/types.ts`, `apps/web/components/ui.tsx`, `apps/web/components/ImpactGraph.tsx`, `apps/web/components/TaskProgress.tsx`, `apps/web/components/SelectionTable.tsx`
- **Provides**: C-WEB@1 (design.md §2.2)
- **Consumes**: C-API@1
- **Blocked by**: none
- **Deliverable**: Next.js 14 App Router + TS strict + Tailwind 前端；`lib/api.ts` 覆盖 C-API@1 全部端点（snake_case 不改写）；首页可导入仓库、选提交、创建 changeset 并触发分析；`/changesets/[id]` 展示任务状态轮询、Cytoscape 影响图（按 `analysis_source` 着色、模型节点带"建议"徽标、`truncated` 提示、节点代码位置）、选择理由表与执行结果（`isolation_mode`、`coverage_missing`）；报告页按静态/动态/模型/残余风险分区；`/eval` 读取 `var/eval/*.json` 展示策略对比。

## T11: 样例仓库、评测与脚本

- **Objective**: 生成受控样例仓库与标注样本，并实现统一口径评测脚本。
- **Requirements**: R10, R11
- **Write set**: `scripts/bootstrap_samples.py`, `scripts/run_eval.py`, `eval/samples/shop_api_rename_q1.json`, `eval/README.md`, `core/evaluation/__init__.py`, `core/evaluation/metrics.py`, `core/evaluation/strategies.py`
- **Provides**: C-SAMPLE@1, C-EVAL@1 (design.md §2.2)
- **Consumes**: C-SCHEMA@1, C-CORE@1, C-STORE@1
- **Blocked by**: none
- **Deliverable**: `scripts/bootstrap_samples.py` 幂等重建 `var/samples/shop_api`（两个提交、`app/core/` 高风险模块与 `tests/test_core_pricing.py`）并写 `eval/samples/shop_api_rename_q1.json` 与 `eval/samples/lock.json`；`core.evaluation.metrics` 导出 `recall_at_20`/`regression_recall`/`median_test_count_reduction`/`verified_rate`/`build_report`/`StrategyReport`；`core/evaluation/strategies.py` 实现 `full|neighbor|static|coverage|combo` 五策略；`scripts/run_eval.py` 输出 `var/eval/{strategy}.json`。

## T12: 部署、启动脚本与文档

- **Objective**: 提供一条命令初始化与启动的运行路径，并覆盖 Docker 可选路径。
- **Requirements**: R12
- **Write set**: `Makefile`, `infra/docker-compose.yml`, `infra/Dockerfile.api`, `infra/Dockerfile.runner`, `infra/README.md`, `docs/DEPLOY.md`
- **Provides**: none（消费 C-API@1 的启动约定）
- **Consumes**: C-API@1, C-RUNNER@1
- **Blocked by**: none
- **Deliverable**: `Makefile` 提供 `make init`（建 `var/` 与 `init_schema`）、`make api`（`uvicorn services.api.main:app`）、`make web`（`npm --prefix apps/web run dev`）、`make sample`、`make eval`；`infra/docker-compose.yml` 定义 `api` 与可选 `postgres`/`minio` 服务并声明默认不启用；`infra/Dockerfile.runner` 定义非 root、可断网运行的 runner 镜像；`docs/DEPLOY.md` 与 `infra/README.md` 说明本地优先、Docker 可选的启动与回退方式。
## T14: 生成测试双版本验证端到端

- **Objective**: 让"生成测试 → 原版本通过 → 变更版本失败 → 重复稳定"链路在样例回归场景产出 `verified`，并把证据写入报告。
- **Requirements**: R10, R9
- **Write set**: `core/generation/generator.py`, `core/generation/model_adapter.py`, `core/validation/validator.py`, `services/worker/orchestrator.py`, `services/worker/reporting.py`
- **Provides**: C-GEN@1 (design.md §2.5)
- **Consumes**: C-ORCH@1, C-CORE@1, C-RUNNER@1, C-SCHEMA@1
- **Blocked by**: none
- **Deliverable**: `core.generation` 与 `core.validation` 按 C-GEN@1 导出全部公开符号；`analyze_changeset(..., generate_tests=True)` 对 `eval/samples/shop_api_rename_q1.json` 场景产出至少一条 `status="verified"` 的生成测试，`ValidationOutcome.evidence` 含 base/target 两次重复的退出码与日志路径；`MockModelAdapter` 保持确定性，`OllamaModelAdapter` 不可用时抛 `ModelUnavailableError` 并由编排记 `degraded_steps=["generation"]`；报告 `model_suggestions[]` 含 `status`、`rationale`、`analysis_source="model"` 与 `validation_evidence`。

## T15: 多样本评测语料（≥40 组）

- **Objective**: 建立可复现的多类变更样本仓库与人工标注语料。
- **Requirements**: R11, R12
- **Write set**: `samples/shop_api_seed/**`, `scripts/bootstrap_samples.py`, `eval/samples/**`, `eval/README.md`
- **Provides**: C-SAMPLE-V2@1 (design.md §2.5)
- **Consumes**: C-SCHEMA@1
- **Blocked by**: none
- **Deliverable**: `samples/shop_api_seed/` 提供 ≥20 个源文件与 ≥15 个测试的 FastAPI 种子工程；`scripts/bootstrap_samples.py` 幂等重建 `var/samples/shop_api`（基线 tag `v1-baseline` + 每样本分支/tag），写出 `eval/samples/<sample_id>.json`（字段见契约）与 `eval/samples/lock.json`；样本 ≥40 组、八类 `change_kind` 每类 ≥5 组；基线版本全测试通过，非 `comment_only` 样本的 `expected_failing_tests` 在 target 上真实失败。

## T16: 批量评测与策略汇总报告

- **Objective**: 支持多策略 × 多样本的批量评测、样本级缓存与统一汇总输出。
- **Requirements**: R11
- **Write set**: `scripts/run_eval.py`, `core/evaluation/strategies.py`, `core/evaluation/metrics.py`, `docs/EVAL.md`
- **Provides**: C-EVAL-V2@1 (design.md §2.5)
- **Consumes**: C-EVAL@1, C-SAMPLE-V2@1, C-CORE@1
- **Blocked by**: none
- **Deliverable**: `scripts/run_eval.py` 支持 `--strategies`/`--samples`/`--out`/`--refresh`，每样本 artifacts 构建一次并缓存 `var/eval/cache/<sample_id>.json`，五策略复用；输出 `var/eval/<strategy>.json` 与 `var/eval/summary.json`（含 strategies 与 `regression_only`、`no_behavior_change` 子集指标）；单样本异常记 `error` 不中断；`docs/EVAL.md` 记录指标口径、缓存策略与复现命令。

## T17: 联调启动与端到端冒烟

- **Objective**: 用真实 uvicorn + Next.js 进程跑通一次完整分析并输出可复现的演示路径。
- **Requirements**: R12, R9
- **Write set**: `Makefile`, `scripts/dev_smoke.py`, `docs/DEMO.md`
- **Provides**: C-DEVUP@1 (design.md §2.5)
- **Consumes**: C-API@1, C-ORCH@1, C-SAMPLE@1
- **Blocked by**: none
- **Deliverable**: `scripts/dev_smoke.py` 以子进程启动 API（默认 8100）与 Web（默认 3100），用 `httpx` 依次走完 导入仓库→创建 changeset→触发分析→轮询任务→读取报告 并在 Web 根路径返回 200 后打印 `SMOKE PASS|FAIL`，退出码与步骤耗时齐全且结束时终止全部子进程；`Makefile` 新增 `smoke`、`eval-all`、`docker-runner`；`docs/DEMO.md` 提供 5～8 分钟演示脚本。

## T18: Docker 执行器启用与隔离验证

- **Objective**: 在可用 Docker 环境下真正以容器执行测试，并完成断网、限额与路径映射。
- **Requirements**: R8, R10
- **Write set**: `infra/Dockerfile.runner`, `infra/runner/**`, `services/runner/executor.py`
- **Provides**: C-RUNNER-DOCKER@1 (design.md §2.5)
- **Consumes**: C-RUNNER@1, C-SCHEMA@1
- **Blocked by**: none
- **Deliverable**: 可构建的 `changeproof-runner:latest`（非 root、含 pytest/coverage/pytest-cov、`/work` 只读挂载点与 `/artifacts` 可写）；`DockerExecutor` 以 `--network=none`、CPU/内存/PID 限额并在 `/work` 下执行，覆盖率 JSON 路径改写为容器内 `/artifacts/<name>` 并回读宿主；`RunResult.isolation_mode="docker"`、日志首行 `network_enforced=true`；超时通过停止容器返回 `TIMEOUT` 且不残留容器。
## T19: 选测精度与路由变更映射修复

- **Objective**: 收紧选测来源并修复路由参数类变更的符号映射，使 40 组语料的两项核心指标达标。
- **Requirements**: R4, R5, R7
- **Write set**: `core/indexer/indexer.py`, `core/indexer/framework.py`, `core/impact/mapper.py`, `core/coverage/selector.py`, `core/evaluation/strategies.py`
- **Provides**: C-SELECT@2 (design.md §2.5)
- **Consumes**: C-CORE@1, C-SCHEMA@1, C-EVAL-V2@1
- **Blocked by**: none
- **Deliverable**: `ROUTE` 符号行区间覆盖装饰器行与处理函数行，装饰器参数变化产出 `kind="route"` 的 `ChangedSymbol`；`select_tests` 按 C-SELECT@2 收紧 `static_dependency`（仅 stem 同名或同文件）与 `high_risk_guard`（仅高风险文件的关联测试），同一 source 每测试仅一条理由；`scripts/run_eval.py --strategies combo --refresh` 在 40 组语料上输出 `regression_recall=1.0`、`median_test_count_reduction≥0.5`、`recall_at_20≥0.9`。
## T20: 评测可信度——覆盖率逐测试兜底与生成验证接入

- **Objective**: 让 `coverage` 基线具备可比性，并把双版本验证结果真正计入 `verified_rate`。
- **Requirements**: R7, R10, R11
- **Write set**: `scripts/run_eval.py`, `core/evaluation/metrics.py`, `docs/EVAL.md`
- **Provides**: C-EVAL-V3@1 (design.md §2.5)
- **Consumes**: C-EVAL@1, C-EVAL-V2@1, C-CORE@1, C-GEN@1
- **Blocked by**: none
- **Deliverable**: `scripts/run_eval.py` 新增 `--coverage-per-test/--no-coverage-per-test`、`--with-generation/--no-generation`、`--coverage-cache`、`--coverage-cache-version`；基线无测试级上下文时按测试逐个采集覆盖行并以 `(base_sha, version)` 缓存到 `var/eval/coverage_cache/<base_sha>.json`；对 `expected_failing_tests` 非空样本执行生成与双版本验证，把 `generated[{test_node_id,status,rationale,analysis_source,validation_evidence}]` 写入样本 artifacts 与缓存；`summary.json` 输出 `coverage_context_source`、`generated_samples`、`verification_failures`；`docs/EVAL.md` 记录新参数、缓存结构与生成验证口径。

## T21: 前端端到端用例与演示门禁

- **Objective**: 用 Playwright 覆盖一次真实浏览器交互，并提供浏览器不可用时的 HTTP 等价门禁。
- **Requirements**: R6, R9, R12
- **Write set**: `apps/web/package.json`, `apps/web/playwright.config.ts`, `apps/web/e2e/**`, `apps/web/app/page.tsx`, `apps/web/app/changesets/[id]/page.tsx`, `apps/web/app/changesets/[id]/report/page.tsx`, `Makefile`, `scripts/e2e_http.py`, `docs/DEMO.md`
- **Provides**: C-WEB-E2E@1 (design.md §2.5)
- **Consumes**: C-WEB@1, C-API@1, C-DEVUP@1, C-SAMPLE-V2@1
- **Blocked by**: none
- **Deliverable**: 页面加入 `repo-path-input`/`repo-submit`/`commit-base`/`commit-target`/`changeset-create`/`analyze-button`/`task-status`/`impact-graph`/`report-summary`/`report-risks` 等 `data-testid`；`playwright.config.ts` 与 `e2e/analysis.spec.ts` 覆盖 导入仓库→建 changeset→触发分析→等待 reported→断言影响图与报告；`Makefile` 新增 `e2e`（8100 + 3100 + 独立 `var/e2e/`）；`scripts/e2e_http.py` 提供浏览器不可用时的等价断言；`docs/DEMO.md` 同步 `make e2e` 与浏览器安装说明。
## T22: 生成模板扩展（覆盖 5 类可判定变更）

- **Objective**: 把生成验证从"仅符号消失"扩展到配置常量、签名、路由参数与跨模块四类，使回归子集验证覆盖率大幅提升。
- **Requirements**: R10
- **Write set**: `core/generation/generator.py`, `core/generation/model_adapter.py`, `scripts/run_eval.py`, `services/worker/orchestrator.py`
- **Provides**: C-GEN@2 (design.md §2.5)
- **Consumes**: C-GEN@1, C-EVAL-V3@1, C-SCHEMA@1
- **Blocked by**: none
- **Deliverable**: `MockModelAdapter` 按 `existence`/`constant_value`/`signature_contract`/`route_contract`/`import_contract` 五类模板产出确定性、可判定的 pytest 正文；`static_check` 白名单扩到 `pytest/inspect/json/fastapi.testclient` 并仍拒绝文件、网络、子进程与 `exec/eval`；编排与评测都把"未被测试覆盖的变更符号全集"传入生成；40 组语料评测输出 `verification_failures ≤ 10` 且 combo 三项指标不回退。

## T23: 覆盖来源收敛（降幅回到 ≥0.55）

- **Objective**: 通过限制 `covered_impact` 的适用距离，降低覆盖率源生效后的并集膨胀。
- **Requirements**: R7
- **Write set**: `core/coverage/selector.py`
- **Provides**: C-SELECT@3 (design.md §2.5)
- **Consumes**: C-SELECT@2, C-CORE@1
- **Blocked by**: none
- **Deliverable**: `select_tests` 中 `covered_impact` 仅对 `distance == 1` 的受影响符号成立，`distance > 1` 的覆盖证据不进入理由；40 组语料评测输出 `combo.regression_recall = 1.0`、`combo.median_test_count_reduction ≥ 0.55`、`coverage.regression_recall ≥ 0.8`。

## T24: 一键门禁

- **Objective**: 把编译、评测、联调冒烟、浏览器 E2E 与容器隔离验证合成一条可重复的执行入口。
- **Requirements**: R12
- **Write set**: `scripts/gate.py`, `Makefile`, `docs/DEMO.md`
- **Provides**: C-GATE@1 (design.md §2.5)
- **Consumes**: C-EVAL-V3@1, C-DEVUP@1, C-WEB-E2E@1, C-RUNNER-DOCKER@1
- **Blocked by**: none
- **Deliverable**: `scripts/gate.py` 顺序执行 compile/eval/smoke/e2e/docker-runner 五步（`--skip-docker`/`--skip-e2e`/`--refresh` 可用），输出每步 `PASS|FAIL|SKIP` 与耗时并汇总到 `var/gate/summary.json`，任一 FAIL 退出码 1；`Makefile` 新增等价的 `gate` 目标；`docs/DEMO.md` 增加门禁一节。
## T27: 基于依赖边的静态选测（提升 static 召回）

- **Objective**: 让静态源基于真实依赖边而非文件名近似，把 `static` 单源回归召回从 0.373 提升到 ≥0.8。
- **Requirements**: R5, R7
- **Write set**: `core/coverage/selector.py`, `core/indexer/indexer.py`, `services/worker/orchestrator.py`, `scripts/run_eval.py`
- **Provides**: C-SELECT@6 (design.md §2.5)
- **Consumes**: C-CORE@1, C-SELECT@5, C-EVAL-V3@1
- **Blocked by**: none
- **Deliverable**: `select_tests` 新增可选 `edges` 参数，按"测试文件→受影响文件 ≤2 跳依赖路径"产生 `static_dependency` 理由，并在无 edges 时回退精确同名规则；`indexer` 保证测试文件到被测产品模块存在模块级 `IMPORTS` 边、`TestClient` 路由保留 `DEPENDS_ON` 边；编排与评测把当前 commit 的 edges 传入；40 组语料输出 `static.regression_recall ≥ 0.8`，`combo` 召回 1.0、降幅 ≥0.50、Recall@20 ≥0.9。

## T28: 边界类变更的生成模板

- **Objective**: 为 env 键名、实例状态与守卫边界三类变更补齐可判定生成模板，把未验证样本从 8 组降到 ≤3 组。
- **Requirements**: R10
- **Write set**: `core/generation/generator.py`, `core/generation/model_adapter.py`, `core/generation/__init__.py`
- **Provides**: C-GEN@3 (design.md §2.5)
- **Consumes**: C-GEN@2, C-EVAL-V3@1
- **Blocked by**: none
- **Deliverable**: `MockModelAdapter` 新增 `env_contract`、`state_contract`、`guard_contract` 三类确定性模板（断言取 base 事实，无法确定时不生成）；`core.generation` 导出保持不变或向后兼容；评测输出 `verification_failures ≤ 3` 且 combo 三项指标不回退。
## T29: 语义等价映射根治注释假阳性

- **Objective**: 让纯注释/空白/格式变更不再产生符号级变更，并建立该不变式的自动化防护。
- **Requirements**: R4, R5
- **Write set**: `core/impact/mapper.py`, `core/evaluation/metrics.py`, `scripts/run_eval.py`, `tests/test_impact_semantics.py`
- **Provides**: C-IMPACT@1 (design.md §2.5)
- **Consumes**: C-CORE@1, C-EVAL-V3@1
- **Blocked by**: none
- **Deliverable**: `map_changes` 按 AST 等价性过滤注释/空白/格式变更（docstring 先归一化，AST 不可解析时保守视为变更并记 `ast_unavailable`）；文件内全部等价时不产生文件级兜底；`metrics.no_behavior_change_summary` 输出 `{samples, affected_symbols}` 并写入 `summary.json` 的 `subsets.no_behavior_change`；新增 `tests/test_impact_semantics.py`，对 5 个 comment_only 样本断言语义等价判定为零符号变更，并覆盖"真变更仍被识别"的正例。

## T30: 覆盖来源 kind 口径统一

- **Objective**: 让 `covered_change` 与 `covered_impact` 共用同一 kind 白名单，消除 FILE/FIELD/CLASS 经覆盖源入选。
- **Requirements**: R7
- **Write set**: `core/coverage/selector.py`, `core/evaluation/strategies.py`, `tests/test_selector_kinds.py`
- **Provides**: C-SELECT@7 (design.md §2.5)
- **Consumes**: C-SELECT@6, C-SELECT@5
- **Blocked by**: none
- **Deliverable**: `selector` 用单一常量同时约束两条覆盖来源的 kind；FILE/FIELD/CLASS 只写 `coverage_evidence` 不入选；`config_tax_rate_default` 选中数 ≤25；新增 `tests/test_selector_kinds.py` 覆盖"FILE/FIELD/CLASS 不入选、可执行符号入选、保底兜底非空"三条不变式。

## T31: 执行计数单一来源

- **Objective**: 把 pytest 计数收敛到执行器的 junitxml 一条路径，消除四处分散解析。
- **Requirements**: R8
- **Write set**: `services/runner/executor.py`, `services/worker/orchestrator.py`, `infra/runner/verify_isolation.py`, `tests/test_runner_counts.py`
- **Provides**: C-RUNNER@2 (design.md §2.5)
- **Consumes**: C-RUNNER@1, C-ORCH@2
- **Blocked by**: none
- **Deliverable**: 执行器自动追加 `--junitxml` 据其填 `RunResult` 计数，junit 缺失时计数全 0 且日志记 `counts_missing=true`；删除 orchestrator 的 `_junit_counts`/`_with_counts`/`_with_junit` 与日志回退、删除 executor 的覆盖率回退；`verify_isolation.py` 只读 `RunResult`；新增 `tests/test_runner_counts.py` 覆盖正常/缺失/超时三态。

## T32: 门禁纳入指标断言

- **Objective**: 把关键指标写成门禁硬断言，让同类回归在交付前被拦住。
- **Requirements**: R12
- **Write set**: `scripts/gate.py`
- **Provides**: C-GATE@2 (design.md §2.5)
- **Consumes**: C-EVAL-V3@1, C-GATE@1
- **Blocked by**: none
- **Deliverable**: `gate.py` 的 `eval` 步读 `var/eval/summary.json` 并断言 `combo.regression_recall==1.0`、`combo.median_test_count_reduction≥0.50`、`combo.recall_at_20≥0.90`、`verification_failures≤3`、`static.regression_recall≥0.80`、`subsets.no_behavior_change.affected_symbols==0`；失败时把期望/实际/样本清单写入 `var/gate/summary.json` 该步 `detail` 且退出码 1。
## T33: 第二示例仓库与泛化评测

- **Objective**: 增加一个结构明显不同的 FastAPI 样例仓库，验证分析链路的泛化能力。
- **Requirements**: R11, R12
- **Write set**: `samples/blog_api_seed/**`, `scripts/bootstrap_blog.py`, `scripts/run_blog_eval.py`, `eval/blog/**`
- **Provides**: C-SECOND-REPO@1 (design.md §2.6)
- **Consumes**: C-CORE@1, C-EVAL@1
- **Blocked by**: none
- **Deliverable**: `samples/blog_api_seed/` 提供包式布局（`src/blog/`）、SQLAlchemy 关系与 FastAPI `Depends` 的 ≥8 源文件、≥8 测试的种子工程；`scripts/bootstrap_blog.py` 幂等生成 `var/samples/blog_api` 与 ≥5 组变更样本及 `eval/blog/lock.json`；`scripts/run_blog_eval.py` 复用分析链路输出 `var/eval_blog/summary.json`；五组样本 base 全通过、target 失败集与标注一致，`combo.regression_recall=1.0`。

## T34: 最小权限控制（默认关闭）

- **Objective**: 为 API 增加可选的 token 与只读/操作角色，默认不改变现有行为。
- **Requirements**: R12
- **Write set**: `services/api/auth.py`, `services/api/deps.py`, `services/api/main.py`, `tests/test_auth.py`
- **Provides**: C-AUTH@1 (design.md §2.6)
- **Consumes**: C-API@1
- **Blocked by**: none
- **Deliverable**: `CHANGEPROOF_AUTH=token` 开启后，除 `/api/health` 外的 `/api/*` 必须带 `Authorization: Bearer`，token 与角色映射来自 `CHANGEPROOF_API_TOKENS`；缺失/未知 token 返回 401 `unauthorized`，`viewer` 仅 GET、`operator` 全部；默认关闭；`tests/test_auth.py` 覆盖关闭放行 / 401 / viewer 只读 / operator 可写四态。

## T35: 故障注入与降级

- **Objective**: 用五类受控故障证明系统的降级与错误呈现行为。
- **Requirements**: R2, R8, R10, R12
- **Write set**: `scripts/fault_injection.py`, `tests/test_fault_injection.py`
- **Provides**: C-FAULT@1 (design.md §2.6)
- **Consumes**: C-RUNNER@2, C-ORCH@2, C-GEN@1
- **Blocked by**: none
- **Deliverable**: `tests/test_fault_injection.py` 覆盖超时（TIMEOUT）、依赖缺失（任务 failed 且保留错误码与日志路径）、Runner 失联（`docker_unavailable` 且降级 local）、覆盖率缺失（`coverage_missing=true` 且选测非空）、模型失败（`degraded_steps=["generation"]` 且任务 reported）五类；`scripts/fault_injection.py` 汇总输出 5/5 PASS。

## T36: 验收文档与 P0 对照

- **Objective**: 形成可核对的 P0 逐项验收文档与一致的启动/复现说明。
- **Requirements**: R12
- **Write set**: `docs/ACCEPTANCE.md`, `README.md`
- **Provides**: C-ACCEPT@1 (design.md §2.6)
- **Consumes**: 全部已完成任务的实测结果
- **Blocked by**: none
- **Deliverable**: `docs/ACCEPTANCE.md` 提供 P0 十四项逐项对照（实现位置 + 复现命令 + 实测值）、P1/P2 未完成项、启动与门禁命令、已知边界；`README.md` 更新为与当前实现一致的启动与验收入口。