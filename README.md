# 配额与上下文 (`quota-cockpit`)

> Antigravity 2.0 侧边栏四合一实时监控插件（Sidecar AUX Pane）——零模型 Token 消耗、零外部 npm 依赖、严格只读本地数据。
>
> A zero-token, zero-dependency Antigravity 2.0 side-pane plugin combining **5h & 7d Rolling Image Generation Quota**, **Dual Model Quota Pools (5h + Weekly Rolling Recovery Forecast)**, **Exact Conversation Context & Prompt Cache Hit Analytics**, and **Google Flow Video Quota & Anti-Throttle Monitor** in one dashboard.

> **快速开发与排查导航**：本项目已完成全量模块化拆分，请优先查阅 [`PROJECT_INDEX.md`](./PROJECT_INDEX.md)（按改动意图秒定位到对应小文件与函数行号，无需通读整文件；改动后运行 `python scripts/generate_index.py` 刷新索引）。

## 核心功能

### 1. 生图配额 · 5h & 7d 双时间滚轴 + 官方 Token 估算 + 自学习水位带
- **增量只读扫描与旧库归档保护**：以只读模式（`?mode=ro`）扫描近 30 天 `~/.gemini/antigravity/conversations/*.db` 中的 `generate_image` 记录，配合 `mtime + size` 缓存（v6 校验、旧库归档保留与原子写入），热刷新约 100ms，自动剥离突发每分钟限流（`429_RPM`）、跨日周池限流（`429_WEEKLY`）与本地参数错误（`CLIENT_ERR`）；即使 Antigravity 自动清理超过约 500 个的旧对话 `.db` 文件，缓存仍会按窗口保留归档事件。
- **5h + 7d 双连续时间滚轴回血**：5h 窗口严格按 `[now - 5h, now]` 滑动窗口追踪实时占用，按自然 30 分钟刻度绘制分段回血时间表（`30m / 1h / 2h` 快捷预测）；**7 天周滚轴**按自然日汇总未来 7 天每日滚出释放量（`24h / 48h / 72h` 释放预测、30 天无周锁安全周峰及周池 429 独立识别，避免污染 5h 上限校准）。
- **官方 `gemini-3.1-flash-image` Token 计量与带垫图均图预估**：依据官方 1K 出图计量（Prompt 估算 + 参考图 `1,120 tok/张` + 1K 出图 `1,120 tok/张` + 最小内部思考 `~250 tok/次`）计算每次与每窗口 Token 及 API 等效美元开销，独立统计 **带垫图（`≥1` 张参考图）均图 Token** 与 `0 / 1 / 2 / 3+` 参考图分档画像，自动换算当前预估剩余可出图张数及 `30m / 1h / 2h` 回血等效张数。
- **双维透明进度条、峰值固定与永久自学习水位带**：在后端未公开生图桶总额的前提下，自动吸收近 30 天 **已证实 0-429 安全峰值** 与 **实测 5h 429 锁定观测（自动过滤高 EOF 噪声锁及开头缺失的截断锁）**，并通过永久校准账本（`image-calibration.json`）单调保护历史峰值；**支持在面板一键固定 5h 峰值张数**（输入自定义峰值或锁定实测峰，永久生效不受旧对话清理或时间滚出影响，同时继续按带垫图均图 Token 实时折算剩余张数，随时可修改或取消固定）；同时展示 Token 与次数双进度条及瓶颈维度，也支持切换为手动阈值模式。
- **429 官方冷却倒计时与可选账本联动**：自动解析 429 响应中的 `quotaResetTimeStamp` 与底层模型名，精准区分 5h 冷却与跨日周池冷却并倒计时；若锁定后已出现新成功出图则自动解除冷却态；工作区存在可选 `api_call_ledger.json` 时自动汇总展示。

### 2. 当前模型配额池 · 双池 × 双窗口实时监控 + 7 天周回血预测
- 并行调用 Antigravity 本地 Language Server 的 `RetrieveUserQuotaSummary` 与 `GetAvailableModels` RPC，实时展示 **池 A（Google Gemini）** 与 **池 B（Claude / 第三方）** 各自的 **5 小时额度（5h）** 与 **周额度（weekly）** 剩余百分比、参考锚点与每秒倒计时（满额未消耗时明确标注 `满额`）。
- **双源融合周回血追踪**：后台自动将官方 `remainingFraction` 变动写入轻量 8 天环形快照账本（`quota-history.json`），绕过黑盒模型权重记录真实掉血轨迹与实时回血速率（`%/h` 与预计回满时长）；冷启动期自动通过本地 7 天对话库加权消耗（`pool_usage_report.py`）锚定官方当前 `usedPct`，平移 `+7 天` 生成未来 7 天按日预计回血比例与恢复后余量曲线，攒满快照后自动过渡为官方实测轨迹。
- 顶栏常驻当前对话模型的 `5h + 周额度` 速览条；自动高亮当前对话所在配额池，并按双窗口瓶颈 `min(5h, weekly)` 在低于 20% 且对侧池充裕时给出跨池切换建议。
- 标注模型梯队与角色（`T0`–`T4`、`IMG`），并提示生图模型虽在接口中归属池 A，但实际调用命中后端独立 5h 生图桶。

### 3. 当前对话上下文与 Prompt 缓存命中 · Exact `/context` 明细
- 解析原生对话 protobuf，实时显示 `used / limit tokens`、单次请求预算预警、100 点占用网格、分类占比（用户 / 模型 / 工具 / 系统 / Rules / Skills / MCP）、Checkpoints 及已加载的 Knowledge / Artifact / Skill / System 文件列表。
- **Prompt 缓存命中监控**：直接解析本地对话库逐轮 `model_usage`（`cache_read_tokens` 与 `uncached_input_tokens`），在总览与上下文详情页实时展示最新一轮缓存命中率、上轮前缀复用率、当前 `/compact` 压缩段与整场累计命中率、真实缓存失效重建次数（自动区分冷启/新增大文件与真超时失效）、距上轮请求时长（含冷却提示）及近 16 轮命中率迷你轨迹图。
- 支持一键复制纯文本报告（含 Prompt 缓存摘要），便于定位长会话上下文膨胀或配合 `/compact` 瘦身。

### 4. Google Flow 生视频配额 · 账号积分 + 模型可生估算 + 1h/24h 防风控滚轴
- **毫秒级纯本地零风控监控（`flow_report.py` / `flow_quota/`）**：日常刷新严格只读本地 `gflow-cli` SQLite 数据库（`gflow.db?mode=ro`）与可选防限流账本（`~/.gemini/gflow_traffic_ledger.json`），零网络请求、零浏览器启动、零 Profile 锁争抢；未安装 `gflow-cli` 时优雅降级。
- **手动按需同步真实积分 + 同步后自动扣减**：因代理环境下 `gflow credits` 会回退浏览器读取，为防止后台高频轮询触发风控，真实余额采用一键手动同步（`POST /api/flow/sync-credits`，自动检测活跃 Profile 锁避让冲突并落盘 `flow-credits-cache.json`）；两次同步之间依据本地账本自动扣除新增积分，实时估算当前余额与净可用预算（默认扣除 `100` 积分安全底线）。
- **主力视频模型可生段数与退役隔离**：按净可用积分换算 `Veo 3.1 Lite`（~10 积分）、`Omni Flash 1.2`（~20 积分）、`Veo 3.1 Lite LP`（~10 积分）剩余可生成段数（标注 `1080p` 超分免费），并将 `2026-12-05` 即将退役模型隔离折叠。
- **1h / 24h 防限流滚轴回血与熔断警示**：实时监控 1h 频次、24h 频次、24h 积分预算、最小间隔冷却（支持在面板自定义阈值）及滚轴释放倒计时；命中 WAF 人工熔断或 429 定时冷却时总览与详情页高亮提醒；无全局账本时自动降级由 `gflow.db` 驱动，同时支持联动工作区可选的 `flow_run_state.json`。

## 环境要求

- **Antigravity 2.0**（支持全局 Sidecar AUX Pane 插件）
- **Node.js 18+**（无需 `npm install`，零第三方依赖）
- **Python 3.10+**（仅使用标准库）

## 安装方式

将本仓库克隆到 Antigravity 全局插件目录 `~/.gemini/config/plugins/quota-cockpit` 即可自动加载（无需重启或重启后即生效）：

### Windows (PowerShell)

```powershell
git clone https://github.com/tasselsz-1/quota-cockpit.git "$env:USERPROFILE\.gemini\config\plugins\quota-cockpit"
```

### macOS / Linux

```bash
git clone https://github.com/tasselsz-1/quota-cockpit.git "$HOME/.gemini/config/plugins/quota-cockpit"
```

安装完成后：
1. 在 Antigravity 的 **Customizations / Plugins** 页面确认 **配额与上下文 (`quota-cockpit`)** 已启用。
2. 在侧边栏打开 **配额与上下文** 面板即可实时查看。

### 更新插件

```bash
cd ~/.gemini/config/plugins/quota-cockpit && git pull
```

## 架构与隐私说明

- **运行方式**：原生 Antigravity Sidecar Web UI（`SIDECAR_UI_ENTRYPOINT_AUX_PANE`），本地回环服务仅在本机运行。
- **严格只读与零 Token 消耗**：只以只读方式读取本地对话 SQLite、本地 `gflow.db`、可选工作区账本与官方本地 RPC（Google Flow 真实积分仅在用户点击同步按钮时按需只读查询）；**不上传任何数据、不消耗任何模型 Token 或视频积分、不修改原始对话数据库**。
- **配置与缓存位置**：用户自定义阈值、增量扫描归档缓存、永久生图校准账本、Flow 积分缓存与 8 天配额变动快照保存在 Antigravity 分配的 Sidecar 数据目录（`ANTIGRAVITY_EXECUTABLE_DATA_DIR`）下的 `settings.json`、`image-scan-cache.json`、`image-calibration.json`、`flow-credits-cache.json`、`pool-usage-cache.json` 与 `quota-history.json`。

## 目录结构（详见 [`PROJECT_INDEX.md`](./PROJECT_INDEX.md)）

```text
quota-cockpit/
├── plugin.json                  # Antigravity 插件清单
├── README.md
├── PROJECT_INDEX.md             # 全项目模块与符号精确索引（按需求秒定位）
├── LICENSE                      # MIT License
├── .gitignore
├── assets/
│   └── logo.svg                 # 插件图标
├── scripts/
│   └── generate_index.py        # 自动生成并校验 PROJECT_INDEX.md
├── sidecars/cockpit/
│   ├── sidecar.json             # Sidecar AUX Pane 注册配置
│   ├── package.json
│   ├── main.mjs                 # 薄 HTTP 路由编排与静态资源托管入口
│   ├── pools_logic.mjs          # 双池逻辑薄门面（重导出 lib/pools_*.mjs）
│   ├── quota_report.py          # 生图配额薄 CLI 门面（重导出 image_quota/）
│   ├── flow_report.py           # Flow 生视频薄 CLI 门面（重导出 flow_quota/）
│   ├── context_report.py        # 上下文解析薄 CLI 门面（重导出 context_parser/）
│   ├── pool_usage_report.py     # 本地 7 天分池对话消耗增量扫描器
│   ├── lib/                     # Node 后端模块（settings / runner / ls_client / reports / pools_*）
│   ├── web/                     # 前端静态资源（index.html / styles.css / js/*.js 原生 ESM）
│   ├── image_quota/             # 生图配额 Python 子包（constants / scanner / schedules / calibration / report）
│   ├── flow_quota/              # Flow 配额 Python 子包（constants / readers / sync / report）
│   └── context_parser/          # 上下文解析 Python 子包（protobuf / collector / renderer / models）
└── tests/
    ├── test_pools.mjs           # Node.js 双池与周回血单元测试
    ├── test_web_assets.mjs      # 前端静态资源、ESM 导入与布局顺序测试
    ├── test_sidecar_routes.mjs  # 后端设置归一化与路由辅助单元测试
    ├── test_quota_report.py     # 生图配额 5h/7d Python 单元测试
    ├── test_flow_report.py      # Google Flow 配额与防限流 Python 单元测试
    ├── test_pool_usage_report.py# 分池对话消耗扫描 Python 单元测试
    ├── test_context_report.py   # 上下文与 Prompt 缓存 Python 单元测试
    └── test_project_index.py    # PROJECT_INDEX.md 同步漂移校验测试
```

## 运行单元测试与索引校验

在仓库根目录执行：

```bash
node --test tests/test_pools.mjs tests/test_web_assets.mjs tests/test_sidecar_routes.mjs
python -m unittest discover -s tests -v
python scripts/generate_index.py --check
```

## 开源协议

基于 [MIT License](LICENSE) 开源。
