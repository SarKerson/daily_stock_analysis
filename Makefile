.PHONY: help install install-web run run-debug dry-run serve web stocks market-review \
       schedule backtest test lint check clean kill-web

PYTHON := uv run python
NPM_REGISTRY := https://registry.npmmirror.com
WEB_DIR := apps/dsa-web

help: ## 显示所有可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ─── 安装 ────────────────────────────────────────────────

install: ## 安装 Python 依赖
	uv venv --python 3.11 .venv 2>/dev/null || true
	uv pip install -r requirements.txt

install-web: ## 安装并构建前端
	cd $(WEB_DIR) && npm ci --registry $(NPM_REGISTRY) && npm run build

# ─── 运行分析 ─────────────────────────────────────────────

run: ## 运行完整分析（所有自选股 + 大盘复盘）
	$(PYTHON) main.py

run-debug: ## 调试模式运行
	$(PYTHON) main.py --debug

dry-run: ## 仅获取数据，不调用 LLM 分析
	$(PYTHON) main.py --dry-run

stocks: ## 分析指定股票，用法: make stocks S=600519,AAPL
	$(PYTHON) main.py --stocks $(S)

market-review: ## 仅运行大盘复盘
	$(PYTHON) main.py --market-review

schedule: ## 启动定时任务模式
	$(PYTHON) main.py --schedule

backtest: ## 运行回测
	$(PYTHON) main.py --backtest

# ─── Web 服务 ─────────────────────────────────────────────

serve: ## 启动分析 + Web 服务
	WEBUI_AUTO_BUILD=false $(PYTHON) main.py --serve

web: static/index.html ## 仅启动 Web 服务（不自动分析）
	WEBUI_AUTO_BUILD=false $(PYTHON) main.py --serve-only

static/index.html: $(WEB_DIR)/package.json
	@if [ ! -f $@ ]; then echo "前端未构建，正在构建..."; $(MAKE) install-web; fi

api: ## 直接用 uvicorn 启动 API（热重载）
	uv run uvicorn server:app --reload --host 0.0.0.0 --port 8000

kill-web: ## 停止占用 8000 端口的进程
	@lsof -i :8000 -sTCP:LISTEN -t 2>/dev/null | xargs kill 2>/dev/null || echo "端口 8000 无占用"

# ─── 验证 ──────────────────────────────────────────────────

test: ## 运行测试（跳过网络依赖）
	uv run python -m pytest -m "not network" -q

test-all: ## 运行所有测试（含网络）
	uv run python -m pytest -q

lint: ## 代码检查
	uv run python -m flake8 src/ api/ data_provider/ --max-line-length=120 --count --statistics

check: ## CI 检查（等同 ci_gate.sh）
	./scripts/ci_gate.sh

test-llm: ## 测试 LLM 连通性
	$(PYTHON) test_env.py --llm

# ─── 清理 ──────────────────────────────────────────────────

clean: ## 清理缓存和临时文件
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache
