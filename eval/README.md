# 自动评测语料（`eval/`）

本目录存放**人工标注**的变更样本。`scripts/bootstrap_samples.py` 从 `samples/shop_api_seed/`
幂等重建受控样例仓库 `var/samples/shop_api`，并为每个样本写一份标注 JSON；
评测脚本（`scripts/run_eval.py`）按统一口径对测试选择策略计算指标。

人工真值只存在于本目录的 JSON 中，**禁止**用运行期预测结果回填；评测只读
`eval/samples/` 与 `var/samples/`，不读运行库。

## 1. 语料构成

- `samples/shop_api_seed/`：种子工程，FastAPI + SQLAlchemy 小型电商服务。
  - 34 个 `*.py`（20 个 `app/` 源文件 + 14 个 `tests/` 文件），46 个 pytest 用例。
  - `app/core/`：高风险纯函数（`money`、`pricing`、`validation`）。
  - `app/models.py`：`Customer` / `Product` / `Order` / `OrderItem` / `Coupon`。
  - `app/services/`：`catalog`、`coupons`、`customers`、`inventory`、`order`。
  - `app/api/`：`health`、`products`、`orders`、`customers`。
  - 基线版本 `python -m pytest -q tests` 全部通过；所有用例不依赖数据库与网络。
- `eval/samples/<sample_id>.json`：样本标注；`eval/samples/lock.json`：样本 sha 锁定表。

样本总数 **40**，`change_kind` 覆盖枚举全部 8 个取值，每类 **5** 组：

| `change_kind` | 组数 | 变更语义 |
|---|---|---|
| `rename_field` | 5 | 模型字段或响应字段改名（含 `Order.total` → `total_amount` 的标杆样本 `shop_api_rename_q1`） |
| `rename_function` | 5 | 服务方法改名，调用点未同步 |
| `signature_change` | 5 | 函数/方法签名增加必填或仅关键字参数 |
| `route_param` | 5 | 路由路径或路径参数形态改变 |
| `shared_util` | 5 | `app/core/` 共享工具行为变化（精度、上限、常量表） |
| `config` | 5 | 配置默认值或环境变量名变化 |
| `comment_only` | 5 | 仅注释/文档字符串，无行为变化 |
| `cross_module` | 5 | 跨模块协调变更（≥2 个模块同时修改） |

## 2. 生成语料

```bash
python scripts/bootstrap_samples.py
```

幂等重建流程：

1. 删除并重建 `var/samples/shop_api`，把 `samples/shop_api_seed/` 原样拷入并 `git init`。
2. 基线提交并打 tag `v1-baseline`。
3. 每个样本从基线派生分支 `sample/<sample_id>`，应用脚本中显式声明的变更加提交，
   并打同名 tag；样本分支只包含该样本的一次变更，`tests/` 不被修改。
4. 回到 `main`，写出 `eval/samples/<sample_id>.json` 与 `eval/samples/lock.json`。

样本仓库默认测试命令：`python -m pytest -q tests`。

> **提交 sha 每次重建都会变化**，代码与契约中不得写死 sha，一律从 `eval/samples/lock.json` 读取。
> 缺少 sha 时评测跳过该样本并记 `skipped_missing_sha`。

## 3. 样本字段（全部必填）

| 字段 | 说明 |
|---|---|
| `sample_id` | `^[a-z0-9_]+$`，与 `target_ref` 后缀一致 |
| `repo_name` | 固定 `shop_api` |
| `repo_path_marker` | 固定 `var/samples/shop_api`（兼容 C-SAMPLE@1 消费者） |
| `base_ref` | 固定 `v1-baseline` |
| `target_ref` | 固定 `sample/<sample_id>` |
| `change_kind` | 上述 8 个取值之一 |
| `expected_impact_files` | 人工核对的预期影响文件（`recall_at_20` 的分母） |
| `expected_impact_symbols` | 人工核对的预期影响符号 ID（`file::qualname`） |
| `expected_failing_tests` | pytest node id 列表；在 target 上真实失败（`regression_recall` 的分母） |
| `expected_no_behavior_change` | `comment_only` 为 `true`，其余为 `false` |
| `high_risk_globs` | 高风险保底目录 glob，固定 `["app/core/**"]` |

`lock.json` 结构：

```json
{"shop_api": {"<sample_id>": {"base_sha": "…", "target_sha": "…"}}}
```

## 4. 语料校验口径

- 基线（`v1-baseline`）：`python -m pytest -q tests` 全部通过。
- 每个非 `comment_only` 样本：在 `sample/<sample_id>` 上执行 `python -m pytest -q tests`，
  `expected_failing_tests` 中的 node id 必须全部失败，且不得有标注之外的失败。
- `comment_only` 样本：`expected_failing_tests` 为空，target 与基线测试结果完全一致。
- 用例之间互相独立（无执行顺序依赖），API 用例对每个请求使用独立库存副本，
  因此单条 node id 单独执行、以及重复执行（双版本稳定性验证）都得到一致结果。

## 5. 运行评测

```bash
python scripts/run_eval.py --strategy combo   --samples eval/samples --out var/eval
python scripts/run_eval.py --strategy full    --samples eval/samples --out var/eval
python scripts/run_eval.py --strategy neighbor --samples eval/samples --out var/eval
python scripts/run_eval.py --strategy static  --samples eval/samples --out var/eval
python scripts/run_eval.py --strategy coverage --samples eval/samples --out var/eval
```

输出 `var/eval/{strategy}.json`（`StrategyReport` 序列化，字段 snake_case），重复运行覆盖写。
批量多策略汇总入口与样本级缓存见 `docs/EVAL.md`。

中间产物位于 `var/eval/_work/{sample_id}/`：`snapshot_base/`、`snapshot_target/`、
`baseline.log`、`coverage_base.json`。覆盖率在上述快照内以
`python -m pytest -q tests --cov=. --cov-context=test --cov-report=json:...` 采集；
采集失败（pytest/coverage 不可用或测试失败）时记 `coverage_missing=true` 并继续，
`coverage` 与 `combo` 策略退化为其余来源，不中断评测。`--skip-coverage` 可跳过采集。

## 6. 策略定义

| 策略 | 选择范围 | 理由来源 |
|---|---|---|
| `full` | 全部已索引测试（上界对照） | `full` |
| `neighbor` | 测试文件与变更/受影响文件同目录或同名模块 | `static_dependency`（detail 前缀 `neighbor:`） |
| `static` | 仅静态邻近（不读覆盖率、不做高风险保底） | `static_dependency` |
| `coverage` | 仅覆盖变更行 / 覆盖受影响符号 | `covered_change`、`covered_impact` |
| `combo` | 四源并集（覆盖变更行 ∪ 覆盖受影响符号 ∪ 静态依赖直连 ∪ 高风险保底） | 四源 |

`static` / `coverage` / `combo` 复用运行期选择器 `core.coverage.selector.select_tests`，仅在上游收窄可见来源；
`full` 与 `neighbor` 是评测专用策略，`full` 的 `SelectionReason.source` 值为 `full`（评测专用命名空间）。

## 7. 指标口径

- `recall_at_20`：前 20 个预测文件与 `expected_impact_files` 的交集 / 预期文件数；排序为「变更文件 → 影响文件（按 distance/rank）」。空预期返回 1.0。
- `regression_recall`：`selected_tests ∩ expected_failing_tests` / 预期失败用例数；空预期返回 1.0。
- `median_test_count_reduction`：逐样本 `1 - selected_count / total_count` 的中位数；`total_count == 0` 的样本跳过，无有效样本返回 0.0。
- `verified_rate`：`generated` 中 `status == "verified"` 的比例；总数 0 返回 0.0。

聚合口径（`build_report`）：`recall_at_20` 与 `regression_recall` 取逐样本算术平均，
样本报错或缺失按未命中（0.0）计；`verified_rate` 汇总全部样本的 `generated` 条目。

`per_sample` 条目字段：`sample_id`、`strategy`、`recall_at_20`、`regression_recall`、
`selected_count`、`total_count`、`test_count_reduction`、`coverage_missing`、
`impact_ranked_files`（前 20）、`selected_tests`、`generated`；失败样本额外含 `error`。

## 8. 已知边界

- 语料规模以可运行、可复现为优先：每个样本只注入一次显式变更，`tests/` 保持不变，
  因此 `expected_failing_tests` 是确定的人工真值，而非随机故障注入。
- 样例评测只执行基线 pytest 采集覆盖率，**不在目标版本执行测试**；`expected_failing_tests` 来自人工标注。
- 未接入测试生成流水线时 `generated` 恒为空，`verified_rate` 为 0.0（该指标覆盖 R10 生成阶段，口径已固定，待生成样本接入后再填充）。
- `app/api/orders.py` 对 `OrderService.checkout` 的调用依赖索引器可解析的 `CALLS` 边；若索引器判定不确定则按 C-CORE@1 记 `warnings`、不建边，此时该文件的 rank 可能后移，由 `recall_at_20` 如实反映。