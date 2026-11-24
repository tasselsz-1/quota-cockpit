# 配额与上下文 (`quota-cockpit`)

> Antigravity 2.0 侧边栏三合一实时监控插件（Sidecar AUX Pane）——零模型 Token 消耗、零外部 npm 依赖、严格只读本地数据。
>
> A zero-token, zero-dependency Antigravity 2.0 side-pane plugin combining **5-hour rolling Image Generation Quota**, **Dual Model Quota Pools (5h + Weekly)**, and **Exact Conversation Context & Prompt Cache Hit Analytics** in one dashboard.

## 核心功能

### 1. 生图配额 · 连续 5 小时时间滚轴 + 官方 Token 估算 + 自学习水位带
- **增量只读扫描**：以只读模式（`?mode=ro`）扫描近 30 天 `~/.gemini/antigravity/conversations/*.db` 中的 `generate_image` 记录，配合 `mtime + size` 缓存（v4 校验与原子写入），热刷新约 100ms，自动剥离突发每分钟限流（`429_RPM`）与本地参数错误（`CLIENT_ERR`）。
- **连续时间滚轴回血**：严格按 `[now - 5h, now]` 滑动窗口追踪实时占用，精准倒计时 **下批回血时间** 与 **完全清空时刻**，按自然 30 分钟刻度绘制 **分段回血时间表**（`+N次`、`Xk tok` 与累计释放比例），并提供 `30m / 1h / 2h` 快捷预测。
- **官方 `gemini-3.1-flash-image` Token 计量与带垫图均图预估**：依据官方 1K 出图计量（Prompt 估算 + 参考图 `1,120 tok/张` + 1K 出图 `1,120 tok/张` + 最小内部思考 `~250 tok/次`）计算每次与每窗口 Token 及 API 等效美元开销，独立统计 **带垫图（`≥1` 张参考图）均图 Token** 与 `0 / 1 / 2 / 3+` 参考图分档画像，自动换算当前预估剩余可出图张数及 `30m / 1h / 2h` 回血等效张数。
- **双维透明进度条与自学习水位带**：在后端未公开生图桶总额的前提下，自动吸收近 30 天 **已证实 0-429 安全峰值** 与 **实测 5h 429 锁定观测（自动过滤高 EOF 噪声锁）**，同时展示 Token 与次数双进度条及瓶颈维度，并支持切换为手动阈值模式（自定义软/硬次数上限或 Token 上限）。
- **429 官方冷却倒计时与可选账本联动**：自动解析 429 响应中的 `quotaResetTimeStamp` 与底层模型名，精准倒计时；若锁定后已出现新成功出图则自动解除冷却态；工作区存在 `api_call_ledger.json` 时自动汇总展示。

### 2. 当前模型配额池 · 双池 × 双窗口实时监控
- 并行调用 Antigravity 本地 Language Server 的 `RetrieveUserQuotaSummary` 与 `GetAvailableModels` RPC，实时展示 **池 A（Google Gemini）** 与 **池 B（Claude / 第三方）** 各自的 **5 小时额度（5h）** 与 **周额度（weekly）** 剩余百分比、重置时刻与每秒倒计时（满额未消耗时明确标注 `满额`）。
- 顶栏常驻当前对话模型的 `5h + 周额度` 速览条；自动高亮当前对话所在配额池，并按双窗口瓶颈 `min(5h, weekly)` 在低于 20% 且对侧池充裕时给出跨池切换建议。
- 标注模型梯队与角色（`T0`–`T4`、`IMG`），并提示生图模型虽在接口中归属池 A，但实际调用命中后端独立 5h 生图桶。

### 3. 当前对话上下文与 Prompt 缓存命中 · Exact `/context` 明细
- 解析原生对话 protobuf，实时显示 `used / limit tokens`、单次请求预算预警、100 点占用网格、分类占比（用户 / 模型 / 工具 / 系统 / Rules / Skills / MCP）、Checkpoints 及已加载的 Knowledge / Artifact / Skill / System 文件列表。
- **Prompt 缓存命中监控**：直接解析本地对话库逐轮 `model_usage`（`cache_read_tokens` 与 `uncached_input_tokens`），在总览与上下文详情页实时展示最新一轮缓存命中率、上轮前缀复用率、当前 `/compact` 压缩段与整场累计命中率、真实缓存失效重建次数（自动区分冷启/新增大文件与真超时失效）、距上轮请求时长（含冷却提示）及近 16 轮命中率迷你轨迹图。
- 支持一键复制纯文本报告（含 Prompt 缓存摘要），便于定位长会话上下文膨胀或配合 `/compact` 瘦身。

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
- **严格只读与零 Token 消耗**：只以只读方式读取本地对话 SQLite、可选工作区账本与官方本地 RPC；**不上传任何数据、不调用外部网络 API、不消耗任何模型 Token、不修改原始对话数据库**。
- **配置与缓存位置**：用户自定义阈值与增量扫描缓存保存在 Antigravity 分配的 Sidecar 数据目录（`ANTIGRAVITY_EXECUTABLE_DATA_DIR`）下的 `settings.json` 与 `image-scan-cache.json`。

## 目录结构

```text
quota-cockpit/
├── plugin.json                  # Antigravity 插件清单
├── README.md
├── LICENSE                      # MIT License
├── .gitignore
├── assets/
│   └── logo.svg                 # 插件图标
├── sidecars/cockpit/
│   ├── sidecar.json             # Sidecar AUX Pane 注册配置
│   ├── package.json
│   ├── main.mjs                 # 本地 HTTP 服务与 RPC 聚合层
│   ├── pools_logic.mjs          # 双池 × 双窗口配额归并逻辑
│   ├── index.html               # 侧边栏 Web UI（零构建依赖）
│   ├── quota_report.py          # 5h 生图配额 / Token / 水位带分析器
│   └── context_report.py        # 对话 protobuf 上下文解析器
└── tests/
    ├── test_pools.mjs           # Node.js 单元测试
    ├── test_quota_report.py     # 生图配额 Python 单元测试
    └── test_context_report.py   # 上下文与 Prompt 缓存 Python 单元测试
```

## 运行单元测试

在仓库根目录执行：

```bash
node --test tests/test_pools.mjs
python -m unittest discover -s tests -v
```

## 开源协议

基于 [MIT License](LICENSE) 开源。
