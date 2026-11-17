# 部署与启动指南

ChangeProof 采用 **本地优先** 的运行方式：一条命令初始化，再分别启动后端与前端。
Docker 为可选路径，且 **不是** 任何功能的硬依赖（本机 Docker daemon 不可用，见第 6 节）。

## 1. 环境要求

- Python 3.12+（3.13 兼容）
- Node.js 20+（仅前端）
- Git（仓库校验、提交枚举、快照导出）
- `make`（可选；Windows 无 `make` 时使用第 3 节的等价命令）

## 2. 安装

```bash
pip install -e .                 # 后端与 core
npm --prefix apps/web install    # 前端
```

可选：`cp .env.example .env` 后按需修改环境变量（未设置时使用代码内默认值）。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `CHANGEPROOF_DB_URL` | `sqlite:///./var/changeproof.db` | 首版只使用 SQLite；PostgreSQL 仅保留连接串入口 |
| `CHANGEPROOF_ARTIFACTS_ROOT` | `./var/artifacts` | 各阶段产物与执行日志根目录 |
| `CHANGEPROOF_LLM` | `mock` | `mock` 为离线确定性建议；`ollama` 走 HTTP，失败自动降级 |
| `CHANGEPROOF_OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama 地址 |
| `CHANGEPROOF_OLLAMA_MODEL` | `llama3.1` | Ollama 模型名 |
| `NEXT_PUBLIC_API_BASE` | `http://localhost:8000` | 前端访问的后端地址 |
| `PORT` | `3000` | 前端端口 |

## 3. 本地启动（推荐路径）

```bash
make init     # 创建 var/ 并初始化 SQLite 表结构（幂等）
make api      # uvicorn services.api.main:app，默认 0.0.0.0:8000
make web      # npm --prefix apps/web run dev，默认 3000
```

可选步骤：

```bash
make sample   # 重建受控样例仓库 var/samples/shop_api 并生成标注样本
make eval     # 运行五策略评测，输出 var/eval/{strategy}.json
```

`make` 可用变量：`PY`（默认 `python`）、`DB_URL`、`ARTIFACTS_ROOT`、`API_PORT`，例如
`make api API_PORT=8100`。

### Windows / 无 make 的等价命令

```bat
mkdir var\artifacts var\samples var\eval
python -c "from core.store import create_engine_and_session, init_schema; engine, _ = create_engine_and_session('sqlite:///./var/changeproof.db'); init_schema(engine)"
python -m uvicorn services.api.main:app --host 0.0.0.0 --port 8000
npm --prefix apps/web run dev
python scripts/bootstrap_samples.py
python scripts/run_eval.py
```

## 4. 验证服务可用

```bash
curl http://localhost:8000/api/health
# {"status":"ok","storage":"sqlite","isolation_mode":"local","llm":"mock"}
```

浏览器打开 `http://localhost:3000`，依次：导入仓库 → 选择两个提交 → 创建 changeset → 触发分析 → 查看影响页与报告页。
评测结果页在 `http://localhost:3000/eval`（需先执行 `make eval`）。

## 5. 运行产物与日志

- 数据库：`var/changeproof.db`
- 任务产物：`var/artifacts/{task_id}/{stage}/`，含 `snapshot/`、`baseline.log`、`coverage.json`、
  `index_base.json`、`index_target.json`、`impact.json`、`selections.json`、`run_target.log`、`run_base.log`、`report.json`
- 执行日志：`{artifact_dir}/{job_id}.log`，首行标注 `network_enforced=false`（本地执行器如实标注，不伪造网络隔离）
- 评测输出：`var/eval/{strategy}.json`

## 6. Docker（可选路径）

完整说明见 `infra/README.md`。要点：

```bash
docker build -f infra/Dockerfile.runner -t changeproof-runner:latest .   # 可选：执行器镜像
docker compose -f infra/docker-compose.yml up --build                    # 仅 api
docker compose -f infra/docker-compose.yml --profile optional up --build # 追加可选 postgres/minio
```

- `infra/docker-compose.yml` 中 `postgres`、`minio` 属于 `optional` profile，**默认不启动**；首版未使用它们，仅保留入口。
- `infra/Dockerfile.api` 以非 root（uid 10001）运行，SQLite 与产物挂在 `/app/var`。
- `infra/Dockerfile.runner` 以 `65534:65534` 运行，构建期装好 pytest/coverage，运行期可断网。

### Docker daemon 不可用时的回退

本机 Docker daemon 当前不可达，`docker compose` 不可用。回退步骤：

1. 不启动容器，按第 3 节执行 `make init && make api && make web`。
2. 保持默认 `isolation_mode=local`；若此前通过环境变量或 compose 指向了 `docker`，移除该项或改回本地。
3. 分析、测试选择、执行与评测结果不受影响，报告中 `isolation_mode` 会如实显示 `local`，
   网络隔离仅在 Docker 路径下才真实生效（本地仅记录 `network_enforced=false`）。
4. 需要更强隔离时，等 daemon 可用后再启用 Docker 执行器与 `infra/Dockerfile.runner`。

## 7. 端口与排障

- 端口：后端默认 `8000`（`make api API_PORT=8100`），前端默认 `3000`（`PORT=3001 make web`），
  并通过 `NEXT_PUBLIC_API_BASE` 让前端指向新端口。
- `not_a_git_repository`（422）：导入路径必须是一个已初始化的 Git 仓库根目录；容器内需使用挂载后的容器路径。
- 覆盖率缺失：报告 `dynamic_facts.coverage_missing=true`，分析仍继续，静态依赖与保底策略照常产出选择结果。
- LLM 不可用：`CHANGEPROOF_LLM=ollama` 但无可用模型时自动降级为 mock，报告 `degraded_steps` 会标注 `generation`。
- 前端 404 / 请求失败：确认 `NEXT_PUBLIC_API_BASE` 与实际后端端口一致，并已重启 `make web`（该变量在构建期注入）。