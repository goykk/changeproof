# infra：可选 Docker 路径

本目录提供 **可选** 的容器化运行方式。ChangeProof 的主链路是本地进程：
`make init && make api` 无需 Docker 即可完成仓库导入、分析、测试执行与报告查看。
Docker 仅用于需要更强隔离（`isolation_mode=docker`）或统一环境的场景。

> 本机 Docker daemon 当前不可用，`docker compose` 命令会直接失败。
> 这不影响任何功能验证，直接按「回退方式」走本地路径即可。

## 目录内容

| 文件 | 说明 |
|---|---|
| `Dockerfile.api` | API 镜像：Python 3.12，非 root 用户（uid 10001），以 uvicorn 启动 `services.api.main:app` |
| `Dockerfile.runner` | 测试执行器镜像：Python 3.12 + pytest/coverage，以 `65534:65534`（nobody）运行，运行期不需要网络 |
| `docker-compose.yml` | `api` 服务默认启用；`postgres`、`minio` 属于 `optional` profile，**默认不创建** |

## 构建与启动（需要可用的 Docker daemon）

```bash
# 仅 API（默认路径）
docker compose -f infra/docker-compose.yml up --build

# 同时启用可选服务（首版未使用 PostgreSQL/MinIO，仅保留入口）
docker compose -f infra/docker-compose.yml --profile optional up --build

# 构建执行器镜像（可选，供 DockerExecutor 使用）
docker build -f infra/Dockerfile.runner -t changeproof-runner:latest .
```

API 监听 `8000`；`../var` 挂载为 `/app/var`，SQLite 与产物落盘位置与本地一致。
被分析仓库需要以只读方式挂进容器（compose 中已挂 `../var/samples:/samples`），
在 API 里导入仓库时填写容器内路径（如 `/samples/shop_api`）。

## 执行器镜像约定

- 非 root：镜像与 `DockerExecutor` 均以 `65534:65534` 运行。
- 可断网：依赖在构建期装好，运行期以 `--network=none --cpus=1 --memory=512m --pids-limit=128` 启动。
- 挂载：`snapshot_dir -> /work:ro`、`artifact_dir -> /artifacts`（可写）。
- 容器挂载 daemon socket、以 docker 模式执行时，请自行确认 `/var/run/docker.sock` 的权限风险。

## 回退方式（Docker 不可用时）

1. 不启动任何容器，直接本地运行：

   ```bash
   make init
   make api      # 默认 isolation_mode=local
   make web
   ```

2. 若已在 compose/环境变量中把隔离模式指向 `docker`，改回本地（默认即 `local`，或移除相关环境变量）。
3. 回退不影响分析结果与评测口径：`isolation_mode` 会如实标注为 `local`，
   本地执行日志首行记录 `network_enforced=false`（不伪造网络隔离）。
4. `make sample` / `make eval` 全程不依赖 Docker。

## 常见问题

- **`docker compose` 报连接失败**：daemon 未启动或不可达，按「回退方式」走本地路径。
- **容器内导入仓库报 `not_a_git_repository`**：宿主机路径在容器内不可见，改用容器内挂载路径。
- **产物目录权限错误**：确认 `../var` 对容器内 uid 可写（runner 写 `/artifacts` 需要宿主机目录可写）。