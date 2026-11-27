# quota-cockpit Project Index

> **Quick Usage**: Agents and contributors should read `PROJECT_INDEX.md` first to jump directly to the exact small module and line range instead of reading monolithic files. Refresh after structural or symbol edits via `python scripts/generate_index.py`, and verify synchronization via `python scripts/generate_index.py --check`.

## 1. 按需求秒定位路由表 (Task → Files → Key Symbols)

| Task / Edit Intent | Target Files | Key Symbols / Anchors |
| --- | --- | --- |
| Top Tab order / HTML shell | `sidecars/cockpit/web/index.html` | `.tabs`, `data-tab="overview|image|pools|context|flow"` |
| Overview card order & summary | `sidecars/cockpit/web/js/render-overview.js` | `renderOverview` |
| Sticky top header | `sidecars/cockpit/web/js/render-header.js` | `updateHeader` |
| Image Quota tab / pin peak / settings UI | `sidecars/cockpit/web/js/render-image.js`, `sidecars/cockpit/web/js/actions.js` | `renderImage`, `saveSettings`, `pinPeak`, `resetCalibration` |
| Model Quota Pools tab / weekly recovery UI | `sidecars/cockpit/web/js/render-pools.js` | `renderPools`, `renderPoolWeeklyRecoveryOverview`, `renderPoolWeeklyRecoveryDetail` |
| Context, Prompt Cache & Step Count tab UI | `sidecars/cockpit/web/js/render-context.js` | `renderContext`, `renderCacheCard`, `renderCacheOverviewStrip`, `renderStepsCard`, `renderStepsOverviewStrip` |
| Google Flow cross-modal Video/Image tab & overview card UI | `sidecars/cockpit/web/js/render-flow.js`, `sidecars/cockpit/web/js/actions.js` | `renderFlow`, `renderFlowOverviewCard`, `saveFlowSettings`, `syncFlowCredits` |
| CSS / theme / scrollbar | `sidecars/cockpit/web/styles.css` | `:root` tokens, `.card`, `.pill`, `.tabs`, scrollbar rules |
| Formatters / countdown tickers | `sidecars/cockpit/web/js/formatters.js` | `fmtTok`, `fmtDuration`, `cdSpan`, `tickCountdowns`, `fmtCredits` |
| Shared frontend state / tab routing / poll focus guard | `sidecars/cockpit/web/js/state.js`, `sidecars/cockpit/web/js/app.js` | `state`, `POLL_MS`, `render`, `setTab` |
| HTTP API routes & static asset wiring | `sidecars/cockpit/main.mjs`, `sidecars/cockpit/lib/runner.mjs` | `app.api(...)`, `registerWebAssets`, `collectWebAssets` |
| Settings defaults, v2 Flow migration, clamping & quota history store | `sidecars/cockpit/lib/settings.mjs` | `DEFAULT_SETTINGS`, `FLOW_POLICY_VERSION`, `migrateLegacyFlowDefaults`, `clampInt`, `normalizeSettings`, `createSettingsStore`, `createQuotaHistoryStore` |
| Antigravity LS RPC & pool refresh cache | `sidecars/cockpit/lib/ls_client.mjs` | `lsRequest`, `createPoolsService` |
| Model role/tier rules (`ROLE_RULES`) & pool aggregation | `sidecars/cockpit/lib/pools_models.mjs` | `ROLE_RULES`, `classifyModelRole`, `parseQuotaSummary`, `buildPoolsPayload`, `resolveActivePool` |
| 7d weekly rolling recovery & snapshot ledger | `sidecars/cockpit/lib/pools_recovery.mjs` | `WINDOW_7D_SEC`, `recordQuotaSnapshot`, `buildWeeklyRecovery`, `enrichPoolsWithWeeklyRecovery` |
| Report runner caching & single-flight locks | `sidecars/cockpit/lib/reports.mjs` | `createReportsService` |
| Image quota constants & official token formula | `sidecars/cockpit/image_quota/constants.py` | `WINDOW_5H_SEC`, `DEFAULT_SOFT_CAP`, `DEFAULT_HARD_CAP`, `compute_event_tokens` |
| Image DB scanner, 429 classification & archive salvage | `sidecars/cockpit/image_quota/scanner.py` | `classify_error`, `extract_tool_image_info`, `scan_single_db`, `scan_conversations`, `_salvage_archived_event` |
| Image 5h/7d rolling schedules & with-ref avg | `sidecars/cockpit/image_quota/schedules.py` | `compute_per_image_avg`, `build_rolling_schedule`, `build_weekly_rolling_schedule` |
| Image 30d self-learning watermark, ledger & peak pinning | `sidecars/cockpit/image_quota/calibration.py` | `compute_calibration`, `load_calibration_ledger`, `pin_calibration_peak`, `merge_calibration_with_ledger` |
| Image anchor selection & workspace ledger linking | `sidecars/cockpit/image_quota/report.py` | `scan_workspace_ledgers`, `compute_anchor`, `build_report` |
| Flow video/image models, 4 daily pools, 10m WAF constants & gflow discovery | `sidecars/cockpit/flow_quota/constants.py` | `VIDEO_MODELS`, `IMAGE_QUOTA_POOLS`, `MODEL_BY_ID`, `is_peak_hours`, `infer_image_quota_pool`, `find_gflow_bin`, `canonical_model_id` |
| Flow read-only v2 ledger, gflow.db, locks & workspace video/image runs | `sidecars/cockpit/flow_quota/readers.py` | `read_ledger`, `read_gflow_db`, `inspect_profile_locks`, `inspect_single_flight_lock`, `scan_workspace_flow_runs` |
| Flow manual live credit sync & structlog stripping | `sidecars/cockpit/flow_quota/sync.py` | `parse_gflow_json_stdout`, `sync_live_credits` |
| Flow 10m WAF wall, 0-credit image pools, 24h video caps & circuit breakers | `sidecars/cockpit/flow_quota/report.py` | `build_rolling_buckets`, `compute_breaker`, `compute_model_breakers`, `merge_profiles`, `build_report` |
| Conversation protobuf decoding & cache hit analytics | `sidecars/cockpit/context_parser/protobuf.py`, `sidecars/cockpit/context_parser/collector.py` | `Msg`, `read_varint`, `collect_cache_stats`, `collect_step_stats`, `collect_stats` |
| Context CLI rendering & model window table | `sidecars/cockpit/context_parser/renderer.py`, `sidecars/cockpit/context_parser/models.py` | `render`, `load`, `NATIVE_CONTEXT_WINDOWS`, `context_limit` |
| Local 7d per-pool usage scanner | `sidecars/cockpit/pool_usage_report.py` | `pool_key_for_usage`, `scan_single_db_usage`, `build_pool_usage_report` |
| Index generator & drift guard | `scripts/generate_index.py`, `tests/test_project_index.py` | `generate_markdown`, `main`, `TestProjectIndex` |

## 2. HTTP API & 静态路由速查

| Method | Route | Definition | Responsibility |
| --- | --- | --- | --- |
| `GET` | `/api/dashboard` | `sidecars/cockpit/main.mjs` (L87-L111) | Aggregates pools, context, imageQuota, flowQuota, and active pool metadata. |
| `POST` | `/api/settings` | `sidecars/cockpit/main.mjs` (L113-L133) | Persists normalized settings, invalidates image/flow caches, and refreshes both reports. |
| `POST` | `/api/flow/sync-credits` | `sidecars/cockpit/main.mjs` (L135-L151) | Runs explicit manual Flow live credit sync (`--sync-credits`) when profile is unlocked. |
| `POST` | `/api/calibration/pin` | `sidecars/cockpit/main.mjs` (L153-L171) | Pins or unpins the empirical 5h image safe peak (`--pin-peak` / `--unpin-peak`). |
| `POST` | `/api/calibration/reset` | `sidecars/cockpit/main.mjs` (L173-L183) | Deletes `image-calibration.json`, invalidates cache, and rebuilds image quota report. |
| `GET` | `/api/report` | `sidecars/cockpit/main.mjs` (L185-L192) | Refreshes pool state (cached) and returns context report for a single conversation. |

**Static Web Routes** (`sidecars/cockpit/lib/runner.mjs` → `registerWebAssets`, all served with `Cache-Control: no-store` & `X-Content-Type-Options: nosniff`, 13 routes): `/`, `/web/index.html`, `/web/js/actions.js`, `/web/js/app.js`, `/web/js/formatters.js`, `/web/js/render-context.js`, `/web/js/render-flow.js`, `/web/js/render-header.js`, `/web/js/render-image.js`, `/web/js/render-overview.js`, `/web/js/render-pools.js`, `/web/js/state.js`, `/web/styles.css`

## 3. 持久化数据与外部只读数据源

### 持久化 JSON 文件 (`ANTIGRAVITY_EXECUTABLE_DATA_DIR`)

| File | Location Env | Purpose |
| --- | --- | --- |
| `settings.json` | `ANTIGRAVITY_EXECUTABLE_DATA_DIR` | User soft/hard image caps, count/anchor mode, and Flow v2 10m WAF/1h/24h/credit/gap settings (`lib/settings.mjs`). |
| `model-catalog.json` | `ANTIGRAVITY_EXECUTABLE_DATA_DIR` | Cached LS `GetAvailableModels` catalog snapshot merged into pool & context lookups. |
| `image-scan-cache.json` | `ANTIGRAVITY_EXECUTABLE_DATA_DIR` | Incremental SQLite scan cache keyed by conversation DB stamp (`image_quota/scanner.py`). |
| `image-calibration.json` | `ANTIGRAVITY_EXECUTABLE_DATA_DIR` | 30-day self-learning safe/429 peak ledger, truncated lock history, and pinned peak (`image_quota/calibration.py`). |
| `flow-credits-cache.json` | `ANTIGRAVITY_EXECUTABLE_DATA_DIR` | Manual live Flow credit sync snapshot and per-profile credit balance history (`flow_quota/readers.py`). |
| `pool-usage-cache.json` | `ANTIGRAVITY_EXECUTABLE_DATA_DIR` | Cached 7d per-pool request usage buckets extracted from local conversation DBs (`pool_usage_report.py`). |
| `quota-history.json` | `ANTIGRAVITY_EXECUTABLE_DATA_DIR` | 8-day rolling pool snapshot time series for weekly recovery predictions (`lib/pools_recovery.mjs`). |

### 外部只读数据源

| Source | Read-Only Consumer & Usage |
| --- | --- |
| `conversations/*.db` | Antigravity SQLite conversation DBs (`steps`, `gen_metadata`) read via `?mode=ro` for context, image quota, and pool usage. |
| `gflow.db` | Local `gflow-cli` SQLite database read via `?mode=ro` for profiles, video/image operations, model counts, and WAF/error telemetry. |
| `gflow_traffic_ledger.json` | Local cross-modal Flow v2 traffic ledger (`GFLOW_TRAFFIC_LEDGER` or `~/.gemini/gflow_traffic_ledger.json`) tracking 10m WAF tokens, 0-credit image pools, 24h video/credit burn, and global/per-pool breakers. |
| `gflow_single_flight.lock` | Global cross-modal single-flight PID lock (`~/.gemini/gflow_single_flight.lock`) inspected read-only by `flow_quota/readers.py`. |
| `api_call_ledger.json` | Workspace image generation call ledger linked by `image_quota/report.py`. |
| `flow_run_state.json` / `flow_image_state.json` / `flow_project_index.json` | Workspace Flow video/image runner state & project rotation files discovered by `flow_quota/readers.py`. |

## 4. 分层模块目录总览（自动统计行数）

### Frontend (`sidecars/cockpit/web/`) — 12 files, 2901 lines

| File | Lines | Description |
| --- | --- | --- |
| `sidecars/cockpit/web/index.html` | 39 | Top header, 5-tab navigation shell, and classic modular script entries. |
| `sidecars/cockpit/web/js/actions.js` | 209 | Frontend API actions for settings, Flow sync, peak pinning, calibration reset, and refresh. |
| `sidecars/cockpit/web/js/app.js` | 251 | DOM diff renderer, tab router, event delegation, and polling loop. |
| `sidecars/cockpit/web/js/formatters.js` | 173 | Token/duration/currency/credit formatters, countdown tags, and grid cell helpers. |
| `sidecars/cockpit/web/js/render-context.js` | 338 | Context & Prompt Cache tab: 100-dot grid, cache hit analytics, and file sections. |
| `sidecars/cockpit/web/js/render-flow.js` | 446 | Google Flow tab: 10m cross-modal WAF wall, 0-credit image/upscale pools, synced credits, 24h video caps, breakers, and settings. |
| `sidecars/cockpit/web/js/render-header.js` | 94 | Sticky header renderer for active model, pool pill, and 5h/weekly summary. |
| `sidecars/cockpit/web/js/render-image.js` | 455 | Image Quota tab: anchor gauge, rolling schedules, calibration, and settings form. |
| `sidecars/cockpit/web/js/render-overview.js` | 216 | Overview tab summary cards (Image, Pools, Context/Cache, Flow). |
| `sidecars/cockpit/web/js/render-pools.js` | 223 | Model Quota Pools tab: Pool A/B windows, model list, and 7d weekly recovery. |
| `sidecars/cockpit/web/js/state.js` | 55 | Shared reactive UI state, polling interval, and context category constants. |
| `sidecars/cockpit/web/styles.css` | 402 | Cockpit dark theme variables, layout cards, grids, badges, and scrollbar styles. |

### Node Backend (`sidecars/cockpit/`) — 10 files, 2008 lines

| File | Lines | Description |
| --- | --- | --- |
| `sidecars/cockpit/lib/ls_client.mjs` | 214 | Language Server RPC client and cached dual-pool refresh service. |
| `sidecars/cockpit/lib/pools_models.mjs` | 501 | Model role classification rules, quota summary parsing, and pool payload builder. |
| `sidecars/cockpit/lib/pools_recovery.mjs` | 532 | 7-day quota snapshot ledger and weekly rolling recovery estimation. |
| `sidecars/cockpit/lib/reports.mjs` | 207 | Python report runner caching, TTL management, and single-flight locks. |
| `sidecars/cockpit/lib/runner.mjs` | 121 | Subprocess runner, Python discovery, workspace URI decoding, and static asset registration. |
| `sidecars/cockpit/lib/settings.mjs` | 194 | Settings normalization/clamping and JSON stores for settings and quota history. |
| `sidecars/cockpit/main.mjs` | 195 | Sidecar HTTP API server wiring static assets, pools, and Python report services. |
| `sidecars/cockpit/package.json` | 5 | Node ESM package metadata for the cockpit sidecar. |
| `sidecars/cockpit/pools_logic.mjs` | 21 | Backward-compatible facade re-exporting pool model and weekly recovery helpers. |
| `sidecars/cockpit/sidecar.json` | 18 | Sidecar manifest declaring entrypoint and runtime metadata. |

### Python Analyzers (`sidecars/cockpit/`) — 20 files, 6493 lines

| File | Lines | Description |
| --- | --- | --- |
| `sidecars/cockpit/context_parser/__init__.py` | 153 | Subpackage facade re-exporting protobuf, models, collector, and renderer APIs. |
| `sidecars/cockpit/context_parser/collector.py` | 580 | Conversation step token accounting, system file detection, and prompt cache stats. |
| `sidecars/cockpit/context_parser/models.py` | 160 | Model context window catalog, effort levels, and model label helpers. |
| `sidecars/cockpit/context_parser/protobuf.py` | 156 | Lightweight protobuf varint/wire decoder and step type constants. |
| `sidecars/cockpit/context_parser/renderer.py` | 198 | CLI text report and JSON loader for conversation context usage. |
| `sidecars/cockpit/context_report.py` | 90 | CLI entrypoint and compatibility re-exports for `context_parser`. |
| `sidecars/cockpit/flow_quota/__init__.py` | 165 | Subpackage facade re-exporting Flow quota constants, readers, sync, and report APIs. |
| `sidecars/cockpit/flow_quota/constants.py` | 334 | Flow video/image model specs, 4 daily pools, 10m WAF defaults, peak-hour check, and gflow binary lookup. |
| `sidecars/cockpit/flow_quota/readers.py` | 434 | Read-only readers for Flow v2 ledger, `gflow.db`, single-flight/profile locks, and workspace video/image runs. |
| `sidecars/cockpit/flow_quota/report.py` | 957 | Flow 10m WAF wall, 0-credit image daily pools, 24h video rolling buckets, global/per-pool breakers, and report builder. |
| `sidecars/cockpit/flow_quota/sync.py` | 208 | Manual live credit sync via `gflow` CLI with structlog filtering. |
| `sidecars/cockpit/flow_report.py` | 106 | CLI entrypoint and compatibility re-exports for `flow_quota`. |
| `sidecars/cockpit/image_quota/__init__.py` | 136 | Subpackage facade re-exporting image quota constants, scanner, schedules, and calibration. |
| `sidecars/cockpit/image_quota/calibration.py` | 580 | 30-day self-learning 429/safe watermark calibration, ledger, and peak pinning. |
| `sidecars/cockpit/image_quota/constants.py` | 135 | Image quota windows, token pricing constants, regexes, and token formulas. |
| `sidecars/cockpit/image_quota/report.py` | 647 | Workspace ledger linking, anchor selection, and image quota report builder. |
| `sidecars/cockpit/image_quota/scanner.py` | 475 | Read-only SQLite image call scanner, 429 classifier, cache, and archive salvage. |
| `sidecars/cockpit/image_quota/schedules.py` | 510 | 5h/7d rolling recovery schedules and with-reference per-image token averages. |
| `sidecars/cockpit/pool_usage_report.py` | 384 | Read-only 7-day per-pool usage scanner over local conversation DBs. |
| `sidecars/cockpit/quota_report.py` | 85 | CLI entrypoint and compatibility re-exports for `image_quota`. |

### Tests (`tests/`) — 8 files, 2954 lines

| File | Lines | Description |
| --- | --- | --- |
| `tests/test_context_report.py` | 233 | Unit tests for context protobuf parsing and cache hit analytics. |
| `tests/test_flow_report.py` | 510 | Unit tests for Flow rolling buckets, circuit breaker, and credit sync parsing. |
| `tests/test_pool_usage_report.py` | 181 | Unit tests for 7d per-pool local usage aggregation. |
| `tests/test_pools.mjs` | 343 | Node tests for pool model classification and weekly recovery math. |
| `tests/test_project_index.py` | 54 | Drift guard verifying `PROJECT_INDEX.md` stays synchronized with the codebase. |
| `tests/test_quota_report.py` | 849 | Unit tests for image quota scanning, schedules, calibration, and anchors. |
| `tests/test_sidecar_routes.mjs` | 178 | Node tests for sidecar route wiring, settings, and report caching. |
| `tests/test_web_assets.mjs` | 606 | Node tests for web asset collection, cache headers, and settings normalization. |

### Tooling & Metadata — 6 files, 805 lines

| File | Lines | Description |
| --- | --- | --- |
| `.gitignore` | 19 | Repository file. |
| `LICENSE` | 21 | Repository file. |
| `README.md` | 124 | Plugin overview, architecture summary, and installation/usage guide. |
| `assets/logo.svg` | 17 | Plugin vector icon displayed in Antigravity UI. |
| `plugin.json` | 21 | Antigravity plugin manifest describing name, version, and suggested prompts. |
| `scripts/generate_index.py` | 603 | Automated `PROJECT_INDEX.md` generator and `--check` drift verifier. |

**Total Tracked Source & Doc Files**: 56 files, 15161 lines (excluding `PROJECT_INDEX.md`).

## 5. 符号级精确行号索引（自动提取）

### Python Modules (`sidecars/cockpit/**/*.py` & `scripts/*.py`)

- **`scripts/generate_index.py`** (603L)
  - Symbols: `def count_lines` (L251-L253), `def iter_repo_files` (L256-L268), `def extract_http_routes` (L271-L282), `def collect_static_web_routes` (L285-L296), `def classify_group` (L299-L311), `def is_pure_py_facade` (L314-L324), `def extract_python_symbols` (L327-L352), `def extract_js_symbols` (L355-L397), `def render_table` (L400-L408), `def generate_markdown` (L411-L563), `def main` (L566-L599)
  - Constants: `REPO_ROOT` (L21), `INDEX_PATH` (L22), `EXCLUDED_DIRS` (L23), `EXCLUDED_FILES` (L24), `ROUTING_ROWS` (L26), `DATA_FILES` (L174), `READONLY_SOURCES` (L184), `FILE_DESCRIPTIONS` (L193)
- **`sidecars/cockpit/context_parser/__init__.py`** (153L): Subpackage facade / re-exports.
- **`sidecars/cockpit/context_parser/collector.py`** (580L)
  - Symbols: `def step_category` (L38-L47), `def packed_original_step` (L55-L68), `def is_skill_file` (L71-L72), `class Step` (L75-L114), `def _varint` (L117-L125), `def estimate_step_tokens` (L128-L143), `def step_tokens` (L146-L150), `def compaction_tokens` (L153-L163), `def checkpoint_summary_tokens` (L166-L171), `def checkpoint_newer_than_generation` (L174-L190), `def chat_tokens_by_category` (L193-L211), `def detect_system_files` (L214-L238), `def step_timestamp` (L241-L249), `def _summarize_cache_turns` (L252-L276), `def collect_cache_stats` (L279-L392), `def _summarize_steps` (L395-L416), `def collect_step_stats` (L419-L437), `def collect_stats` (L440-L580)
  - Constants: `STEP_GENERIC` (L50), `GENERIC_TYPE` (L51), `ORIGINAL_STEP_TYPE_URL` (L52)
- **`sidecars/cockpit/context_parser/models.py`** (160L)
  - Symbols: `def model_id` (L36-L38), `def model_label` (L41-L56), `def model_name` (L59-L67), `def context_limit` (L70-L76), `def request_budget` (L79-L83), `def main` (L86-L160)
  - Constants: `NATIVE_CONTEXT_WINDOWS` (L24), `EFFORT_LEVELS` (L32), `WORD_LABELS` (L33)
- **`sidecars/cockpit/context_parser/protobuf.py`** (156L)
  - Symbols: `def read_varint` (L9-L19), `class Msg` (L22-L69), `def fmt_tok` (L126-L132), `def fmt_ctx_limit` (L135-L136), `def go_div4` (L139-L141), `def shorten_path` (L144-L156)
  - Constants: `STEP_VIEW_FILE` (L73), `STEP_USER_INPUT` (L74), `STEP_PLANNER_RESPONSE` (L75), `STEP_CHECKPOINT` (L76), `STEP_CODE_ACTION` (L77), `STATUS_DONE` (L79), `STEP_TYPES` (L86)
- **`sidecars/cockpit/context_parser/renderer.py`** (198L)
  - Symbols: `def pct` (L31-L32), `def stats_heading` (L35-L40), `def render_stats` (L43-L83), `def grid_segments` (L86-L94), `def render_grid` (L97-L112), `def checkpoint_view` (L115-L132), `def render_checkpoints` (L135-L145), `def render_file_section` (L148-L154), `def render` (L157-L183), `def load` (L189-L198)
  - Constants: `CATEGORIES` (L11), `GRID_COLORS` (L28)
- **`sidecars/cockpit/context_report.py`** (90L): CLI wrapper / compatibility re-exports.
- **`sidecars/cockpit/flow_quota/__init__.py`** (165L): Subpackage facade / re-exports.
- **`sidecars/cockpit/flow_quota/constants.py`** (334L)
  - Symbols: `def default_gflow_data_dir` (L173-L187), `def default_ledger_path` (L190-L196), `def default_global_lock_path` (L199-L207), `def is_peak_hours` (L210-L213), `def infer_image_quota_pool` (L216-L222), `def find_gflow_bin` (L225-L240), `def parse_iso_ts` (L243-L257), `def safe_float` (L260-L265), `def safe_int` (L268-L272), `def canonical_model_id` (L275-L289), `def model_label` (L292-L303), `def is_pid_alive` (L306-L334)
  - Constants: `REPORT_VERSION` (L14), `CREDITS_CACHE_VERSION` (L15), `CREDITS_HISTORY_MAX` (L16), `WAF_WINDOW_SEC` (L18), `HOUR_SEC` (L19), `DAY_SEC` (L20), `WEEK_SEC` (L21), `MONTH_SEC` (L22), `LOCK_STALE_SEC` (L23), `DEFAULT_MAX_RECAPTCHA_10M` (L26), `MAX_RECAPTCHA_HARD_CEILING` (L27), `PEAK_MAX_RECAPTCHA_10M` (L28), `DEFAULT_MAX_PER_HOUR` (L29), `DEFAULT_MAX_PER_DAY` (L30), `DEFAULT_MAX_DAILY_CREDITS` (L31), `DEFAULT_MIN_REMAINING_CREDITS` (L32), `DEFAULT_MIN_GAP_SEC` (L33), `DEFAULT_IMAGE_MIN_GAP_SEC` (L34), `DEFAULT_IMAGE_PEAK_GAP_SEC` (L35), `DEFAULT_IMAGE_WAF_RECOVERY_GAP_SEC` (L36), `DEFAULT_IMAGE_S2S_GAP_SEC` (L37), `DEFAULT_IMAGE_S2S_PEAK_GAP_SEC` (L38), `DEFAULT_IMAGE_S2S_RECOVERY_GAP_SEC` (L39), `DEFAULT_IMAGE_FINISH_FLOOR_SEC` (L40), `DEFAULT_IMAGE_HEAT_REST_EVERY` (L41), `DEFAULT_PROJECT_ROTATE_TILES` (L42), `DEFAULT_PROJECT_ROTATE_SEGMENTS` (L43), `VIDEO_MODELS` (L48), `IMAGE_MODELS` (L111), `IMAGE_QUOTA_POOLS` (L117), `IMAGE_POOL_BY_ID` (L123), `IMAGE_MODEL_BY_ID` (L125), `IMAGE_MODEL_ALIAS_TO_ID` (L126), `MODEL_ALIAS_TO_ID` (L149), `MODEL_BY_ID` (L150), `VIDEO_ACTIONS` (L158), `IMAGE_ACTIONS` (L159), `ALL_FLOW_ACTIONS` (L160), `RECAPTCHA_ACTIONS` (L162), `LEDGER_COUNTED_ACTIONS` (L166), `WORKSPACE_SKIP_DIRS` (L167)
- **`sidecars/cockpit/flow_quota/readers.py`** (434L)
  - Symbols: `def read_default_profile_from_config` (L31-L40), `def inspect_profile_locks` (L43-L74), `def inspect_single_flight_lock` (L77-L115), `def read_ledger` (L118-L149), `def read_credits_cache` (L152-L176), `def save_credits_cache` (L179-L183), `def _open_sqlite_ro` (L186-L188), `def read_gflow_db` (L191-L282), `def _read_video_project_rotation` (L285-L310), `def scan_workspace_flow_runs` (L313-L434)
- **`sidecars/cockpit/flow_quota/report.py`** (957L)
  - Symbols: `def build_rolling_buckets` (L68-L100), `def compute_breaker` (L103-L123), `def compute_model_breakers` (L126-L148), `def merge_profiles` (L154-L202), `def _is_video_op` (L205-L208), `def _is_image_op` (L211-L214), `def _is_waf_error` (L217-L219), `def _event_tag` (L222-L244), `def build_report` (L247-L902), `def main` (L905-L957)
- **`sidecars/cockpit/flow_quota/sync.py`** (208L)
  - Symbols: `def _resolve_gflow_bin` (L22-L27), `def _is_structlog_dict` (L30-L36), `def parse_gflow_json_stdout` (L39-L66), `def _extract_profiles_from_credits_payload` (L69-L114), `def sync_live_credits` (L117-L208)
- **`sidecars/cockpit/flow_report.py`** (106L): CLI wrapper / compatibility re-exports.
- **`sidecars/cockpit/image_quota/__init__.py`** (136L): Subpackage facade / re-exports.
- **`sidecars/cockpit/image_quota/calibration.py`** (580L)
  - Symbols: `def compute_calibration` (L18-L271), `def _empty_peak_dict` (L274-L291), `def _normalize_peak_dict` (L294-L314), `def _merge_peak_dicts` (L317-L362), `def _normalize_pinned_peak` (L365-L383), `def _empty_calibration_ledger` (L386-L395), `def load_calibration_ledger` (L398-L417), `def save_calibration_ledger` (L420-L423), `def pin_calibration_peak` (L426-L448), `def reset_calibration_ledger` (L451-L459), `def _lock_obs_quality` (L462-L467), `def merge_calibration_with_ledger` (L470-L580)
- **`sidecars/cockpit/image_quota/constants.py`** (135L)
  - Symbols: `def estimate_text_tokens` (L56-L61), `def compute_event_tokens` (L64-L94), `def parse_iso_utc` (L97-L109), `def file_stamp` (L112-L117), `def db_combined_stamp` (L120-L122), `def db_latest_mtime` (L125-L132)
  - Constants: `WINDOW_5H_SEC` (L8), `WINDOW_24H_SEC` (L9), `WINDOW_7D_SEC` (L10), `WINDOW_30D_SEC` (L11), `WEEKLY_LOCK_THRESHOLD_SEC` (L12), `TRUNCATED_LOCK_GAP_SEC` (L13), `DEFAULT_SOFT_CAP` (L15), `DEFAULT_HARD_CAP` (L16), `CACHE_VERSION` (L17), `CALIBRATION_LEDGER_VERSION` (L18), `REQUIRED_CACHED_EVENT_FIELDS` (L19), `REF_IMAGE_TOKENS` (L34), `OUTPUT_1K_IMAGE_TOKENS` (L35), `THINKING_MINIMAL_TOKENS` (L36), `PRICE_INPUT_PER_M` (L37), `PRICE_THINKING_PER_M` (L38), `PRICE_OUTPUT_IMG_PER_M` (L39), `BILLABLE_KINDS` (L41), `RE_RESET_TS` (L43), `RE_RESET_DELAY` (L44), `RE_RETRY_DELAY` (L45), `RE_ERR_MODEL` (L46), `RE_ERR_MSG` (L47), `RE_CJK` (L48), `SKIP_DIRS` (L50), `KNOWN_EVENT_KINDS` (L135)
- **`sidecars/cockpit/image_quota/report.py`** (647L)
  - Symbols: `def scan_workspace_ledgers` (L37-L110), `def compute_anchor` (L113-L404), `def build_report` (L407-L604), `def main` (L607-L647)
- **`sidecars/cockpit/image_quota/scanner.py`** (475L)
  - Symbols: `def classify_error` (L36-L130), `def extract_tool_image_info` (L133-L184), `def scan_single_db` (L187-L269), `def _is_valid_cached_event` (L272-L280), `def _is_valid_cached_events` (L283-L284), `def _salvage_archived_event` (L287-L324), `def _load_cache_raw` (L327-L337), `def load_cache` (L340-L344), `def _cleanup_stale_cache_temps` (L347-L359), `def save_cache` (L362-L388), `def scan_conversations` (L391-L475)
  - Constants: `_COCKPIT_DIR` (L12)
- **`sidecars/cockpit/image_quota/schedules.py`** (510L)
  - Symbols: `def summarize_bucket` (L18-L60), `def compute_per_image_avg` (L63-L217), `def build_rolling_schedule` (L220-L352), `def build_weekly_rolling_schedule` (L355-L510)
- **`sidecars/cockpit/pool_usage_report.py`** (384L)
  - Symbols: `def file_stamp` (L40-L45), `def db_combined_stamp` (L48-L50), `def db_latest_mtime` (L53-L60), `def pool_key_for_usage` (L63-L73), `def model_tier_multiplier` (L76-L86), `def _step_ts` (L89-L94), `def scan_single_db_usage` (L97-L167), `def _is_valid_cached_buckets` (L170-L178), `def load_cache` (L181-L190), `def _cleanup_stale_temps` (L193-L205), `def save_cache` (L208-L234), `def build_pool_usage_report` (L237-L367), `def main` (L370-L380)
  - Constants: `HERE` (L21), `WINDOW_5H_SEC` (L27), `WINDOW_7D_SEC` (L28), `LOOKBACK_MARGIN_SEC` (L29), `CACHE_VERSION` (L30), `PROVIDER_GOOGLE` (L32), `PROVIDER_ANTHROPIC` (L33), `RE_LIGHT_TIER` (L35), `RE_MID_TIER` (L36), `RE_HEAVY_TIER` (L37)
- **`sidecars/cockpit/quota_report.py`** (85L): CLI wrapper / compatibility re-exports.

### Node & Frontend Modules (`sidecars/cockpit/**/*.mjs` & `sidecars/cockpit/**/*.js`)

- **`sidecars/cockpit/lib/ls_client.mjs`** (214L)
  - Functions: `export function lsRequest` (L16), `export function createPoolsService` (L58)
- **`sidecars/cockpit/lib/pools_models.mjs`** (501L)
  - Functions: `export function classifyModelRole` (L21), `export function poolMetaForProvider` (L28), `export function baseFamilyName` (L60), `export function pctToHealth` (L65), `function clampFraction` (L72), `function parseIsoSec` (L77), `function normalizeWindowKind` (L83), `function windowMeta` (L99), `function makeWindowEntry` (L105), `function poolKeyForSummaryGroup` (L142), `export function parseQuotaSummary` (L177), `export function buildPoolsPayload` (L216), `function summarizePoolWindows` (L427), `export function resolveActivePool` (L441)
  - Exports: `export const ROLE_RULES` (L5)
- **`sidecars/cockpit/lib/pools_recovery.mjs`** (532L)
  - Functions: `export function createEmptyQuotaHistory` (L9), `export function normalizeQuotaHistory` (L13), `export function recordQuotaSnapshot` (L40), `function localDayInfo` (L106), `function roundPct` (L118), `export function buildWeeklyRecovery` (L122), `export function enrichPoolsWithWeeklyRecovery` (L494)
  - Exports: `export const WINDOW_7D_SEC` (L3)
- **`sidecars/cockpit/lib/reports.mjs`** (207L)
  - Functions: `export function createReportsService` (L9)
- **`sidecars/cockpit/lib/runner.mjs`** (121L)
  - Functions: `export function runCommand` (L21), `export async function findPython` (L38), `export function workspacePath` (L54), `export function fileStamp` (L62), `export function collectWebAssets` (L71), `export function registerWebAssets` (L104)
  - Exports: `export const CONTENT_TYPES` (L12)
- **`sidecars/cockpit/lib/settings.mjs`** (194L)
  - Functions: `export function clampInt` (L29), `export function migrateLegacyFlowDefaults` (L35), `export function normalizeSettings` (L56), `export function createSettingsStore` (L124), `export function createQuotaHistoryStore` (L158)
  - Exports: `export const FLOW_POLICY_VERSION` (L12), `export const DEFAULT_SETTINGS` (L14)
- **`sidecars/cockpit/main.mjs`** (195L): Sidecar bootstrap & HTTP API routes: `GET /api/dashboard` (L87), `POST /api/settings` (L113), `POST /api/flow/sync-credits` (L135), `POST /api/calibration/pin` (L153), `POST /api/calibration/reset` (L173), `GET /api/report` (L185)
- **`sidecars/cockpit/pools_logic.mjs`** (21L): Backward-compatible re-export facade.
- **`sidecars/cockpit/web/js/actions.js`** (209L)
  - Functions: `function setActionsCallbacks` (L8), `async function copyText` (L12), `async function saveSettings` (L32), `async function saveFlowSettings` (L63), `async function syncFlowCredits` (L88), `async function pinPeak` (L117), `async function resetCalibration` (L146), `async function refresh` (L169)
- **`sidecars/cockpit/web/js/app.js`** (251L)
  - Functions: `function render` (L11), `function setTab` (L87)
- **`sidecars/cockpit/web/js/formatters.js`** (173L)
  - Functions: `function fmtTok` (L11), `function fmtClock` (L18), `function fmtHM` (L24), `function fmtUsd` (L31), `function fmtDateTime` (L38), `function fmtMD` (L45), `function fmtDuration` (L52), `function cdSpan` (L65), `function fmtElapsed` (L70), `function elapsedTextWithHint` (L82), `function elapsedSpan` (L90), `function tickCountdowns` (L97), `function healthCls` (L115), `function barFillColor` (L121), `function gridCells` (L127), `function splitPath` (L141), `function fmtCredits` (L146)
  - Exports: `const nowSec` (L6), `const esc` (L7)
- **`sidecars/cockpit/web/js/render-context.js`** (338L)
  - Functions: `function fmtStepInt` (L8), `function cacheStatusMeta` (L12), `function cacheTurnStatusLabel` (L30), `function renderCacheOverviewStrip` (L40), `function renderCacheCard` (L68), `function renderStepsOverviewStrip` (L141), `function renderStepsCard` (L176), `function renderFileList` (L226), `function renderContext` (L238)
- **`sidecars/cockpit/web/js/render-flow.js`** (446L)
  - Functions: `function flowStatusMeta` (L8), `function renderFlowOverviewCard` (L19), `function renderFlowSchedule` (L136), `function renderFlow` (L153)
- **`sidecars/cockpit/web/js/render-header.js`** (94L)
  - Functions: `function updateHeader` (L8)
- **`sidecars/cockpit/web/js/render-image.js`** (455L)
  - Functions: `function imgStatusMeta` (L8), `function eventTagInfo` (L35), `function renderImage` (L44)
- **`sidecars/cockpit/web/js/render-overview.js`** (216L)
  - Functions: `function renderOverview` (L11)
- **`sidecars/cockpit/web/js/render-pools.js`** (223L)
  - Functions: `function weeklyRecSourceMeta` (L8), `function renderPoolWeeklyRecoveryOverview` (L17), `function renderPoolWeeklyRecoveryDetail` (L42), `function poolWindowsList` (L93), `function winResetHtml` (L113), `function renderPools` (L127)
- **`sidecars/cockpit/web/js/state.js`** (55L)
  - Exports: `const POLL_MS` (L4), `const SEG_COLORS` (L5), `const CAT_COLOR` (L6), `const CAT_LABEL` (L14), `const CTX_GROUPS` (L22), `const state` (L28), `const $` (L44)
