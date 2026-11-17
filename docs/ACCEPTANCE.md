# ChangeProof 验收文档（P0 对照）

> 验收基线：40 组人工标注样本（8 类变更 × 5 组）× 五种选择策略，`cache_hits=0/40`（全量重算）。
> 本文件中的每条命令均在当前实现上真实执行过，未虚构结果。

## 一、核心指标（`var/eval/summary.json`）

| 策略 | Recall@20 | 回归召回 | 用例数中位降幅 | 已验证率 |
|---|---|---|---|---|
| full | 1.000 | 1.000 | 0.000 | 0.667 |
| neighbor | 1.000 | 1.000 | 0.000 | 0.667 |
| static | 1.000 | 1.000 | 0.538 | 0.667 |
| coverage | 1.000 | 0.967 | 0.596 | 0.667 |
| **combo（本项目）** | **1.000** | **1.000** | **0.538** | **0.667** |

- `verification_failures = 1`（目标 ≤3）
- `no_behavior_change.affected_symbols = 0`（注释/空白变更不产生符号级变更）
- 一键门禁 `python scripts/gate.py` 退出码 0，约 59s（compile/eval/smoke/e2e PASS，docker-runner 在 daemon 不可用时 SKIP）

## 二、P0 逐项对照

| # | 需求 | 实现位置 | 复现命令 | 实测结果 |
|---|---|---|---|---|
| 1 | 仓库导入与版本选择 | `core/repo/git_repo.py`、`services/api/routes_repos.py` | `POST /api/repos` → `GET /api/repos/{id}/commits` | 本地 Git 仓库导入、跨全部 ref 的提交枚举（41 条）、base/target 双版本；重复导入返回 200 同 id |
| 2 | 代码索引 | `core/indexer/` | `python scripts/run_eval.py --refresh` | AST/FastAPI/SQLAlchemy/pytest 符号与带证据边；40 组样本索引无异常 |
| 3 | 变更分析（Diff→符号） | `core/impact/mapper.py` | `pytest tests/test_impact_semantics.py -q` | 行级 diff→符号；AST 语义等价过滤注释/空白（5 组注释样本 changed=0） |
| 4 | 影响分析 | `core/impact/propagation.py` | `GET /api/changesets/{id}/impact` | 直接/间接影响、路径证据、层级与数量限制；`recall_at_20=1.0` |
| 5 | 影响图 | `apps/web/components/ImpactGraph.tsx` | Playwright `apps/web/e2e/analysis.spec.ts` | Cytoscape 图，按 `analysis_source` 着色、模型节点带"建议"徽标、节点代码位置、`truncated` 提示；`1 passed` |
| 6 | 覆盖率映射 | `core/coverage/mapper.py` | `python scripts/run_eval.py --refresh` | `coverage_context_source=per_test`；测试→文件→行映射；`--cov-context=test` 解析与 `\|run` 后缀归一化 |
| 7 | 测试选择 | `core/coverage/selector.py` | `pytest tests/test_selector_kinds.py -q` | 四源并集（覆盖变更行/覆盖影响符号/静态依赖/高风险保底）+ 每条理由；`combo.regression_recall=1.0`、降幅 0.538 |
| 8 | 隔离执行 | `services/runner/executor.py`、`infra/Dockerfile.runner` | `python infra/runner/verify_isolation.py` | 本地进程执行器（默认，`isolation_mode=local`）与 Docker（可选）双实现；断网、非 root、CPU/内存/PID/时长限额、日志与覆盖率回读；超时无残留容器 |
| 9 | 证据报告 | `services/worker/reporting.py`、`apps/web/app/changesets/[id]/report` | `GET /api/changesets/{id}/report` | 静态事实/动态事实/模型建议/残余风险四段分离，`analysis_source` 逐条标注，`regression_detected` 由执行结果决定 |
| 10 | 受限测试生成 | `core/generation/` | `python scripts/run_eval.py --refresh` | 五类模板 + env/状态/守卫三类边界模板；AST 静态白名单（禁文件/网络/子进程/exec）；默认 `pending_review` |
| 11 | 生成测试验证 | `core/validation/validator.py` | `var/eval/summary.json` | 原版本通过 ∧ 变更版本失败 ∧ 重复稳定 → `verified`；`generated_samples=34`、`verification_failures=1` |
| 12 | 自动评测 | `core/evaluation/`、`scripts/run_eval.py` | `python scripts/run_eval.py --strategies full,neighbor,static,coverage,combo --refresh` | 40 组样本 × 五策略 + 子集（回归/无行为变更）+ 缓存 + `summary.json`；单轮约 4 分钟，缓存复跑 <1s |
| 13 | 最小权限控制 | `services/api/auth.py` | `pytest tests/test_auth.py -q` | 默认关闭；`CHANGEPROOF_AUTH=token` 开启后 401/403/viewer 只读/operator 可写四态通过 |
| 14 | 部署、安全与文档 | `Makefile`、`infra/`、`docs/DEPLOY.md`、`docs/DEMO.md` | `python scripts/gate.py`、`python scripts/fault_injection.py` | 一键门禁五步；故障注入 5/5（超时/依赖缺失/Runner 失联/覆盖率缺失/模型失败）；`tests/` 21 个不变式用例通过 |

## 三、第二示例仓库（泛化验证）

`samples/blog_api_seed/`（包式布局 `src/blog/`、SQLAlchemy `relationship`、FastAPI `Depends`），5 组样本实测：
`PYTHONPATH=. python scripts/run_blog_eval.py` → `combo.recall_at_20=1.000`、`regression_recall=1.000`、降幅 **0.556**；5 组样本 base 全通过、target 失败集与标注一致（脚本含"实际失败集 == 标注"硬校验，见 `[ok]` 行）。

## 四、复现命令

```bash
# 一键门禁（编译 → 评测 → 联调冒烟 → 浏览器 E2E → 容器隔离）
python scripts/gate.py                 # 可附 --skip-docker / --skip-e2e / --refresh

# 全量评测（40 组 × 五策略，含逐测试覆盖与生成验证）
PYTHONPATH=. python scripts/run_eval.py --refresh

# 联调冒烟（真实 uvicorn + Next.js，非 TestClient）
PYTHONPATH=. python scripts/dev_smoke.py --api-port 8111 --web-port 3111

# 故障注入（5 类受控故障）
PYTHONPATH=. python scripts/fault_injection.py

# 不变式单测（21 个）
PYTHONPATH=. python -m pytest tests -q

# 启动开发服务（本机 8000/3000 常被占用，建议用下列端口）
make init
CHANGEPROOF_DB_URL=sqlite:///./var/demo2/changeproof.db uvicorn services.api.main:app --port 8102
NEXT_PUBLIC_API_BASE=http://127.0.0.1:8102 npm --prefix apps/web run dev -- -p 3101
```

## 五、已知边界（不粉饰）

1. `coverage` 单源召回 0.967：C-SELECT@7 把 FILE/FIELD/CLASS 排除出覆盖入选源后的预期结果，纯字段/配置类变更不再靠覆盖率入选。
2. `verified_rate=0.667` 的分母是"有生成条目的回归样本"，不是全部 40 组；剩余 1 组 `shared_util_coupon_rate_cap` 需要构造带有效 `Coupon` 的 Service 才能判别，按"不得伪造"保持 `pending_review`。
3. 影响分析对装饰器参数、动态导入、反射等动态行为是边界；未被静态边覆盖的关系不会出现在影响图中。
4. 纯注释/文档变更现在正确地不产生符号级变更，因此其 `expected_impact_files` 标注已同步改为空（生成器与 JSON 一致）。
5. Docker runner 在 daemon 不可用时自动降级为本地执行并如实标注，不作为已验证的隔离能力展示。

## 六、P1/P2 状态

- **P1 已完成**：故障注入（5 类）、五策略完整基准、第二示例仓库泛化、真实开源仓库适配（`full-stack-fastapi-template`：索引 234 符号 / 220 边 / 58 测试，删除型变更选测非空）、FastAPI 依赖注入图（`DEPENDS_ON` 边，真实仓库边数 220→351）、CI 门禁（`.github/workflows/gate.yml`）、**testmon 参考基线对照**（testmon 召回 0.881 / 降幅 0.904 vs combo 1.000 / 0.538）。
- **P1 未完成**：LangGraph 编排（当前为确定性状态机 + 同步执行，按此前拍板不做）、Celery/Redis 队列、PostgreSQL/MinIO 实际接入、按仓库风格定制生成模板的显式流程。
- **P2 已完成**：TypeScript 索引级支持（`scripts/ts_index.cjs` + `core/indexer/typescript.py`，真实仓库 351 个 TS 符号 / 242 条边；**不执行 TS 测试**）；**全链路异常注入 7 类**（超时、依赖缺失、Runner 失联、覆盖率缺失、模型失败、快照缺失、git 命令失败），`scripts/fault_injection.py` 实测 7/7 PASS、`tests/test_fault_injection.py` 7 passed。
- **P2 未完成**：Go 支持、跨仓库分析、IDE 插件、自定义影响规则 DSL；并发幂等由 API 层"复用非终态任务"保证，未在故障注入中重复模拟。
- **已知边界（已闭合）**：模块级变量引用（如 `engine`）已通过 `USES_GLOBAL` 边接通，真实仓库影响面 **5 → 12**、选测 **58 → 48**；`api/deps.py` 的 `except A, B` 语法不兼容也已由降级解析兜住（保留导入边）。剩余边界：跨文件全局引用仍依赖 `IMPORTS` 边，动态反射无法建边；Go 未支持；TS 只索引不执行。