# ChangeProof 演示脚本（5～8 分钟）

目标：在 8 分钟内向观众证明「一次提交变更 → 影响面 → 测试选择 → 真实回归发现 → 可读报告」的闭环，
并展示同一套能力在批量评测上的稳定性。全程本地运行，后端 `8100`、前端 `3100`（避开本机已占用的 `8000/3000`）。

## 0. 演示前一次性准备（约 1 分钟，不计入演示时长）

```bash
pip install -e .
npm --prefix apps/web install
make init          # 建 var/ 目录并初始化 SQLite
make sample        # 重建 var/samples/shop_api 并写入 eval/samples/lock.json
```

确认 `eval/samples/lock.json` 存在（样例 sha 每次重建都会变化，演示中一律从该文件读取，禁止写死）。
若已执行过 `make smoke` 且通过，可认为环境就绪，直接进入演示。

## 1. 一键证明链路可用（0:00–1:30）

```bash
make smoke
```

`make smoke` 等价于：

```bash
python scripts/dev_smoke.py --api-port 8100 --web-port 3100 --timeout 300
```

它用真实子进程拉起 `uvicorn services.api.main:app` 与 `npm --prefix apps/web run dev`，
用 `httpx` 走完：

1. `POST /api/repos`（`var/samples/shop_api`）
2. `POST /api/changesets`（sha 取自 `eval/samples/lock.json` 第一组）
3. `POST /api/changesets/{id}/analyze`
4. 轮询 `GET /api/tasks/{id}` 至 `reported`
5. `GET /api/changesets/{id}/report`
6. 前端根路径 `http://127.0.0.1:3100/` 返回 200

预期输出（每一步都有耗时）：

```
[1/10] load_sample_refs     ok    1 ms
[2/10] reset_state          ok    2 ms
...
SMOKE PASS total=48210ms repo_id=... changeset_id=... task_id=...
```

讲点：失败与成功的判定都来自真实 HTTP 响应，不使用 `TestClient`；结束时脚本会终止全部子进程，
失败时 `var/smoke/last_task.json` 保留最后一次任务 id 与各步骤耗时，`var/smoke/api.log` / `web.log` 保留进程日志。

## 1.1 浏览器端到端门禁（make e2e）

```bash
npm --prefix apps/web i -D @playwright/test
PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright npx playwright install chromium
make e2e
```

`make e2e` 用独立数据库 `var/e2e/changeproof.db` 与 `var/e2e/artifacts/` 拉起 `uvicorn`（`8100`），
再在 `apps/web` 执行 `npx playwright test`（Next 由 `playwright.config.ts` 的 `webServer` 在 `3100` 启动），
结束后清理全部子进程；日志在 `var/e2e/api.log`、`var/e2e/playwright.log`、`var/e2e/web.log`。

Playwright 覆盖一次真实浏览器交互（`apps/web/e2e/analysis.spec.ts`）：

1. 打开 `/` → 填写仓库路径 `var/samples/shop_api`（名称留空自动取路径末段）并提交
2. 按 `eval/samples/lock.json` 的 sha 选择 base/target 提交
3. 点击 `创建 changeset`，进入 `/changesets/{id}`
4. 点击 `触发分析`，等待 `task-status` 出现 `reported`（上限 120s）
5. 断言 `impact-graph` 可见且节点数 > 0
6. 打开 `/changesets/{id}/report`，断言 `report-summary` 与 `report-risks` 非空

预期输出（浏览器可用时）：

```
1 passed (…s)
E2E PASS: playwright 1 passed
```

未安装 chromium 时用例自动 `test.skip()`，`make e2e` 回退执行 `scripts/e2e_http.py`：
它用 `httpx` 走同一链路并断言 `/`、`/changesets/{id}`、`/changesets/{id}/report` 的关键文案，输出

```
HTTP E2E PASS total=…ms
```

讲点：门禁不写 `eval/samples/*.json`、不写运行库以外的文件；重复运行幂等（仓库与 changeset 复用既有记录）。

## 1.2 一键门禁（make gate）

```bash
make gate
# 等价于：python scripts/gate.py
```

`scripts/gate.py` 按固定顺序串联前四步与容器隔离验证，每步只调用既有脚本，不重写其逻辑：

| 步骤 | 执行的命令 | 判据 |
| --- | --- | --- |
| `compile` | `python -m compileall -q core services scripts` | 返回码 0 |
| `eval` | `python scripts/run_eval.py`（默认五策略，样本缓存命中） | 返回码 0 |
| `smoke` | `python scripts/dev_smoke.py --api-port 8100 --web-port 3100` | 返回码 0 |
| `e2e` | `apps/web` 下 `npx playwright test`，浏览器不可用时回退 `scripts/e2e_http.py` | `1 passed` 或 `HTTP E2E PASS` |
| `docker-runner` | `python infra/runner/verify_isolation.py` | `docker info` 不可用时记 `SKIP` |

可选参数：

```bash
python scripts/gate.py --skip-docker    # 本机无 docker daemon 时跳过容器步骤
python scripts/gate.py --skip-e2e       # 跳过浏览器 E2E（仍执行前四步中的前三步 + 容器步骤）
python scripts/gate.py --refresh        # 透传 run_eval.py，忽略样本级缓存
```

每步输出一行 `PASS|FAIL|SKIP` 与耗时（各步完整日志在 `var/gate/*.log`），结束后打印总分与
`var/gate/summary.json` 路径，汇总内含 `duration_ms`、`exit_code` 与每步的 `command`/`log_path`。
任一 `FAIL` 退出码为 1；样本缓存命中时总时长目标 ≤8 分钟（具体耗时以本机实测为准，不写死数值）。

讲点：门禁默认不重建样本、不删主库；`smoke` 用独立 `var/smoke/`、`e2e` 用独立 `var/e2e/` 库与
artifacts，`docker-runner` 用 `var/runner_smoke/`，因此重复执行互不污染。

## 2. 手动启动两个服务（1:30–2:30）

另开两个终端（Windows 用 `set` 代替 `export`）：

```bash
# 终端 A：后端
make api API_PORT=8100

# 终端 B：前端
export NEXT_PUBLIC_API_BASE=http://127.0.0.1:8100
npm --prefix apps/web run dev -- -p 3100
```

浏览器打开 `http://127.0.0.1:3100/`。健康检查可现场演示：

```bash
curl http://127.0.0.1:8100/api/health
# {"status":"ok","storage":"sqlite","isolation_mode":"local","llm":"mock"}
```

## 3. 走一遍分析流程（2:30–4:00）

在首页依次操作，并强调「同一 changeset 重复触发返回既有非终态任务」的幂等语义：

1. **导入仓库**：路径填 `var/samples/shop_api`，名称留空则自动取路径末段 → `POST /api/repos`
2. **填写提交**：基线 / 变更提交各填一个完整 sha（取自 `eval/samples/lock.json` 的 `base_sha` / `target_sha`；
   输入框的候选列表只覆盖当前分支历史，样本提交位于各 `sample/<id>` 分支）
3. **创建 changeset** → 跳转 `/changesets/{id}` → `POST /api/changesets`
4. **触发分析**（不勾选生成测试）→ `POST /api/changesets/{id}/analyze`，任务按
   `baseline_preparing → indexing → analyzing → test_selecting → test_running → reported` 推进，
   页面每 2000ms 轮询 `task-status` 直至 `reported`

讲点：状态机是确定性的，阶段产物落在 `var/artifacts/{task_id}/{stage}/`（`baseline.log`、`coverage.json`、
`impact.json`、`selections.json`、`run_target.log`、`report.json`），但 API 一律以库表为权威数据源。

## 4. 影响面、图谱与测试选择（4:00–5:30）

变更集详情页可切换到三个视图：

- **影响面**：`Order.total` 重命名 → `app/models.py`、`app/services/order.py`、`app/api/orders.py`；
  每条影响带 `distance`、`rank`、`confidence`、`analysis_source`、`is_direct` 与路径证据 `path`
- **依赖图谱**（cytoscape）：节点为该 changeset 的 changed ∪ impacted 符号，边只保留两端都在节点集合内的
- **选测结果**：每条测试都带 `reasons`（`covered_change | covered_impact | static_dependency | high_risk_guard`），
  解释「为什么选它」

讲点：回归是故意注入的 `AttributeError`（`Order.total` 已不存在），因此 `test_order_total` 在 target 上真实失败；
`app/core/pricing.py` 属于 `high_risk_globs`，`tests/test_core_pricing.py` 由保底源入选。

## 5. 报告页（5:30–6:30）

打开 `/changesets/{id}/report`，按四段讲：

- `summary`：`changed_symbols`、`impacted_symbols`、`selected_tests`、`regression_detected`
- `static_facts`：变更符号与影响传播（含 `truncated`）
- `dynamic_facts`：base/target 两次运行的结果、`coverage_missing` 标记、`isolation_mode` 与日志路径
- `model_suggestions` / `residual_risks` / `degraded_steps`：模型建议、残留风险与降级步骤（覆盖率缺失时必须显式标注）

## 6. 批量评测（6:30–7:30）

```bash
make eval-all
# 等价于：python scripts/run_eval.py --strategies full,neighbor,static,coverage,combo \
#          --samples eval/samples --out var/eval
```

浏览器打开 `http://127.0.0.1:3100/eval`，展示五策略在 `recall_at_20`、`regression_recall`、
`median_test_count_reduction`、`verified_rate` 上的对比，以及 `regression_only` 子集。
讲点：指标口径固定（空预期返回 1.0），单样本失败只记 `error` 不中断整轮；每样本 artifacts 只构建一次，五策略复用缓存。

## 7. 收尾与故障处理（7:30–8:00）

- 端口被占用：`make smoke` 会因端口冲突失败，交换端口即可
  （`python scripts/dev_smoke.py --api-port 8101 --web-port 3101`），脚本内部会用
  `NEXT_PUBLIC_API_BASE=http://127.0.0.1:<api-port>` 指向同一个后端
- Docker 执行器：`make docker-runner` 构建 `changeproof-runner:latest`，再以
  `CHANGEPROOF_ISOLATION_MODE=docker` 重启 API 即切换为容器执行（健康检查 `isolation_mode` 变为 `docker`）；
  daemon 不可用时自动降级本地执行器并在报告中写入降级步骤
- 失败排查顺序：`var/smoke/last_task.json`（最后一步与任务 id）→ `var/smoke/api.log` →
  `var/artifacts/{task_id}/{stage}/*.log`

演示结论：一条命令复现端到端链路，报告中的每条影响与每个测试选择都能追溯到具体证据。