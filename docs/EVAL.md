# 批量评测（`scripts/run_eval.py`）

本文件说明 **C-EVAL-V3@1**（兼容 C-EVAL@1 / C-EVAL-V2@1）的批量评测口径、样本级缓存、
覆盖率逐测试兜底、生成验证接入与复现命令。标注样本位于 `eval/samples/*.json`（真值只
来自人工标注，运行期预测结果禁止回写），评测产物全部落在 `var/eval/`。

## 1. 复现命令

```bash
# 1) 幂等重建样例仓库、分支/tag 与 eval/samples/*.json + lock.json
python scripts/bootstrap_samples.py

# 2) 批量评测：一次构建样本 artifacts，五策略复用
python scripts/run_eval.py
# 等价于
python scripts/run_eval.py --strategies full,neighbor,static,coverage,combo \
    --samples eval/samples --out var/eval

# 3) 只跑部分策略 / 忽略缓存重算 / 跳过基线覆盖率采集
python scripts/run_eval.py --strategies combo,static --refresh
python scripts/run_eval.py --strategies combo --skip-coverage

# 4) 单策略入口（C-EVAL@1 兼容，输出同 var/eval/<strategy>.json）
python scripts/run_eval.py --strategy combo

# 5) 关闭逐测试覆盖率兜底 / 关闭生成验证（输出与 C-EVAL-V2@1 一致）
python scripts/run_eval.py --no-coverage-per-test --no-generation
python scripts/run_eval.py --strategies combo --no-generation
```

参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--strategies` | `full,neighbor,static,coverage,combo` | 逗号分隔；非法名称直接报错退出 |
| `--strategy` | 无 | 单策略入口，提供时覆盖 `--strategies` |
| `--samples` | `eval/samples` | 标注样本目录（自动排除 `lock.json`） |
| `--out` | `var/eval` | 报告与缓存输出目录 |
| `--refresh` | 关 | 忽略样本级缓存，重建全部 artifacts |
| `--skip-coverage` | 关 | 不跑基线 pytest 覆盖率采集，`coverage`/`combo` 退化为其余来源 |
| `--coverage-per-test` / `--no-coverage-per-test` | 开 | 基线覆盖率缺测试级上下文时逐测试采集覆盖行 |
| `--with-generation` / `--no-generation` | 开 | 对回归样本执行受限生成与双版本验证 |
| `--coverage-cache` | `var/eval/coverage_cache` | 逐测试覆盖率缓存目录（按 `base_sha` 共享） |
| `--coverage-cache-version` | `v1` | 覆盖率缓存版本；结构变化时自增以失效旧缓存 |
| `--project-root` | 脚本上级目录 | 相对路径参数的解析基准 |

## 2. 样本级缓存

每个样本的 artifacts（基线/目标快照索引、diff 映射结果、影响传播结果、基线覆盖率）
构建成本最高，因此在**策略循环之外**只构建一次，然后五个策略复用同一份内存对象：

```text
var/eval/
├── cache/<sample_id>.json     # 样本 artifacts 缓存
├── coverage_cache/<base_sha>.json  # 逐测试覆盖率缓存（同一基线只采集一次）
├── _work/<sample_id>/         # 快照与覆盖率/日志等中间产物
├── <strategy>.json            # StrategyReport（C-EVAL@1 结构）
└── summary.json               # 多策略汇总
```

缓存载荷：

```json
{
  "cache_version": 2,
  "sample_id": "...", "repo_name": "shop_api",
  "base_sha": "...", "target_sha": "...",
  "coverage_missing": false,
  "coverage_context_source": "per_test",
  "generated": [],
  "degraded": [], "warnings": [],
  "changed": [], "impacts": [], "symbols": [], "tests": [], "coverage": []
}
```

命中条件（全部满足，且未传 `--refresh`）：

1. `cache_version` 与脚本内 `CACHE_VERSION` 一致；
2. `base_sha`/`target_sha` 与 `eval/samples/lock.json` 中当前样本的 sha 完全一致。

`bootstrap_samples.py` 每次重建都会改变 sha，因此重建样本后旧缓存自动失效，不会读到
过期 artifacts。缓存文件损坏或字段缺失时按未命中处理并重建；缓存写失败只是失去加速，
不影响本轮指标。

运行结束在 `summary.json.cache_hits` 报告命中数，便于确认「artifacts 只构建一次」。

## 3. 指标口径（严格沿用 C-EVAL@1）

指标只依赖人工标注与预测结果，空值语义固定，调用方不得再兜底改写：

| 指标 | 定义 | 空值语义 |
|---|---|---|
| `recall_at_20` | 影响分析给出的文件排名前 20 与 `expected_impact_files` 的交集占预期比例 | 空预期返回 `1.0` |
| `regression_recall` | 被选中的 `expected_failing_tests` 比例 | 空预期返回 `1.0` |
| `median_test_count_reduction` | 逐样本 `1 - selected_count/total_count` 的中位数 | `total_count <= 0` 的样本跳过；无有效样本返回 `0.0` |
| `verified_rate` | artifacts 中 `generated[]` 里 `status == "verified"` 的比例 | 无生成样本返回 `0.0` |

策略聚合为逐样本指标的算术平均（`median_test_count_reduction` 例外，取中位数），由
`core.evaluation.metrics.build_report` 统一计算。

五种策略的含义（详见 `eval/README.md`）：`full` 全量上界对照、`neighbor` 同目录/同名模块
邻近、`static` 仅静态依赖、`coverage` 仅覆盖率来源、`combo` 四源并集（覆盖变更行 ∪
覆盖受影响符号 ∪ 静态依赖直连 ∪ 高风险保底）。

## 4. 输出结构

`var/eval/<strategy>.json`：`StrategyReport` 序列化，字段 `strategy`、`samples_total`、
`recall_at_20`、`regression_recall`、`median_test_count_reduction`、`verified_rate`、
`per_sample[]`、`generated_at`（snake_case）。

`var/eval/summary.json`：

```json
{
  "generated_at": "2026-11-14T00:00:00+00:00",
  "samples_total": 40,
  "cache_hits": 40,
  "strategies": [
    {"strategy": "combo", "samples_total": 40, "recall_at_20": 0.9,
     "regression_recall": 0.85, "median_test_count_reduction": 0.72,
     "verified_rate": 0.0}
  ],
  "subsets": {
    "regression_only": {"samples_total": 30, "strategies": {"combo": {"...": "同上结构"}}},
    "no_behavior_change": {"samples_total": 4, "strategies": {"combo": {"...": "同上结构"}}}
  },
  "coverage_context_source": "per_test",
  "generated_samples": 9,
  "verification_failures": 26
}
```

`coverage_context_source`（`contexts|per_test|missing`）、`generated_samples`、
`verification_failures` 是 C-EVAL-V3@1 新增字段，仅在逐测试兜底或生成验证开启时输出；
两者都关闭时 `summary.json` 与 C-EVAL-V2@1 完全一致。`generated_samples` 为回归子集中
至少产出一条 `verified` 生成物的样本数，`verification_failures` 为其余回归样本数
（含缺 sha/报错样本），二者之和等于 `subsets.regression_only.samples_total`。

子集定义：

- `regression_only`：`expected_failing_tests` 非空的样本（能体现回归召回）；
- `no_behavior_change`：`expected_no_behavior_change == true` 的样本（如 `comment_only`），
  重点看 `median_test_count_reduction` 是否接近 1（不应被全量测试淹没）。

子集指标与顶层策略指标使用同一口径，逐样本条目保持与 `per_sample` 相同的字段结构。

## 5. 失败语义

- 单样本 artifacts 构建或策略执行异常：写入该策略 `per_sample[i].error`，指标按未命中计
  （`recall_at_20`/`regression_recall` 记 `0.0`），**不中断**整轮评测；失败样本不写缓存。
- `lock.json` 缺少该样本 sha：记 `skipped_missing_sha`（同样按未命中计）。
- 样本声明了仓库路径但目录不存在：记 `sample_repo_missing`。
- 基线覆盖率采集失败（pytest/coverage 不可用或基线测试失败）：记
  `per_sample[i].coverage_missing = true`，`coverage`/`combo` 退化为其余来源，继续评测。
- `--strategies` 出现未知策略名：参数错误，直接退出（不产出半套报告）。

## 6. 约束

- 评测只读取 `eval/samples/*.json`、`eval/samples/lock.json`、`var/samples/<repo>` 与
  `var/eval/coverage_cache/`；样本 sha 一律来自 `lock.json`，代码与样本中不写死 sha。
- 人工真值只存在于 `eval/samples/`，运行期预测结果禁止回写该目录。

## 7. 覆盖率逐测试兜底与生成验证（C-EVAL-V3@1）

### 7.1 逐测试覆盖率兜底

基线 `coverage.json` 由 `pytest --cov=. --cov-context=test` 产出。若解析后**没有**测试级
上下文（只剩空 `test_node_id`），且 `--coverage-per-test` 开启，则对目标版本索引出的每个
测试 `node_id` 单独执行：

```text
python -m pytest -q <node_id> --cov=. --cov-report=json:<abs>
```

`cwd` 为基线快照，读取 `executed_lines` 生成
`CoverageRecord(commit_sha=base_sha, test_node_id=node_id, file_path=posix 相对路径, lines)`。
单测试上限 `MAX_PER_TEST_COVERAGE_RUNS = 60`，超出时只采集前 60 个并记
`per_sample[i].warnings = ["coverage_per_test_capped"]`。

覆盖率缓存键为 `(base_sha, --coverage-cache-version)`，文件
`var/eval/coverage_cache/<base_sha>.json`，内容：

```json
{
  "cache_version": "v1",
  "base_sha": "...",
  "source": "per_test",
  "records": [{"commit_sha": "...", "test_node_id": "...", "file_path": "app/core/money.py", "lines": [1, 2]}]
}
```

40 组样本共享同一 `base_sha` 时只采集一次；`--coverage-cache-version` 变化即失效旧缓存。
`--no-coverage-per-test` 时不读该缓存，采集路径与 C-EVAL-V2@1 完全一致。

### 7.2 生成与双版本验证

对 `expected_failing_tests` 非空的样本，在 artifacts（含快照）构建后调用 C-GEN@1 公开
函数：`build_generation_request` → `generate_test_suggestions(..., default_model_adapter())`
→ `validate_generated_tests(..., repeats=2)`，`job_factory` 由本地执行器在单测节点上跑
`python -m pytest -q <node_id>`。

生成输入取**目标版本中已消失的变更符号**（`symbol_id` 不在目标索引中）：离线适配器的断言
是「符号在目标版本仍可访问」，只有这类符号才可能得到「base 通过 / target 失败」的可判定
结果；仍存在的符号必然只能停在 `pending_review`。

结果写入 artifacts 与样本缓存的 `generated`：

```json
[{"test_node_id": "...", "status": "verified|pending_review|rejected",
  "rationale": "...", "analysis_source": "model", "validation_evidence": {"repeats": 2, "base": [], "target": []}}]
```

`verified` 唯一判定沿用 C-GEN@1（`base_passed ∧ target_failed ∧ stable`）。模型不可用、
接口异常或全部生成物被拒时 `generated` 为空（或全为 `rejected`）、`degraded=["generation"]`，
样例记入 `per_sample[i].error` 的路径不触发，评测继续。`--no-generation` 时不做生成，
`generated` 恒为空；样本级缓存命中时不会重跑 pytest 生成或覆盖率采集。