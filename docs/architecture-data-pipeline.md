# Daily Stock Analysis - 数据链路与逻辑架构

> 本文档描述系统从数据采集到报告推送的完整数据链路，包含每个核心模块的职责、入参、出参及模块间关系。

## 逻辑架构图

![逻辑架构图](https://obsidian-1254275759.cos.ap-shanghai.myqcloud.com/dsa/docs/20260421113510_1.png)

> draw.io 源文件：[architecture-logical.drawio](assets/architecture-logical.drawio)

## 数据链路图（单股分析流程）

![数据链路图](https://obsidian-1254275759.cos.ap-shanghai.myqcloud.com/dsa/docs/20260421113510_2.png)

> draw.io 源文件：[architecture-data-pipeline.drawio](assets/architecture-data-pipeline.drawio)

---

## 1. 全局数据流概览

```
                              ┌──────────────────┐
                              │   用户入口层      │
                              │ CLI / API / Bot   │
                              └────────┬─────────┘
                                       │
                                       ▼
                        ┌──────────────────────────┐
                        │  StockAnalysisPipeline   │
                        │  src/core/pipeline.py    │
                        │  (主编排器)               │
                        └──────────┬───────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                     │
              ▼                    ▼                     ▼
    ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
    │ 数据采集层       │  │ 分析引擎层       │  │ 输出层           │
    │ DataFetcherMgr  │  │ Analyzer +      │  │ Notification +  │
    │ SearchService   │  │ TrendAnalyzer   │  │ Formatters      │
    └────────┬────────┘  └────────┬────────┘  └────────┬────────┘
             │                    │                     │
             ▼                    ▼                     ▼
    ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
    │ 外部数据源       │  │ LLM 服务        │  │ 通知渠道         │
    │ Akshare/Tushare │  │ Gemini/Claude/  │  │ WeChat/Feishu/  │
    │ YFinance/...    │  │ GPT-4 (LiteLLM) │  │ Telegram/Email  │
    └─────────────────┘  └─────────────────┘  └─────────────────┘
```

## 2. 单股分析 Pipeline 时序

```
[1] 历史行情采集 ──→ [2] 实时行情 ──→ [3] 筹码分布 ──→ [4] 基本面
         │                │               │               │
         └────────┬───────┘───────────────┘───────────────┘
                  │
                  ▼
           [5] 技术分析 (StockTrendAnalyzer)
                  │
                  ▼
           [6] 新闻情报 (SearchService)
                  │
                  ▼
           [7] AI 综合分析 (GeminiAnalyzer)
                  │
                  ▼
           [8] 报告格式化 (Formatters)
                  │
                  ▼
           [9] 多渠道推送 (NotificationService)
                  │
                  ▼
           [10] 持久化 (SQLite via DatabaseManager)
```

## 3. 核心模块详解

---

### 3.1 入口层

#### `main.py` — CLI 入口

| 项目 | 说明 |
|------|------|
| **职责** | 命令行参数解析、批量分析调度、定时任务、API 服务启动 |
| **入参** | `--stocks 600519,HK00700,AAPL` `--debug` `--dry-run` `--schedule` `--serve` `--market-review` `--backtest` |
| **出参** | 触发 Pipeline 运行，最终产出分析报告 + 推送通知 |
| **关键函数** | `run_full_analysis()` `_compute_trading_day_filter()` |
| **下游** | `StockAnalysisPipeline` `NotificationService` |

#### `server.py` + `api/app.py` — FastAPI 入口

| 项目 | 说明 |
|------|------|
| **职责** | REST API 服务，提供分析触发、状态查询、历史报告、SSE 实时推送 |
| **关键端点** | `POST /analyze` `GET /status/{task_id}` `GET /tasks/stream` `GET /stocks/{code}` `GET /history` |
| **下游** | `AnalysisService` → `StockAnalysisPipeline` |

**`POST /analyze` 入参/出参：**

```
入参:
  stock_code: str          # 股票代码或名称（自动解析）
  report_type: str         # "simple" | "detailed" | "full"

出参 (同步):
  AnalysisResultResponse   # 完整分析结果

出参 (异步):
  TaskAccepted (202)       # { task_id, status: "queued" }
  DuplicateTask (409)      # 同股票任务已在队列中
```

#### `bot/` — 机器人入口

| 项目 | 说明 |
|------|------|
| **职责** | 接收 WeChat/Telegram/Webhook 指令，路由到分析命令 |
| **关键命令** | `/analyze STOCK` `/market` `/history STOCK` `/research STOCK` |
| **下游** | `AnalysisService` / `StockAnalysisPipeline` |

---

### 3.2 编排层

#### `src/core/pipeline.py` — StockAnalysisPipeline（核心编排器）

| 项目 | 说明 |
|------|------|
| **职责** | 协调数据获取、技术分析、新闻搜索、AI 分析、通知推送的全流程 |
| **并发模型** | `ThreadPoolExecutor`，每只股票独立线程 |
| **断点续跑** | 检查 `has_today_data()` 跳过已有数据的采集 |

**核心入参：**

```python
stock_codes: List[str]       # 股票代码列表
config: Config               # 全局配置（max_workers, 数据源开关等）
query_id: str                # 追踪 ID
query_source: str            # "cli" | "api"
```

**核心出参：**

```python
List[AnalysisResult]         # 每只股票的分析结果
# 副作用：DB 写入、通知发送
```

**内部调用链：**

```
process_single_stock(code)
  ├→ fetch_and_save_stock_data()    # 数据采集 + 持久化
  ├→ get_realtime_quote()           # 实时行情
  ├→ get_chip_distribution()        # 筹码分布
  ├→ get_fundamental_context()      # 基本面数据
  ├→ StockTrendAnalyzer.analyze()   # 技术分析
  ├→ SearchService.search()         # 新闻情报
  ├→ GeminiAnalyzer.analyze()       # AI 综合分析
  ├→ save_analysis_report()         # 报告持久化
  └→ NotificationService.send()     # 多渠道推送
```

#### `src/core/market_review.py` — 大盘点评

| 项目 | 说明 |
|------|------|
| **入参** | 分析器实例、搜索服务、通知配置 |
| **出参** | 市场分析报告（A/港/美三市指数） |
| **下游** | `NotificationService` |

---

### 3.3 数据采集层

#### `data_provider/base.py` — DataFetcherManager（数据源管理器）

| 项目 | 说明 |
|------|------|
| **职责** | 多数据源策略管理，自动 failover + 限流 + 指数退避 |
| **设计模式** | Strategy Pattern |

**核心方法与入参出参：**

| 方法 | 入参 | 出参 |
|------|------|------|
| `get_daily_data(code, days)` | 标准化股票代码, 天数 | `pd.DataFrame` (OHLC + volume + pct_chg) |
| `get_realtime_quote(code)` | 标准化股票代码 | `RealtimeQuote` (price, volume_ratio, turnover_rate, pct_chg) |
| `get_chip_distribution(code)` | 标准化股票代码 | `ChipDistribution` (profit_ratio, avg_cost, concentration) |
| `get_fundamental_context(code)` | 标准化股票代码 | `FundamentalContext` (PE, PB, dividend_yield, total_market_cap) |
| `get_stock_name(code)` | 标准化股票代码 | `str` (股票名称) |
| `normalize_stock_code(code)` | 任意格式代码 | 标准化代码 (e.g. `"600519"`, `"HK00700"`, `"AAPL"`) |

**DataFrame 标准列：**

```python
STANDARD_COLUMNS = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']
```

**数据源优先级与覆盖：**

| 数据源 | A 股 | 港股 | 美股 | 实时行情 | 筹码 | 基本面 |
|--------|------|------|------|----------|------|--------|
| Akshare | Y | Y | - | Y | Y | Y |
| Tushare | Y | - | - | - | - | - |
| YFinance | - | Y | Y | Y | - | - |
| Longbridge | - | Y | - | Y | - | - |
| Baostock | Y | - | - | - | - | - |
| E-Finance | Y | - | - | Y | - | - |
| pytdx | Y | - | - | Y | - | - |
| Tickflow | Y | - | - | Y | - | - |

---

### 3.4 技术分析引擎

#### `src/stock_analyzer.py` — StockTrendAnalyzer

| 项目 | 说明 |
|------|------|
| **职责** | 计算技术指标、判断趋势状态 |

**入参：**

```python
df: pd.DataFrame             # StockDaily 记录 (OHLC + volume)
```

**出参 `TrendAnalysisResult`：**

```python
TrendAnalysisResult:
    trend_status: str         # STRONG_BULL / BULL / WEAK_BULL / CONSOLIDATION /
                              # WEAK_BEAR / BEAR / STRONG_BEAR
    trend_strength: float     # 0.0 ~ 1.0
    ma_alignment: str         # "多头排列" / "空头排列" / "交叉"
    ma5: float
    ma10: float
    ma20: float
    ma60: float
    bias_ma5: float           # 乖离率 (%)
    volume_ratio: float       # 量比
    buy_signal_level: int     # 信号强度 0~5
```

---

### 3.5 新闻情报引擎

#### `src/search_service.py` — SearchService

| 项目 | 说明 |
|------|------|
| **职责** | 多引擎新闻搜索 + URL 正文提取 + 结果持久化 |

**入参：**

```python
stock_code: str               # 股票代码
stock_name: str               # 股票名称（用于构建查询词）
news_strategy_profile: str    # "short" (7天) / "medium" (14天) / "long" (30天)
```

**出参 `SearchResponse`：**

```python
SearchResponse:
    formatted_news: str       # 格式化新闻摘要（直接注入 LLM prompt）
    raw_results: List[dict]   # 原始搜索结果
    source_engine: str        # 实际使用的搜索引擎
```

**支持的搜索引擎：**

| 引擎 | 说明 |
|------|------|
| Bocha | 国内财经搜索 |
| Tavily | AI 优化搜索 |
| Brave | 隐私搜索 |
| SerpAPI | Google 代理 |
| SearXNG | 自建聚合搜索 |
| Minimax | LLM 兜底摘要 |

---

### 3.6 AI 分析引擎

#### `src/analyzer.py` — GeminiAnalyzer

| 项目 | 说明 |
|------|------|
| **职责** | 调用 LLM 综合多维数据生成结构化分析报告 |
| **LLM 路由** | 通过 LiteLLM Router 支持 Gemini / Claude / GPT-4 等模型 |

**入参（注入 LLM prompt 的数据）：**

```python
technical: TrendAnalysisResult    # 技术面指标
realtime_quote: RealtimeQuote     # 实时行情（价格、量比、换手率）
chip_ ChipDistribution       # 筹码结构（获利比例、集中度）
fundamental: FundamentalContext    # 基本面（PE、PB、股息率）
intelligence: SearchResponse      # 新闻情报（最新动态、风险提示、催化剂）
market_context: MarketContext      # 市场环境（牛熊状态、板块表现）
report_language: str              # "zh" | "en"
```

**出参 `AnalysisResult`：**

```python
AnalysisResult:
    stock_code: str
    stock_name: str
    sentiment_score: int          # 0~100 综合情绪分
    operation_advice: str         # "买入" / "持有" / "卖出" / "观望"
    decision_type: str            # "buy" / "hold" / "sell" / "observe"
    confidence_level: str         # "high" / "medium" / "low"
    analysis_summary: str         # 分析摘要
    dashboard: dict               # 嵌套结构化面板（见 3.8 Schema）
    trend_prediction: str
    risk_warning: str
    buy_reason: str
    report_language: str
```

**内容完整性校验 (`check_content_integrity`)：**

必须字段：`sentiment_score`, `operation_advice`, `analysis_summary`, `dashboard.core_conclusion.one_sentence`, `dashboard.intelligence.risk_alerts`

Buy/Hold 决策追加必须：`dashboard.battle_plan.sniper_points.stop_loss`

---

### 3.7 通知推送层

#### `src/notification.py` + `src/notification_sender/` — NotificationService

| 项目 | 说明 |
|------|------|
| **职责** | 将分析结果格式化并广播到所有已配置通知渠道 |
| **并发** | 多渠道并行发送 |
| **降级** | 单渠道失败不阻塞主流程 |

**入参：**

```python
analysis_results: List[AnalysisResult]    # 分析结果列表
report_language: str                       # 输出语言
config: Config                             # 渠道配置
```

**出参：** 无返回值（副作用：消息发送到各渠道）

**支持渠道：**

| 渠道 | 格式 | 说明 |
|------|------|------|
| 企业微信 | Markdown / 图片 | Webhook 推送 |
| 飞书 | Markdown | Webhook 推送 |
| Telegram | Markdown | Bot API |
| Email | HTML | SMTP |
| Pushover | 文本 | 移动推送 |
| PushPlus | Markdown | 国内推送 |
| Server酱3 | Markdown | 移动推送 |
| Discord | Markdown | Bot API |
| Slack | Markdown | Webhook |
| ASTRBOT | JSON | 自定义集成 |
| Custom Webhook | JSON | 自定义 HTTP |

---

### 3.8 数据模型 (Schema)

#### `src/schemas/report_schema.py` — AnalysisReportSchema

LLM 输出的 JSON 校验结构：

```
AnalysisReportSchema
├── stock_name: str
├── sentiment_score: int (0~100)
├── trend_prediction: str
├── operation_advice: str
├── decision_type: str
├── confidence_level: str
├── analysis_summary: str
│
├── dashboard: Dashboard
│   ├── core_conclusion: CoreConclusion
│   │   ├── one_sentence: str          # 一句话结论
│   │   ├── signal_type: str           # 信号类型
│   │   ├── time_sensitivity: str      # 时效性
│   │   └── position_advice            # 持仓/空仓建议
│   │
│   ├── data_perspective: DataPerspective
│   │   ├── trend_status               # MA 排列 + 趋势分
│   │   ├── price_position             # 价格位置 + 支撑/压力
│   │   ├── volume_analysis            # 量比 + 换手 + 含义
│   │   └── chip_structure             # 获利比 + 集中度 + 健康度
│   │
│   ├── intelligence: Intelligence
│   │   ├── latest_news: str           # 最新动态
│   │   ├── risk_alerts: List[str]     # 风险提示
│   │   ├── positive_catalysts         # 利好催化
│   │   └── earnings_outlook           # 业绩展望
│   │
│   └── battle_plan: BattlePlan
│       ├── sniper_points              # 理想买点 / 止损 / 止盈
│       ├── position_strategy          # 仓位策略 / 入场计划
│       └── action_checklist           # 操作清单
│
├── risk_warning: str
├── buy_reason: str
└── news_summary: str
```

---

### 3.9 持久化层

#### `src/storage.py` — DatabaseManager (SQLite + SQLAlchemy)

| ORM 模型 | 主要字段 | 唯一约束 |
|-----------|----------|----------|
| `StockDaily` | code, date, OHLC, volume, amount, pct_chg, MA5/10/20, volume_ratio | (code, date) |
| `NewsIntel` | query_id, stock_code, title, snippet, url, source, published_date | — |
| `AnalysisReport` | stock_code, date, report_json, sentiment_score, decision_type | — |
| `AnalysisContext` | stock_code, date, input_snapshot (审计用) | — |
| `FundamentalSnapshot` | stock_code, date, PE, PB, dividend_yield, total_market_cap | — |
| `BacktestResult` | stock_code, strategy, metrics | — |

#### `src/repositories/` — Repository 层

| Repository | 职责 |
|------------|------|
| `stock_repo.py` | StockDaily CRUD, `get_latest()`, `get_range()`, `has_today_data()` |
| `analysis_repo.py` | AnalysisReport CRUD, 历史报告查询 |
| `portfolio_repo.py` | 账户/交易/持仓数据 |
| `backtest_repo.py` | 回测结果存取 |

---

### 3.10 辅助模块

| 模块 | 职责 | 入参 | 出参 |
|------|------|------|------|
| `src/formatters.py` | 报告格式转换 | `AnalysisResult` | Markdown / JSON / HTML |
| `src/report_language.py` | 多语言支持 | 语言代码 + 原文 | 本地化文本 |
| `src/market_context.py` | 市场环境判断 | 市场类型 | 角色提示词 + 市场规则 |
| `src/core/trading_calendar.py` | 交易日历 | 股票代码 + 日期 | 是否交易日、有效交易日 |
| `src/core/config_registry.py` | 策略配置管理 | — | 分析策略参数 |
| `src/core/backtest_engine.py` | 回测引擎 | 历史分析结果 | 命中率、精准度 |

---

## 4. 跨模块数据类型流转

```
                    DataFrame (OHLC)
                         │
                         ▼
             ┌─── StockTrendAnalyzer ───┐
             │   TrendAnalysisResult    │
             └───────────┬──────────────┘
                         │
   RealtimeQuote ────────┤
   ChipDistribution ─────┤
   FundamentalContext ────┤
   SearchResponse ────────┤
   MarketContext ─────────┤
                         │
                         ▼
              ┌─── GeminiAnalyzer ───┐
              │   AnalysisResult     │
              └──────────┬───────────┘
                         │
              ┌──────────┼──────────────┐
              │          │              │
              ▼          ▼              ▼
         Formatters   Database    NotificationService
         (MD/JSON)   (SQLite)    (10+ channels)
```

## 5. 容错与降级策略

| 环节 | 策略 |
|------|------|
| 数据源调用 | 多源 failover + 指数退避 + 限流 |
| 实时行情缺失 | 跳过，用历史收盘价替代 |
| 筹码数据缺失 | 跳过，AI 分析中标记 N/A |
| 新闻搜索失败 | 降级到 Minimax LLM 摘要兜底 |
| LLM 响应不完整 | `check_content_integrity` + placeholder 填充 |
| 单通知渠道失败 | 不阻塞主流程，日志告警 |
| 数据库写入 | 唯一约束去重，断点续跑 |

## 6. 多市场支持

| 市场 | 代码格式 | 示例 | 主要数据源 |
|------|----------|------|-----------|
| A 股 (沪深京) | 6 位数字 | `600519`, `000001`, `920748` | Akshare, Tushare, Baostock |
| 港股 | HK + 5 位 | `HK00700`, `HK01810` | YFinance, Longbridge, Akshare |
| 美股 | 字母 ticker | `AAPL`, `MSFT` | YFinance |

代码标准化规则 (`normalize_stock_code`)：
- `SH600519` → `600519`
- `000001.SZ` → `000001`
- `1810.HK` → `HK01810`
- `AAPL` → `AAPL`
