# ChangeProof

变更影响分析与回归测试平台：从 Git 变更出发构建符号级影响图，结合覆盖率与静态依赖选择测试集，
在隔离执行器中断言结果，并输出带证据与残余风险的分析报告。

## 架构

```text
core/schema       共享类型、枚举、符号 ID 规则、仓储 Protocol、任务状态机（纯数据，无 IO）
core/store        SQLAlchemy ORM 与仓储实现（唯一持久化所有者）
core/repo         Git 仓库校验、提交枚举（跨全部 ref）、只读快照导出、双版本 diff
core/indexer      AST / FastAPI / SQLAlchemy / pytest → Symbol 与 DependencyEdge
core/impact       diff → ChangedSymbol（AST 语义等价过滤），反向传播 → ImpactResult
core/coverage     coverage.json 解析、覆盖映射、四源测试选择（每条带理由）
core/generation   受限测试生成（五类模板 + env/状态/守卫边界模板）
core/validation   双版本验证与稳定性判定（verified 闸门）
core/evaluation   样本加载、五策略执行、指标计算与汇总
services/api      FastAPI 路由 + 状态机控制 + 可选 token 权限
services/worker   分析编排、失败处理与报告聚合
services/runner   Executor 实现：本地进程（默认）/ Docker（可选）
apps/web          Next.js 14 前端（仓库/变更/影响图/报告/评测）
```

数据分层互不混算：静态事实（`ast` / `framework` / `git_history`）、动态事实（`coverage` / `execution`）、
模型建议（`model`）；人工真值仅存在于 `eval/samples/*.json` 与 `eval/blog/*.json`。

## 环境要求

- Python 3.12+（本机 3.13 实测）
- Node.js 20+（仅前端）
- 可选：Docker（隔离执行器）、Ollama（真实模型建议）

## 安装

```bash
pip install -e .
```

环境变量见 `.env.example`，默认 `sqlite:///./var/changeproof.db` 与 `var/artifacts`。

## 快速开始

```bash
# 一键门禁（约 1 分钟，含指标断言）
python scripts/gate.py

# 全量评测（40 组样本 × 五策略）
PYTHONPATH=. python scripts/run_eval.py --refresh

# 联调冒烟（真实 uvicorn + Next.js）
PYTHONPATH=. python scripts/dev_smoke.py --api-port 8111 --web-port 3111

# 故障注入（5 类）
PYTHONPATH=. python scripts/fault_injection.py

# 不变式单测（21 个）
PYTHONPATH=. python -m pytest tests -q
```

## 启动开发服务

```bash
make init

# 后端（本机 8000 常被占用，改用 8102）
CHANGEPROOF_DB_URL=sqlite:///./var/demo2/changeproof.db \
  uvicorn services.api.main:app --host 127.0.0.1 --port 8102

# 前端（3000 常被占用，改用 3101）
NEXT_PUBLIC_API_BASE=http://127.0.0.1:8102 npm --prefix apps/web run dev -- -p 3101
```

可选权限（默认关闭）：

```bash
export CHANGEPROOF_AUTH=token
export CHANGEPROOF_API_TOKENS="viewer-token:viewer,operator-token:operator"
```

## 文档

- `docs/ACCEPTANCE.md`：P0 逐项验收对照、实测指标与已知边界
- `docs/DEPLOY.md`：本地优先部署与 Docker 可选路径
- `docs/DEMO.md`：5～8 分钟演示脚本
- `docs/EVAL.md`：评测参数、缓存结构与指标口径
- `.codeartsdoer/plans/changeproof/design.md`：全部冻结契约（C-*）与实现期澄清
- `.codeartsdoer/plans/changeproof/task.md`：T1–T40 任务清单

## 交付清单与证据

| 能力 | 入口 | 证据 |
|---|---|---|
| 一键门禁 | `python scripts/gate.py` | `var/gate/summary.json` |
| 主评测（40 组 × 五策略） | `python scripts/run_eval.py --refresh` | `var/eval/summary.json` + `var/eval/{strategy}.json` |
| 第二仓库泛化（5 组） | `python scripts/run_blog_eval.py` | `var/eval_blog/summary.json` |
| 真实开源仓库分析 | `python scripts/run_real_eval.py --repo var/realrepos/full-stack-fastapi-template --base f8dd304^ --target f8dd304` | `var/eval_real/summary.json` |
| testmon 对照 | `python scripts/run_testmon_eval.py` | `var/eval_testmon/summary.json` |
| 故障注入（7 类） | `python scripts/fault_injection.py` | 终端输出 `FAULT INJECTION 7/7 PASS` |
| 联调冒烟 | `python scripts/dev_smoke.py --api-port 8111 --web-port 3111` | `var/smoke/last_task.json` |
| 浏览器 E2E | `npx --prefix apps/web playwright test` | `apps/web/e2e/analysis.spec.ts` |
| 权限四态 | `pytest tests/test_auth.py -q` | `tests/test_auth.py` |
| 不变式单测 | `pytest tests -q`（29 例） | `tests/` |
| 样例生成 | `python scripts/bootstrap_samples.py` / `bootstrap_blog.py` | `var/samples/*` + `eval/*/lock.json` |

### 最终实测（2026-11-17）
- 主评测 combo：recall@20 **1.000** / 回归召回 **1.000** / 降幅 **0.538** / verified_rate 0.667；`verification_failures=1`。
- 第二仓库：combo **1.000 / 1.000 / 0.556**，base 全通过且标注与实测一致。
- 真实仓库：impacts **5→12**、selected **58→48**。
- testmon：召回 0.881 / 降幅 0.904（本项目按"宁可冗余不可漏"选择召回优先）。
- 门禁：**GATE PASS（54.2s）**，docker-runner PASS。

### 已知边界
跨文件全局引用依赖 `IMPORTS` 边；动态反射无法建边；Go 未支持；TypeScript 只索引不执行；`verified_rate` 分母是"有生成条目的回归样本"，剩余 1 组保持 `pending_review`；`testmon` 对照脚本会切换样例仓库工作区，不可与主评测/门禁同时运行。