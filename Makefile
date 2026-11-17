# ChangeProof 本地运行入口（Windows 无 make 时见 docs/DEPLOY.md 的等价命令）
PY ?= python
DB_URL ?= sqlite:///./var/changeproof.db
ARTIFACTS_ROOT ?= ./var/artifacts
API_PORT ?= 8000
SMOKE_API_PORT ?= 8100
SMOKE_WEB_PORT ?= 3100
E2E_API_PORT ?= 8100
E2E_WEB_PORT ?= 3100
E2E_DB_URL ?= sqlite:///./var/e2e/changeproof.db

.PHONY: init api web sample eval smoke eval-all docker-runner e2e gate

# 建 var/ 目录并初始化 SQLite 表结构（幂等，可重复执行）
init:
	mkdir -p var/artifacts var/samples var/eval
	$(PY) -c "from core.store import create_engine_and_session, init_schema; engine, _ = create_engine_and_session('$(DB_URL)'); init_schema(engine)"

api:
	$(PY) -m uvicorn services.api.main:app --host 0.0.0.0 --port $(API_PORT)

web:
	npm --prefix apps/web run dev

sample:
	$(PY) scripts/bootstrap_samples.py

eval:
	$(PY) scripts/run_eval.py

# 真实 uvicorn + Next.js 子进程端到端冒烟（默认 8100/3100，避开 8000/3000）
smoke:
	$(PY) scripts/dev_smoke.py --api-port $(SMOKE_API_PORT) --web-port $(SMOKE_WEB_PORT) --timeout 300

# 五策略批量评测，输出 var/eval/{strategy}.json 与 var/eval/summary.json
eval-all:
	$(PY) scripts/run_eval.py --strategies full,neighbor,static,coverage,combo --samples eval/samples --out var/eval

# 浏览器端到端门禁：独立 var/e2e 库的真实 uvicorn(8100) + Playwright(3100)；
# 浏览器未安装时 analysis.spec.ts 自动 skip，本目标回退 scripts/e2e_http.py 的 HTTP 等价断言
e2e:
	@if netstat -ano | grep -q "127.0.0.1:$(E2E_API_PORT) .*LISTENING"; then echo "E2E FAIL: 端口 $(E2E_API_PORT) 已被占用，请先停止残留进程"; exit 1; fi
	@rm -f var/e2e/changeproof.db || { echo "E2E FAIL: 无法删除 var/e2e/changeproof.db（可能被残留 uvicorn 占用）"; exit 1; }
	@rm -rf var/e2e/artifacts
	@mkdir -p var/e2e
	@set -u; \
	  CHANGEPROOF_DB_URL=$(E2E_DB_URL) CHANGEPROOF_ARTIFACTS_ROOT=./var/e2e/artifacts \
	    CHANGEPROOF_CORS_ORIGINS=http://127.0.0.1:$(E2E_WEB_PORT) \
	    $(PY) -m uvicorn services.api.main:app --host 127.0.0.1 --port $(E2E_API_PORT) >var/e2e/api.log 2>&1 & \
	  api_pid=$$!; \
	  web_pid=""; \
	  kill_port() { port_pid=$$(netstat -ano | grep "127.0.0.1:$$1 " | grep LISTENING | awk '{print $$5}' | head -1); 	    if [ -n "$$port_pid" ]; then if command -v taskkill >/dev/null 2>&1; then taskkill //F //T //PID $$port_pid >/dev/null 2>&1 || true; else kill $$port_pid 2>/dev/null || true; fi; fi; }; 	  cleanup() { if [ -n "$$web_pid" ]; then kill_port $(E2E_WEB_PORT); fi; kill_port $(E2E_API_PORT); }; \
	  trap cleanup EXIT INT TERM; \
	  $(PY) scripts/e2e_http.py --only-health --api-url http://127.0.0.1:$(E2E_API_PORT) || { echo "E2E FAIL: API 未就绪，见 var/e2e/api.log"; exit 1; }; \
	  (cd apps/web && npx playwright test) >var/e2e/playwright.log 2>&1 || true; \
	  cat var/e2e/playwright.log; \
	  if grep -q "1 passed" var/e2e/playwright.log; then echo "E2E PASS: playwright 1 passed"; exit 0; fi; \
	  echo "Playwright 未通过或缺少 chromium，回退 HTTP 等价断言（scripts/e2e_http.py）"; \
	  (cd apps/web && NEXT_PUBLIC_API_BASE=http://127.0.0.1:$(E2E_API_PORT) npm run dev -- -p $(E2E_WEB_PORT)) >var/e2e/web.log 2>&1 & \
	  web_pid=$$!; \
	  $(PY) scripts/e2e_http.py --api-url http://127.0.0.1:$(E2E_API_PORT) --web-url http://127.0.0.1:$(E2E_WEB_PORT)

# 一键门禁：compile → eval → smoke → e2e → docker-runner；汇总写 var/gate/summary.json
# 等价于 python scripts/gate.py，可加 --skip-docker / --skip-e2e / --refresh
gate:
	$(PY) scripts/gate.py

# 构建隔离执行器镜像；以 CHANGEPROOF_ISOLATION_MODE=docker 重启 API 后启用
docker-runner:
	docker build -f infra/Dockerfile.runner -t changeproof-runner:latest .
	@echo "设置 CHANGEPROOF_ISOLATION_MODE=docker 后重启 API 即启用容器执行"