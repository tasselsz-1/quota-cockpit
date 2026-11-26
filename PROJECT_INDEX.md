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
| Context & Prompt Cache tab UI | `sidecars/cockpit/web/js/render-context.js` | `renderContext`, `renderCacheCard`, `renderCacheOverviewStrip` |
| Google Flow Video tab & overview card UI | `sidecars/cockpit/web/js/render-flow.js`, `sidecars/cockpit/web/js/actions.js` | `renderFlow`, `renderFlowOverviewCard`, `saveFlowSettings`, `syncFlowCredits` |
| CSS / theme / scrollbar | `sidecars/cockpit/web/styles.css` | `:root` tokens, `.card`, `.pill`, `.tabs`, scrollbar rules |
| Formatters / countdown tickers | `sidecars/cockpit/web/js/formatters.js` | `fmtTok`, `fmtDuration`, `cdSpan`, `tickCountdowns`, `fmtCredits` |
| Shared frontend state / tab routing / poll focus guard | `sidecars/cockpit/web/js/state.js`, `sidecars/cockpit/web/js/app.js` | `state`, `POLL_MS`, `render`, `setTab` |
| HTTP API routes & static asset wiring | `sidecars/cockpit/main.mjs`, `sidecars/cockpit/lib/runner.mjs` | `app.api(...)`, `registerWebAssets`, `collectWebAssets` |
| Settings defaults, clamping & quota history store | `sidecars/cockpit/lib/settings.mjs` | `DEFAULT_SETTINGS`, `clampInt`, `normalizeSettings`, `createSettingsStore`, `createQuotaHistoryStore` |
| Antigravity LS RPC & pool refresh cache | `sidecars/cockpit/lib/ls_client.mjs` | `lsRequest`, `createPoolsService` |
| Model role/tier rules (`ROLE_RULES`) & pool aggregation | `sidecars/cockpit/lib/pools_models.mjs` | `ROLE_RULES`, `classifyModelRole`, `parseQuotaSummary`, `buildPoolsPayload`, `resolveActivePool` |
| 7d weekly rolling recovery & snapshot ledger | `sidecars/cockpit/lib/pools_recovery.mjs` | `WINDOW_7D_SEC`, `recordQuotaSnapshot`, `buildWeeklyRecovery`, `enrichPoolsWithWeeklyRecovery` |
| Report runner caching & single-flight locks | `sidecars/cockpit/lib/reports.mjs` | `createReportsService` |
| Image quota constants & official token formula | `sidecars/cockpit/image_quota/constants.py` | `WINDOW_5H_SEC`, `DEFAULT_SOFT_CAP`, `DEFAULT_HARD_CAP`, `compute_event_tokens` |
| Image DB scanner, 429 classification & archive salvage | `sidecars/cockpit/image_quota/scanner.py` | `classify_error`, `extract_tool_image_info`, `scan_single_db`, `scan_conversations`, `_salvage_archived_event` |
| Image 5h/7d rolling schedules & with-ref avg | `sidecars/cockpit/image_quota/schedules.py` | `compute_per_image_avg`, `build_rolling_schedule`, `build_weekly_rolling_schedule` |
| Image 30d self-learning watermark, ledger & peak pinning | `sidecars/cockpit/image_quota/calibration.py` | `compute_calibration`, `load_calibration_ledger`, `pin_calibration_peak`, `merge_calibration_with_ledger` |
| Image anchor selection & workspace ledger linking | `sidecars/cockpit/image_quota/report.py` | `scan_workspace_ledgers`, `compute_anchor`, `build_report` |
| Flow video models, pricing & gflow binary discovery | `sidecars/cockpit/flow_quota/constants.py` | `VIDEO_MODELS`, `MODEL_BY_ID`, `find_gflow_bin`, `canonical_model_id` |
| Flow read-only ledger, gflow.db, locks & workspace runs | `sidecars/cockpit/flow_quota/readers.py` | `read_ledger`, `read_gflow_db`, `inspect_profile_locks`, `scan_workspace_flow_runs` |
| Flow manual live credit sync & structlog stripping | `sidecars/cockpit/flow_quota/sync.py` | `parse_gflow_json_stdout`, `sync_live_credits` |
| Flow 1h/24h anti-throttle rolling buckets & circuit breaker | `sidecars/cockpit/flow_quota/report.py` | `build_rolling_buckets`, `compute_breaker`, `merge_profiles`, `build_report` |
| Conversation protobuf decoding & cache hit analytics | `sidecars/cockpit/context_parser/protobuf.py`, `sidecars/cockpit/context_parser/collector.py` | `Msg`, `read_varint`, `collect_cache_stats`, `collect_stats` |
| Context CLI rendering & model window table | `sidecars/cockpit/context_parser/renderer.py`, `sidecars/cockpit/context_parser/models.py` | `render`, `load`, `NATIVE_CONTEXT_WINDOWS`, `context_limit` |
| Local 7d per-pool usage scanner | `sidecars/cockpit/pool_usage_report.py` | `pool_key_for_usage`, `scan_single_db_usage`, `build_pool_usage_report` |
| Index generator & drift guard | `scripts/generate_index.py`, `tests/test_project_index.py` | `generate_markdown`, `main`, `TestProjectIndex` |

## 2. HTTP API & 静态路由速查

| Method | Route | Definition | Responsibility |
| --- | --- | --- | --- |
| `GET` | `/api/dashboard` | `sidecars/cockpit/main.mjs` (L85-L109) | Aggregates pools, context, imageQuota, flowQuota, and active pool metadata. |
| `POST` | `/api/settings` | `sidecars/cockpit/main.mjs` (L111-L131) | Persists normalized settings, invalidates image/flow caches, and refreshes both reports. |
| `POST` | `/api/flow/sync-credits` | `sidecars/cockpit/main.mjs` (L133-L149) | Runs explicit manual Flow live credit sync (`--sync-credits`) when profile is unlocked. |
| `POST` | `/api/calibration/pin` | `sidecars/cockpit/main.mjs` (L151-L169) | Pins or unpins the empirical 5h image safe peak (`--pin-peak` / `--unpin-peak`). |
| `POST` | `/api/calibration/reset` | `sidecars/cockpit/main.mjs` (L171-L181) | Deletes `image-calibration.json`, invalidates cache, and rebuilds image quota report. |
| `GET` | `/api/report` | `sidecars/cockpit/main.mjs` (L183-L190) | Refreshes pool state (cached) and returns context report for a single conversation. |

**Static Web Routes** (`sidecars/cockpit/lib/runner.mjs` → `registerWebAssets`, all served with `Cache-Control: no-store`, 13 routes): `/`, `/web/index.html`, `/web/js/actions.js`, `/web/js/app.js`, `/web/js/formatters.js`, `/web/js/render-context.js`, `/web/js/render-flow.js`, `/web/js/render-header.js`, `/web/js/render-image.js`, `/web/js/render-overview.js`, `/web/js/render-pools.js`, `/web/js/state.js`, `/web/styles.css`

## 3. 持久化数据与外部只读数据源

### 持久化 JSON 文件 (`ANTIGRAVITY_EXECUTABLE_DATA_DIR`)

| File | Location Env | Purpose |
| --- | --- | --- |
| `settings.json` | `ANTIGRAVITY_EXECUTABLE_DATA_DIR` | User soft/hard image caps, count/anchor mode, and Flow 1h/24h/credit/gap settings (`lib/settings.mjs`). |
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
| `gflow.db` | Local `gflow-cli` SQLite database read via `?mode=ro` for profiles, video operations, model counts, and WAF/error telemetry. |
| `gflow_traffic_ledger.json` | Local Flow traffic ledger (`GFLOW_TRAFFIC_LEDGER` or `~/.gemini/gflow_traffic_ledger.json`) tracking 1h/24h/credit burn. |
| `api_call_ledger.json` | Workspace image generation call ledger linked by `image_quota/report.py`. |
| `flow_run_state.json` | Workspace Flow runner state files discovered by `flow_quota/readers.py`. |

## 4. 分层模块目录总览（自动统计行数）

### Frontend (`sidecars/cockpit/web/`) — 12 files, 2551 lines

| File | Lines | Description |
| --- | --- | --- |
| `sidecars/cockpit/web/index.html` | 30 | Top header, 5-tab navigation shell, and module script entry. |
| `sidecars/cockpit/web/js/actions.js` | 195 | Frontend API actions for settings, Flow sync, peak pinning, calibration reset, and refresh. |
| `sidecars/cockpit/web/js/app.js` | 238 | DOM diff renderer, tab router, event delegation, and polling loop. |
| `sidecars/cockpit/web/js/formatters.js` | 147 | Token/duration/currency/credit formatters, countdown tags, and grid cell helpers. |
| `sidecars/cockpit/web/js/render-context.js` | 231 | Context & Prompt Cache tab: 100-dot grid, cache hit analytics, and file sections. |
| `sidecars/cockpit/web/js/render-flow.js` | 322 | Google Flow tab: synced credits, 1h/24h rolling caps, breaker status, and settings. |
| `sidecars/cockpit/web/js/render-header.js` | 86 | Sticky header renderer for active model, pool pill, and 5h/weekly summary. |
| `sidecars/cockpit/web/js/render-image.js` | 446 | Image Quota tab: anchor gauge, rolling schedules, calibration, and settings form. |
| `sidecars/cockpit/web/js/render-overview.js` | 211 | Overview tab summary cards (Image, Pools, Context/Cache, Flow). |
| `sidecars/cockpit/web/js/render-pools.js` | 210 | Model Quota Pools tab: Pool A/B windows, model list, and 7d weekly recovery. |
| `sidecars/cockpit/web/js/state.js` | 41 | Shared reactive UI state, polling interval, and context category constants. |
| `sidecars/cockpit/web/styles.css` | 394 | Cockpit dark theme variables, layout cards, grids, badges, and scrollbar styles. |

### Node Backend (`sidecars/cockpit/`) — 10 files, 1967 lines

| File | Lines | Description |
| --- | --- | --- |
| `sidecars/cockpit/lib/ls_client.mjs` | 214 | Language Server RPC client and cached dual-pool refresh service. |
| `sidecars/cockpit/lib/pools_models.mjs` | 501 | Model role classification rules, quota summary parsing, and pool payload builder. |
| `sidecars/cockpit/lib/pools_recovery.mjs` | 532 | 7-day quota snapshot ledger and weekly rolling recovery estimation. |
| `sidecars/cockpit/lib/reports.mjs` | 206 | Python report runner caching, TTL management, and single-flight locks. |
| `sidecars/cockpit/lib/runner.mjs` | 118 | Subprocess runner, Python discovery, workspace URI decoding, and static asset registration. |
| `sidecars/cockpit/lib/settings.mjs` | 159 | Settings normalization/clamping and JSON stores for settings and quota history. |
| `sidecars/cockpit/main.mjs` | 193 | Sidecar HTTP API server wiring static assets, pools, and Python report services. |
| `sidecars/cockpit/package.json` | 5 | Node ESM package metadata for the cockpit sidecar. |
| `sidecars/cockpit/pools_logic.mjs` | 21 | Backward-compatible facade re-exporting pool model and weekly recovery helpers. |
| `sidecars/cockpit/sidecar.json` | 18 | Sidecar manifest declaring entrypoint and runtime metadata. |

### Python Analyzers (`sidecars/cockpit/`) — 20 files, 5729 lines

| File | Lines | Description |
| --- | --- | --- |
| `sidecars/cockpit/context_parser/__init__.py` | 149 | Subpackage facade re-exporting protobuf, models, collector, and renderer APIs. |
| `sidecars/cockpit/context_parser/collector.py` | 534 | Conversation step token accounting, system file detection, and prompt cache stats. |
| `sidecars/cockpit/context_parser/models.py` | 159 | Model context window catalog, effort levels, and model label helpers. |
| `sidecars/cockpit/context_parser/protobuf.py` | 156 | Lightweight protobuf varint/wire decoder and step type constants. |
| `sidecars/cockpit/context_parser/renderer.py` | 183 | CLI text report and JSON loader for conversation context usage. |
| `sidecars/cockpit/context_report.py` | 88 | CLI entrypoint and compatibility re-exports for `context_parser`. |
| `sidecars/cockpit/flow_quota/__init__.py` | 105 | Subpackage facade re-exporting Flow quota constants, readers, sync, and report APIs. |
| `sidecars/cockpit/flow_quota/constants.py` | 236 | Flow video model specs, credit pricing, defaults, and gflow binary lookup. |
| `sidecars/cockpit/flow_quota/readers.py` | 287 | Read-only readers for Flow ledger, `gflow.db`, profile locks, and workspace runs. |
| `sidecars/cockpit/flow_quota/report.py` | 596 | Flow 1h/24h rolling buckets, circuit breaker computation, and report builder. |
| `sidecars/cockpit/flow_quota/sync.py` | 208 | Manual live credit sync via `gflow` CLI with structlog filtering. |
| `sidecars/cockpit/flow_report.py` | 76 | CLI entrypoint and compatibility re-exports for `flow_quota`. |
| `sidecars/cockpit/image_quota/__init__.py` | 136 | Subpackage facade re-exporting image quota constants, scanner, schedules, and calibration. |
| `sidecars/cockpit/image_quota/calibration.py` | 580 | 30-day self-learning 429/safe watermark calibration, ledger, and peak pinning. |
| `sidecars/cockpit/image_quota/constants.py` | 135 | Image quota windows, token pricing constants, regexes, and token formulas. |
| `sidecars/cockpit/image_quota/report.py` | 647 | Workspace ledger linking, anchor selection, and image quota report builder. |
| `sidecars/cockpit/image_quota/scanner.py` | 475 | Read-only SQLite image call scanner, 429 classifier, cache, and archive salvage. |
| `sidecars/cockpit/image_quota/schedules.py` | 510 | 5h/7d rolling recovery schedules and with-reference per-image token averages. |
| `sidecars/cockpit/pool_usage_report.py` | 384 | Read-only 7-day per-pool usage scanner over local conversation DBs. |
| `sidecars/cockpit/quota_report.py` | 85 | CLI entrypoint and compatibility re-exports for `image_quota`. |

### Tests (`tests/`) — 8 files, 2242 lines

| File | Lines | Description |
| --- | --- | --- |
| `tests/test_context_report.py` | 163 | Unit tests for context protobuf parsing and cache hit analytics. |
| `tests/test_flow_report.py` | 285 | Unit tests for Flow rolling buckets, circuit breaker, and credit sync parsing. |
| `tests/test_pool_usage_report.py` | 181 | Unit tests for 7d per-pool local usage aggregation. |
| `tests/test_pools.mjs` | 343 | Node tests for pool model classification and weekly recovery math. |
| `tests/test_project_index.py` | 54 | Drift guard verifying `PROJECT_INDEX.md` stays synchronized with the codebase. |
| `tests/test_quota_report.py` | 849 | Unit tests for image quota scanning, schedules, calibration, and anchors. |
| `tests/test_sidecar_routes.mjs` | 132 | Node tests for sidecar route wiring, settings, and report caching. |
| `tests/test_web_assets.mjs` | 235 | Node tests for web asset collection, cache headers, and settings normalization. |

### Tooling & Metadata — 6 files, 788 lines

| File | Lines | Description |
| --- | --- | --- |
| `.gitignore` | 19 | Repository file. |
| `LICENSE` | 21 | Repository file. |
| `README.md` | 123 | Plugin overview, architecture summary, and installation/usage guide. |
| `assets/logo.svg` | 17 | Plugin vector icon displayed in Antigravity UI. |
| `plugin.json` | 21 | Antigravity plugin manifest describing name, version, and suggested prompts. |
| `scripts/generate_index.py` | 587 | Automated `PROJECT_INDEX.md` generator and `--check` drift verifier. |

**Total Tracked Source & Doc Files**: 56 files, 13277 lines (excluding `PROJECT_INDEX.md`).

## 5. 符号级精确行号索引（自动提取）

### Python Modules (`sidecars/cockpit/**/*.py` & `scripts/*.py`)

- **`scripts/generate_index.py`** (587L)
  - Symbols: `def count_lines` (L250-L252), `def iter_repo_files` (L255-L267), `def extract_http_routes` (L270-L281), `def collect_static_web_routes` (L284-L295), `def classify_group` (L298-L310), `def is_pure_py_facade` (L313-L323), `def extract_python_symbols` (L326-L351), `def extract_js_symbols` (L354-L381), `def render_table` (L384-L392), `def generate_markdown` (L395-L547), `def main` (L550-L583)
  - Constants: `REPO_ROOT` (L21), `INDEX_PATH` (L22), `EXCLUDED_DIRS` (L23), `EXCLUDED_FILES` (L24), `ROUTING_ROWS` (L26), `DATA_FILES` (L174), `READONLY_SOURCES` (L184), `FILE_DESCRIPTIONS` (L192)
- **`sidecars/cockpit/context_parser/__init__.py`** (149L): Subpackage facade / re-exports.
- **`sidecars/cockpit/context_parser/collector.py`** (534L)
  - Symbols: `def step_category` (L38-L47), `def packed_original_step` (L55-L68), `def is_skill_file` (L71-L72), `class Step` (L75-L114), `def _varint` (L117-L125), `def estimate_step_tokens` (L128-L143), `def step_tokens` (L146-L150), `def compaction_tokens` (L153-L163), `def checkpoint_summary_tokens` (L166-L171), `def checkpoint_newer_than_generation` (L174-L190), `def chat_tokens_by_category` (L193-L211), `def detect_system_files` (L214-L238), `def step_timestamp` (L241-L249), `def _summarize_cache_turns` (L252-L276), `def collect_cache_stats` (L279-L392), `def collect_stats` (L395-L534)
  - Constants: `STEP_GENERIC` (L50), `GENERIC_TYPE` (L51), `ORIGINAL_STEP_TYPE_URL` (L52)
- **`sidecars/cockpit/context_parser/models.py`** (159L)
  - Symbols: `def model_id` (L36-L38), `def model_label` (L41-L56), `def model_name` (L59-L67), `def context_limit` (L70-L76), `def request_budget` (L79-L83), `def main` (L86-L159)
  - Constants: `NATIVE_CONTEXT_WINDOWS` (L24), `EFFORT_LEVELS` (L32), `WORD_LABELS` (L33)
- **`sidecars/cockpit/context_parser/protobuf.py`** (156L)
  - Symbols: `def read_varint` (L9-L19), `class Msg` (L22-L69), `def fmt_tok` (L126-L132), `def fmt_ctx_limit` (L135-L136), `def go_div4` (L139-L141), `def shorten_path` (L144-L156)
  - Constants: `STEP_VIEW_FILE` (L73), `STEP_USER_INPUT` (L74), `STEP_PLANNER_RESPONSE` (L75), `STEP_CHECKPOINT` (L76), `STEP_CODE_ACTION` (L77), `STATUS_DONE` (L79), `STEP_TYPES` (L86)
- **`sidecars/cockpit/context_parser/renderer.py`** (183L)
  - Symbols: `def pct` (L31-L32), `def stats_heading` (L35-L40), `def render_stats` (L43-L68), `def grid_segments` (L71-L79), `def render_grid` (L82-L97), `def checkpoint_view` (L100-L117), `def render_checkpoints` (L120-L130), `def render_file_section` (L133-L139), `def render` (L142-L168), `def load` (L174-L183)
  - Constants: `CATEGORIES` (L11), `GRID_COLORS` (L28)
- **`sidecars/cockpit/context_report.py`** (88L): CLI wrapper / compatibility re-exports.
- **`sidecars/cockpit/flow_quota/__init__.py`** (105L): Subpackage facade / re-exports.
- **`sidecars/cockpit/flow_quota/constants.py`** (236L)
  - Symbols: `def default_gflow_data_dir` (L112-L126), `def default_ledger_path` (L129-L135), `def find_gflow_bin` (L138-L153), `def parse_iso_ts` (L156-L170), `def safe_float` (L173-L178), `def safe_int` (L181-L185), `def canonical_model_id` (L188-L198), `def model_label` (L201-L205), `def is_pid_alive` (L208-L236)
  - Constants: `REPORT_VERSION` (L14), `CREDITS_CACHE_VERSION` (L15), `CREDITS_HISTORY_MAX` (L16), `HOUR_SEC` (L18), `DAY_SEC` (L19), `WEEK_SEC` (L20), `MONTH_SEC` (L21), `LOCK_STALE_SEC` (L22), `DEFAULT_MAX_PER_HOUR` (L24), `DEFAULT_MAX_PER_DAY` (L25), `DEFAULT_MAX_DAILY_CREDITS` (L26), `DEFAULT_MIN_REMAINING_CREDITS` (L27), `DEFAULT_MIN_GAP_SEC` (L28), `VIDEO_MODELS` (L33), `MODEL_ALIAS_TO_ID` (L96), `MODEL_BY_ID` (L97), `LEDGER_COUNTED_ACTIONS` (L105), `WORKSPACE_SKIP_DIRS` (L106)
- **`sidecars/cockpit/flow_quota/readers.py`** (287L)
  - Symbols: `def read_default_profile_from_config` (L29-L38), `def inspect_profile_locks` (L41-L72), `def read_ledger` (L75-L91), `def read_credits_cache` (L94-L118), `def save_credits_cache` (L121-L125), `def _open_sqlite_ro` (L128-L130), `def read_gflow_db` (L133-L224), `def scan_workspace_flow_runs` (L227-L287)
- **`sidecars/cockpit/flow_quota/report.py`** (596L)
  - Symbols: `def build_rolling_buckets` (L48-L78), `def compute_breaker` (L81-L101), `def merge_profiles` (L107-L155), `def _is_video_op` (L158-L161), `def _is_waf_error` (L164-L166), `def build_report` (L169-L543), `def main` (L546-L596)
- **`sidecars/cockpit/flow_quota/sync.py`** (208L)
  - Symbols: `def _resolve_gflow_bin` (L22-L27), `def _is_structlog_dict` (L30-L36), `def parse_gflow_json_stdout` (L39-L66), `def _extract_profiles_from_credits_payload` (L69-L114), `def sync_live_credits` (L117-L208)
- **`sidecars/cockpit/flow_report.py`** (76L): CLI wrapper / compatibility re-exports.
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
- **`sidecars/cockpit/lib/reports.mjs`** (206L)
  - Functions: `export function createReportsService` (L9)
- **`sidecars/cockpit/lib/runner.mjs`** (118L)
  - Functions: `export function runCommand` (L21), `export async function findPython` (L38), `export function workspacePath` (L54), `export function fileStamp` (L62), `export function collectWebAssets` (L71), `export function registerWebAssets` (L104)
  - Exports: `export const CONTENT_TYPES` (L12)
- **`sidecars/cockpit/lib/settings.mjs`** (159L)
  - Functions: `export function clampInt` (L25), `export function normalizeSettings` (L31), `export function createSettingsStore` (L90), `export function createQuotaHistoryStore` (L123)
  - Exports: `export const DEFAULT_SETTINGS` (L12)
- **`sidecars/cockpit/main.mjs`** (193L): Sidecar bootstrap & HTTP API routes: `GET /api/dashboard` (L85), `POST /api/settings` (L111), `POST /api/flow/sync-credits` (L133), `POST /api/calibration/pin` (L151), `POST /api/calibration/reset` (L171), `GET /api/report` (L183)
- **`sidecars/cockpit/pools_logic.mjs`** (21L): Backward-compatible re-export facade.
- **`sidecars/cockpit/web/js/actions.js`** (195L)
  - Functions: `export function setActionsCallbacks` (L6), `export async function copyText` (L10), `export async function saveSettings` (L30), `export async function saveFlowSettings` (L61), `export async function syncFlowCredits` (L86), `export async function pinPeak` (L115), `export async function resetCalibration` (L144), `export async function refresh` (L167)
- **`sidecars/cockpit/web/js/app.js`** (238L)
  - Functions: `export function render` (L14), `export function setTab` (L85)
- **`sidecars/cockpit/web/js/formatters.js`** (147L)
  - Functions: `export function fmtTok` (L8), `export function fmtClock` (L15), `export function fmtHM` (L21), `export function fmtUsd` (L28), `export function fmtDateTime` (L35), `export function fmtMD` (L42), `export function fmtDuration` (L49), `export function cdSpan` (L62), `export function fmtElapsed` (L67), `export function elapsedTextWithHint` (L79), `export function elapsedSpan` (L87), `export function tickCountdowns` (L94), `export function healthCls` (L112), `export function barFillColor` (L118), `export function gridCells` (L124), `export function splitPath` (L138), `export function fmtCredits` (L143)
  - Exports: `export const nowSec` (L3), `export const esc` (L4)
- **`sidecars/cockpit/web/js/render-context.js`** (231L)
  - Functions: `export function cacheStatusMeta` (L6), `export function cacheTurnStatusLabel` (L24), `export function renderCacheOverviewStrip` (L34), `export function renderCacheCard` (L62), `export function renderFileList` (L135), `export function renderContext` (L147)
- **`sidecars/cockpit/web/js/render-flow.js`** (322L)
  - Functions: `export function flowStatusMeta` (L6), `export function renderFlowOverviewCard` (L17), `export function renderFlowSchedule` (L98), `export function renderFlow` (L115)
- **`sidecars/cockpit/web/js/render-header.js`** (86L)
  - Functions: `export function updateHeader` (L5)
- **`sidecars/cockpit/web/js/render-image.js`** (446L)
  - Functions: `export function imgStatusMeta` (L6), `export function eventTagInfo` (L33), `export function renderImage` (L42)
- **`sidecars/cockpit/web/js/render-overview.js`** (211L)
  - Functions: `export function renderOverview` (L12)
- **`sidecars/cockpit/web/js/render-pools.js`** (210L)
  - Functions: `export function weeklyRecSourceMeta` (L5), `export function renderPoolWeeklyRecoveryOverview` (L14), `export function renderPoolWeeklyRecoveryDetail` (L39), `export function poolWindowsList` (L90), `export function winResetHtml` (L110), `export function renderPools` (L124)
- **`sidecars/cockpit/web/js/state.js`** (41L)
  - Exports: `export const POLL_MS` (L1), `export const SEG_COLORS` (L2), `export const CAT_COLOR` (L3), `export const CAT_LABEL` (L11), `export const CTX_GROUPS` (L19), `export const state` (L25)
