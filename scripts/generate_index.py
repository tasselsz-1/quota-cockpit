#!/usr/bin/env python3
"""Generate and verify PROJECT_INDEX.md for quota-cockpit.

Pure Python stdlib script. Extracts HTTP routes, layered module line counts,
and symbol-level line ranges across Python, MJS, and frontend JS modules.

Usage:
    python scripts/generate_index.py          # Regenerate PROJECT_INDEX.md
    python scripts/generate_index.py --check  # Exit 0 if up to date, else 1
"""

from __future__ import annotations

import argparse
import ast
import difflib
from pathlib import Path
import re
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = REPO_ROOT / "PROJECT_INDEX.md"
EXCLUDED_DIRS = {".git", "__pycache__", ".data", "node_modules"}
EXCLUDED_FILES = {"PROJECT_INDEX.md"}

ROUTING_ROWS: list[tuple[str, str, str]] = [
    (
        "Top Tab order / HTML shell",
        "`sidecars/cockpit/web/index.html`",
        "`.tabs`, `data-tab=\"overview|image|pools|context|flow\"`",
    ),
    (
        "Overview card order & summary",
        "`sidecars/cockpit/web/js/render-overview.js`",
        "`renderOverview`",
    ),
    (
        "Sticky top header",
        "`sidecars/cockpit/web/js/render-header.js`",
        "`updateHeader`",
    ),
    (
        "Image Quota tab / pin peak / settings UI",
        "`sidecars/cockpit/web/js/render-image.js`, `sidecars/cockpit/web/js/actions.js`",
        "`renderImage`, `saveSettings`, `pinPeak`, `resetCalibration`",
    ),
    (
        "Model Quota Pools tab / weekly recovery UI",
        "`sidecars/cockpit/web/js/render-pools.js`",
        "`renderPools`, `renderPoolWeeklyRecoveryOverview`, `renderPoolWeeklyRecoveryDetail`",
    ),
    (
        "Context & Prompt Cache tab UI",
        "`sidecars/cockpit/web/js/render-context.js`",
        "`renderContext`, `renderCacheCard`, `renderCacheOverviewStrip`",
    ),
    (
        "Google Flow Video tab & overview card UI",
        "`sidecars/cockpit/web/js/render-flow.js`, `sidecars/cockpit/web/js/actions.js`",
        "`renderFlow`, `renderFlowOverviewCard`, `saveFlowSettings`, `syncFlowCredits`",
    ),
    (
        "CSS / theme / scrollbar",
        "`sidecars/cockpit/web/styles.css`",
        "`:root` tokens, `.card`, `.pill`, `.tabs`, scrollbar rules",
    ),
    (
        "Formatters / countdown tickers",
        "`sidecars/cockpit/web/js/formatters.js`",
        "`fmtTok`, `fmtDuration`, `cdSpan`, `tickCountdowns`, `fmtCredits`",
    ),
    (
        "Shared frontend state / tab routing / poll focus guard",
        "`sidecars/cockpit/web/js/state.js`, `sidecars/cockpit/web/js/app.js`",
        "`state`, `POLL_MS`, `render`, `setTab`",
    ),
    (
        "HTTP API routes & static asset wiring",
        "`sidecars/cockpit/main.mjs`, `sidecars/cockpit/lib/runner.mjs`",
        "`app.api(...)`, `registerWebAssets`, `collectWebAssets`",
    ),
    (
        "Settings defaults, clamping & quota history store",
        "`sidecars/cockpit/lib/settings.mjs`",
        "`DEFAULT_SETTINGS`, `clampInt`, `normalizeSettings`, `createSettingsStore`, `createQuotaHistoryStore`",
    ),
    (
        "Antigravity LS RPC & pool refresh cache",
        "`sidecars/cockpit/lib/ls_client.mjs`",
        "`lsRequest`, `createPoolsService`",
    ),
    (
        "Model role/tier rules (`ROLE_RULES`) & pool aggregation",
        "`sidecars/cockpit/lib/pools_models.mjs`",
        "`ROLE_RULES`, `classifyModelRole`, `parseQuotaSummary`, `buildPoolsPayload`, `resolveActivePool`",
    ),
    (
        "7d weekly rolling recovery & snapshot ledger",
        "`sidecars/cockpit/lib/pools_recovery.mjs`",
        "`WINDOW_7D_SEC`, `recordQuotaSnapshot`, `buildWeeklyRecovery`, `enrichPoolsWithWeeklyRecovery`",
    ),
    (
        "Report runner caching & single-flight locks",
        "`sidecars/cockpit/lib/reports.mjs`",
        "`createReportsService`",
    ),
    (
        "Image quota constants & official token formula",
        "`sidecars/cockpit/image_quota/constants.py`",
        "`WINDOW_5H_SEC`, `DEFAULT_SOFT_CAP`, `DEFAULT_HARD_CAP`, `compute_event_tokens`",
    ),
    (
        "Image DB scanner, 429 classification & archive salvage",
        "`sidecars/cockpit/image_quota/scanner.py`",
        "`classify_error`, `extract_tool_image_info`, `scan_single_db`, `scan_conversations`, `_salvage_archived_event`",
    ),
    (
        "Image 5h/7d rolling schedules & with-ref avg",
        "`sidecars/cockpit/image_quota/schedules.py`",
        "`compute_per_image_avg`, `build_rolling_schedule`, `build_weekly_rolling_schedule`",
    ),
    (
        "Image 30d self-learning watermark, ledger & peak pinning",
        "`sidecars/cockpit/image_quota/calibration.py`",
        "`compute_calibration`, `load_calibration_ledger`, `pin_calibration_peak`, `merge_calibration_with_ledger`",
    ),
    (
        "Image anchor selection & workspace ledger linking",
        "`sidecars/cockpit/image_quota/report.py`",
        "`scan_workspace_ledgers`, `compute_anchor`, `build_report`",
    ),
    (
        "Flow video models, pricing & gflow binary discovery",
        "`sidecars/cockpit/flow_quota/constants.py`",
        "`VIDEO_MODELS`, `MODEL_BY_ID`, `find_gflow_bin`, `canonical_model_id`",
    ),
    (
        "Flow read-only ledger, gflow.db, locks & workspace runs",
        "`sidecars/cockpit/flow_quota/readers.py`",
        "`read_ledger`, `read_gflow_db`, `inspect_profile_locks`, `scan_workspace_flow_runs`",
    ),
    (
        "Flow manual live credit sync & structlog stripping",
        "`sidecars/cockpit/flow_quota/sync.py`",
        "`parse_gflow_json_stdout`, `sync_live_credits`",
    ),
    (
        "Flow 1h/24h anti-throttle rolling buckets & circuit breaker",
        "`sidecars/cockpit/flow_quota/report.py`",
        "`build_rolling_buckets`, `compute_breaker`, `merge_profiles`, `build_report`",
    ),
    (
        "Conversation protobuf decoding & cache hit analytics",
        "`sidecars/cockpit/context_parser/protobuf.py`, `sidecars/cockpit/context_parser/collector.py`",
        "`Msg`, `read_varint`, `collect_cache_stats`, `collect_stats`",
    ),
    (
        "Context CLI rendering & model window table",
        "`sidecars/cockpit/context_parser/renderer.py`, `sidecars/cockpit/context_parser/models.py`",
        "`render`, `load`, `NATIVE_CONTEXT_WINDOWS`, `context_limit`",
    ),
    (
        "Local 7d per-pool usage scanner",
        "`sidecars/cockpit/pool_usage_report.py`",
        "`pool_key_for_usage`, `scan_single_db_usage`, `build_pool_usage_report`",
    ),
    (
        "Index generator & drift guard",
        "`scripts/generate_index.py`, `tests/test_project_index.py`",
        "`generate_markdown`, `main`, `TestProjectIndex`",
    ),
]

DATA_FILES: list[tuple[str, str, str]] = [
    ("`settings.json`", "`ANTIGRAVITY_EXECUTABLE_DATA_DIR`", "User soft/hard image caps, count/anchor mode, and Flow 1h/24h/credit/gap settings (`lib/settings.mjs`)."),
    ("`model-catalog.json`", "`ANTIGRAVITY_EXECUTABLE_DATA_DIR`", "Cached LS `GetAvailableModels` catalog snapshot merged into pool & context lookups."),
    ("`image-scan-cache.json`", "`ANTIGRAVITY_EXECUTABLE_DATA_DIR`", "Incremental SQLite scan cache keyed by conversation DB stamp (`image_quota/scanner.py`)."),
    ("`image-calibration.json`", "`ANTIGRAVITY_EXECUTABLE_DATA_DIR`", "30-day self-learning safe/429 peak ledger, truncated lock history, and pinned peak (`image_quota/calibration.py`)."),
    ("`flow-credits-cache.json`", "`ANTIGRAVITY_EXECUTABLE_DATA_DIR`", "Manual live Flow credit sync snapshot and per-profile credit balance history (`flow_quota/readers.py`)."),
    ("`pool-usage-cache.json`", "`ANTIGRAVITY_EXECUTABLE_DATA_DIR`", "Cached 7d per-pool request usage buckets extracted from local conversation DBs (`pool_usage_report.py`)."),
    ("`quota-history.json`", "`ANTIGRAVITY_EXECUTABLE_DATA_DIR`", "8-day rolling pool snapshot time series for weekly recovery predictions (`lib/pools_recovery.mjs`)."),
]

READONLY_SOURCES: list[tuple[str, str]] = [
    ("`conversations/*.db`", "Antigravity SQLite conversation DBs (`steps`, `gen_metadata`) read via `?mode=ro` for context, image quota, and pool usage."),
    ("`gflow.db`", "Local `gflow-cli` SQLite database read via `?mode=ro` for profiles, video operations, model counts, and WAF/error telemetry."),
    ("`gflow_traffic_ledger.json`", "Local Flow traffic ledger (`GFLOW_TRAFFIC_LEDGER` or `~/.gemini/gflow_traffic_ledger.json`) tracking 1h/24h/credit burn."),
    ("`api_call_ledger.json`", "Workspace image generation call ledger linked by `image_quota/report.py`."),
    ("`flow_run_state.json`", "Workspace Flow runner state files discovered by `flow_quota/readers.py`."),
]

FILE_DESCRIPTIONS: dict[str, str] = {
    "sidecars/cockpit/web/index.html": "Top header, 5-tab navigation shell, and classic modular script entries.",
    "sidecars/cockpit/web/styles.css": "Cockpit dark theme variables, layout cards, grids, badges, and scrollbar styles.",
    "sidecars/cockpit/web/js/state.js": "Shared reactive UI state, polling interval, and context category constants.",
    "sidecars/cockpit/web/js/formatters.js": "Token/duration/currency/credit formatters, countdown tags, and grid cell helpers.",
    "sidecars/cockpit/web/js/render-header.js": "Sticky header renderer for active model, pool pill, and 5h/weekly summary.",
    "sidecars/cockpit/web/js/render-overview.js": "Overview tab summary cards (Image, Pools, Context/Cache, Flow).",
    "sidecars/cockpit/web/js/render-image.js": "Image Quota tab: anchor gauge, rolling schedules, calibration, and settings form.",
    "sidecars/cockpit/web/js/render-pools.js": "Model Quota Pools tab: Pool A/B windows, model list, and 7d weekly recovery.",
    "sidecars/cockpit/web/js/render-context.js": "Context & Prompt Cache tab: 100-dot grid, cache hit analytics, and file sections.",
    "sidecars/cockpit/web/js/render-flow.js": "Google Flow tab: synced credits, 1h/24h rolling caps, breaker status, and settings.",
    "sidecars/cockpit/web/js/actions.js": "Frontend API actions for settings, Flow sync, peak pinning, calibration reset, and refresh.",
    "sidecars/cockpit/web/js/app.js": "DOM diff renderer, tab router, event delegation, and polling loop.",
    "sidecars/cockpit/main.mjs": "Sidecar HTTP API server wiring static assets, pools, and Python report services.",
    "sidecars/cockpit/pools_logic.mjs": "Backward-compatible facade re-exporting pool model and weekly recovery helpers.",
    "sidecars/cockpit/lib/ls_client.mjs": "Language Server RPC client and cached dual-pool refresh service.",
    "sidecars/cockpit/lib/pools_models.mjs": "Model role classification rules, quota summary parsing, and pool payload builder.",
    "sidecars/cockpit/lib/pools_recovery.mjs": "7-day quota snapshot ledger and weekly rolling recovery estimation.",
    "sidecars/cockpit/lib/reports.mjs": "Python report runner caching, TTL management, and single-flight locks.",
    "sidecars/cockpit/lib/runner.mjs": "Subprocess runner, Python discovery, workspace URI decoding, and static asset registration.",
    "sidecars/cockpit/lib/settings.mjs": "Settings normalization/clamping and JSON stores for settings and quota history.",
    "sidecars/cockpit/package.json": "Node ESM package metadata for the cockpit sidecar.",
    "sidecars/cockpit/sidecar.json": "Sidecar manifest declaring entrypoint and runtime metadata.",
    "sidecars/cockpit/context_report.py": "CLI entrypoint and compatibility re-exports for `context_parser`.",
    "sidecars/cockpit/flow_report.py": "CLI entrypoint and compatibility re-exports for `flow_quota`.",
    "sidecars/cockpit/quota_report.py": "CLI entrypoint and compatibility re-exports for `image_quota`.",
    "sidecars/cockpit/pool_usage_report.py": "Read-only 7-day per-pool usage scanner over local conversation DBs.",
    "sidecars/cockpit/context_parser/__init__.py": "Subpackage facade re-exporting protobuf, models, collector, and renderer APIs.",
    "sidecars/cockpit/context_parser/collector.py": "Conversation step token accounting, system file detection, and prompt cache stats.",
    "sidecars/cockpit/context_parser/models.py": "Model context window catalog, effort levels, and model label helpers.",
    "sidecars/cockpit/context_parser/protobuf.py": "Lightweight protobuf varint/wire decoder and step type constants.",
    "sidecars/cockpit/context_parser/renderer.py": "CLI text report and JSON loader for conversation context usage.",
    "sidecars/cockpit/flow_quota/__init__.py": "Subpackage facade re-exporting Flow quota constants, readers, sync, and report APIs.",
    "sidecars/cockpit/flow_quota/constants.py": "Flow video model specs, credit pricing, defaults, and gflow binary lookup.",
    "sidecars/cockpit/flow_quota/readers.py": "Read-only readers for Flow ledger, `gflow.db`, profile locks, and workspace runs.",
    "sidecars/cockpit/flow_quota/report.py": "Flow 1h/24h rolling buckets, circuit breaker computation, and report builder.",
    "sidecars/cockpit/flow_quota/sync.py": "Manual live credit sync via `gflow` CLI with structlog filtering.",
    "sidecars/cockpit/image_quota/__init__.py": "Subpackage facade re-exporting image quota constants, scanner, schedules, and calibration.",
    "sidecars/cockpit/image_quota/calibration.py": "30-day self-learning 429/safe watermark calibration, ledger, and peak pinning.",
    "sidecars/cockpit/image_quota/constants.py": "Image quota windows, token pricing constants, regexes, and token formulas.",
    "sidecars/cockpit/image_quota/report.py": "Workspace ledger linking, anchor selection, and image quota report builder.",
    "sidecars/cockpit/image_quota/scanner.py": "Read-only SQLite image call scanner, 429 classifier, cache, and archive salvage.",
    "sidecars/cockpit/image_quota/schedules.py": "5h/7d rolling recovery schedules and with-reference per-image token averages.",
    "tests/test_context_report.py": "Unit tests for context protobuf parsing and cache hit analytics.",
    "tests/test_flow_report.py": "Unit tests for Flow rolling buckets, circuit breaker, and credit sync parsing.",
    "tests/test_pool_usage_report.py": "Unit tests for 7d per-pool local usage aggregation.",
    "tests/test_pools.mjs": "Node tests for pool model classification and weekly recovery math.",
    "tests/test_project_index.py": "Drift guard verifying `PROJECT_INDEX.md` stays synchronized with the codebase.",
    "tests/test_quota_report.py": "Unit tests for image quota scanning, schedules, calibration, and anchors.",
    "tests/test_sidecar_routes.mjs": "Node tests for sidecar route wiring, settings, and report caching.",
    "tests/test_web_assets.mjs": "Node tests for web asset collection, cache headers, and settings normalization.",
    "plugin.json": "Antigravity plugin manifest describing name, version, and suggested prompts.",
    "README.md": "Plugin overview, architecture summary, and installation/usage guide.",
    "assets/logo.svg": "Plugin vector icon displayed in Antigravity UI.",
    "scripts/generate_index.py": "Automated `PROJECT_INDEX.md` generator and `--check` drift verifier.",
}


def count_lines(path: Path) -> int:
    """Count lines in a UTF-8 text file."""
    return len(path.read_text(encoding="utf-8", errors="replace").splitlines())


def iter_repo_files() -> list[Path]:
    """Return sorted repo files excluding VCS, caches, runtime data, and PROJECT_INDEX.md."""
    files: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(REPO_ROOT)
        if any(part in EXCLUDED_DIRS for part in rel.parts):
            continue
        if rel.as_posix() in EXCLUDED_FILES or path.suffix == ".pyc":
            continue
        files.append(path)
    return sorted(files, key=lambda p: p.relative_to(REPO_ROOT).as_posix())


def extract_http_routes(main_mjs: Path) -> list[tuple[str, str, int, int]]:
    """Extract app.api('/api/...', ..., 'METHOD') declarations and line spans."""
    text = main_mjs.read_text(encoding="utf-8")
    pattern = re.compile(
        r"app\.api\(\s*['\"]([^'\"]+)['\"]\s*,[\s\S]*?,\s*['\"]([A-Z]+)['\"]\s*\);",
    )
    routes: list[tuple[str, str, int, int]] = []
    for match in pattern.finditer(text):
        start_line = text.count("\n", 0, match.start()) + 1
        end_line = text.count("\n", 0, match.end()) + 1
        routes.append((match.group(2), match.group(1), start_line, end_line))
    return routes


def collect_static_web_routes(web_dir: Path) -> list[str]:
    """List static web routes exposed by registerWebAssets()."""
    if not web_dir.is_dir():
        return []
    routes: list[str] = []
    if (web_dir / "index.html").is_file():
        routes.append("/")
    for path in sorted(web_dir.rglob("*")):
        if path.is_file():
            rel = path.relative_to(web_dir).as_posix()
            routes.append(f"/web/{rel}")
    return routes


def classify_group(rel_posix: str) -> str:
    """Map a repo-relative path to its architecture layer group."""
    if rel_posix.startswith("sidecars/cockpit/web/"):
        return "Frontend (`sidecars/cockpit/web/`)"
    if rel_posix.startswith("sidecars/cockpit/") and (
        rel_posix.endswith(".mjs") or rel_posix.endswith(".json")
    ):
        return "Node Backend (`sidecars/cockpit/`)"
    if rel_posix.startswith("sidecars/cockpit/") and rel_posix.endswith(".py"):
        return "Python Analyzers (`sidecars/cockpit/`)"
    if rel_posix.startswith("tests/"):
        return "Tests (`tests/`)"
    return "Tooling & Metadata"


def is_pure_py_facade(path: Path, tree: ast.Module) -> bool:
    """Return True for __init__.py or thin CLI wrappers with only imports/constants/main guard."""
    has_defs = any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        for node in tree.body
    )
    if path.name == "__init__.py" and not has_defs:
        return True
    if not has_defs and path.name in {"context_report.py", "flow_report.py", "quota_report.py"}:
        return True
    return False


def extract_python_symbols(path: Path) -> tuple[bool, list[str], list[str]]:
    """Return (is_facade, defs_and_classes, top_level_constants) from a Python file."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=path.as_posix())
    if is_pure_py_facade(path, tree):
        return True, [], []

    symbols: list[str] = []
    constants: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            end = node.end_lineno or node.lineno
            symbols.append(f"`class {node.name}` (L{node.lineno}-L{end})")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = node.end_lineno or node.lineno
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            symbols.append(f"`{prefix} {node.name}` (L{node.lineno}-L{end})")
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    constants.append(f"`{target.id}` (L{node.lineno})")
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id.isupper():
                constants.append(f"`{target.id}` (L{node.lineno})")
    return False, symbols, constants


def extract_js_symbols(path: Path) -> tuple[bool, list[str], list[str]]:
    """Extract module-level JS/MJS functions and const exports with line numbers.

    Supports both top-level ESM declarations (`export function` / `export const`
    in `*.mjs`) and 2-space-indented classic IIFE declarations (`function` / `const`
    inside `(function (QC) { ... })` in `web/js/*.js`).
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    if path.name == "pools_logic.mjs":
        return True, [], []

    is_classic_iife = path.suffix == ".js" and "(function (QC)" in text
    if is_classic_iife:
        decl_re = re.compile(
            r"^  (?P<async>async\s+)?(?P<kind>function|const|class)\s+(?P<name>[A-Za-z_$][\w$]*)"
        )
    else:
        decl_re = re.compile(
            r"^(?P<export>export\s+)?(?P<async>async\s+)?(?P<kind>function|const|class)\s+(?P<name>[A-Za-z_$][\w$]*)"
        )

    functions: list[str] = []
    exported_consts: list[str] = []
    for idx, line in enumerate(lines, start=1):
        match = decl_re.match(line)
        if not match:
            continue
        is_export = bool(match.groupdict().get("export"))
        is_async = bool(match.group("async"))
        kind = match.group("kind")
        name = match.group("name")
        if kind in {"function", "class"}:
            exp_prefix = "export " if is_export else ""
            async_prefix = "async " if is_async else ""
            functions.append(f"`{exp_prefix}{async_prefix}{kind} {name}` (L{idx})")
        elif kind == "const":
            if is_classic_iife:
                exported_consts.append(f"`const {name}` (L{idx})")
            elif is_export:
                exported_consts.append(f"`export const {name}` (L{idx})")
    return False, functions, exported_consts


def render_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    """Render a GitHub-flavored Markdown table."""
    out = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        out.append("| " + " | ".join(row) + " |")
    return out


def generate_markdown() -> str:
    """Build the complete PROJECT_INDEX.md content."""
    lines: list[str] = []
    lines.append("# quota-cockpit Project Index")
    lines.append("")
    lines.append(
        "> **Quick Usage**: Agents and contributors should read `PROJECT_INDEX.md` first to jump directly to the exact small module and line range instead of reading monolithic files. Refresh after structural or symbol edits via `python scripts/generate_index.py`, and verify synchronization via `python scripts/generate_index.py --check`."
    )
    lines.append("")

    # Section 1: Routing table
    lines.append("## 1. 按需求秒定位路由表 (Task → Files → Key Symbols)")
    lines.append("")
    routing_table_rows = [[task, files, symbols] for task, files, symbols in ROUTING_ROWS]
    lines.extend(render_table(["Task / Edit Intent", "Target Files", "Key Symbols / Anchors"], routing_table_rows))
    lines.append("")

    # Section 2: HTTP API & static routes
    lines.append("## 2. HTTP API & 静态路由速查")
    lines.append("")
    main_mjs = REPO_ROOT / "sidecars" / "cockpit" / "main.mjs"
    api_routes = extract_http_routes(main_mjs)
    route_descriptions = {
        "/api/dashboard": "Aggregates pools, context, imageQuota, flowQuota, and active pool metadata.",
        "/api/settings": "Persists normalized settings, invalidates image/flow caches, and refreshes both reports.",
        "/api/flow/sync-credits": "Runs explicit manual Flow live credit sync (`--sync-credits`) when profile is unlocked.",
        "/api/calibration/pin": "Pins or unpins the empirical 5h image safe peak (`--pin-peak` / `--unpin-peak`).",
        "/api/calibration/reset": "Deletes `image-calibration.json`, invalidates cache, and rebuilds image quota report.",
        "/api/report": "Refreshes pool state (cached) and returns context report for a single conversation.",
    }
    api_rows = [
        [
            f"`{method}`",
            f"`{route}`",
            f"`sidecars/cockpit/main.mjs` (L{start}-L{end})",
            route_descriptions.get(route, "Sidecar JSON API endpoint."),
        ]
        for method, route, start, end in api_routes
    ]
    lines.extend(render_table(["Method", "Route", "Definition", "Responsibility"], api_rows))
    lines.append("")

    static_routes = collect_static_web_routes(REPO_ROOT / "sidecars" / "cockpit" / "web")
    lines.append(
        f"**Static Web Routes** (`sidecars/cockpit/lib/runner.mjs` → `registerWebAssets`, all served with `Cache-Control: no-store` & `X-Content-Type-Options: nosniff`, {len(static_routes)} routes): "
        + ", ".join(f"`{r}`" for r in static_routes)
    )
    lines.append("")

    # Section 3: Persistent data & external read-only sources
    lines.append("## 3. 持久化数据与外部只读数据源")
    lines.append("")
    lines.append("### 持久化 JSON 文件 (`ANTIGRAVITY_EXECUTABLE_DATA_DIR`)")
    lines.append("")
    lines.extend(render_table(["File", "Location Env", "Purpose"], [[f, loc, desc] for f, loc, desc in DATA_FILES]))
    lines.append("")
    lines.append("### 外部只读数据源")
    lines.append("")
    lines.extend(render_table(["Source", "Read-Only Consumer & Usage"], [[src, desc] for src, desc in READONLY_SOURCES]))
    lines.append("")

    # Section 4: Layered module directory overview
    lines.append("## 4. 分层模块目录总览（自动统计行数）")
    lines.append("")
    group_order = [
        "Frontend (`sidecars/cockpit/web/`)",
        "Node Backend (`sidecars/cockpit/`)",
        "Python Analyzers (`sidecars/cockpit/`)",
        "Tests (`tests/`)",
        "Tooling & Metadata",
    ]
    grouped: dict[str, list[tuple[str, int, str]]] = {g: [] for g in group_order}
    total_lines = 0
    total_files = 0
    for path in iter_repo_files():
        rel_posix = path.relative_to(REPO_ROOT).as_posix()
        n_lines = count_lines(path)
        total_lines += n_lines
        total_files += 1
        desc = FILE_DESCRIPTIONS.get(rel_posix, "Repository file.")
        grouped[classify_group(rel_posix)].append((rel_posix, n_lines, desc))

    for group in group_order:
        entries = grouped[group]
        group_lines = sum(item[1] for item in entries)
        lines.append(f"### {group} — {len(entries)} files, {group_lines} lines")
        lines.append("")
        rows = [[f"`{rel}`", str(n_lines), desc] for rel, n_lines, desc in entries]
        lines.extend(render_table(["File", "Lines", "Description"], rows))
        lines.append("")

    lines.append(f"**Total Tracked Source & Doc Files**: {total_files} files, {total_lines} lines (excluding `PROJECT_INDEX.md`).")
    lines.append("")

    # Section 5: Symbol-level exact line number index
    lines.append("## 5. 符号级精确行号索引（自动提取）")
    lines.append("")
    lines.append("### Python Modules (`sidecars/cockpit/**/*.py` & `scripts/*.py`)")
    lines.append("")
    py_paths = sorted(
        list((REPO_ROOT / "sidecars" / "cockpit").rglob("*.py"))
        + list((REPO_ROOT / "scripts").glob("*.py")),
        key=lambda p: p.relative_to(REPO_ROOT).as_posix(),
    )
    for py_path in py_paths:
        if any(part in EXCLUDED_DIRS for part in py_path.parts):
            continue
        rel_posix = py_path.relative_to(REPO_ROOT).as_posix()
        n_lines = count_lines(py_path)
        is_facade, symbols, constants = extract_python_symbols(py_path)
        if is_facade:
            role_note = (
                "Subpackage facade / re-exports."
                if py_path.name == "__init__.py"
                else "CLI wrapper / compatibility re-exports."
            )
            lines.append(f"- **`{rel_posix}`** ({n_lines}L): {role_note}")
            continue
        lines.append(f"- **`{rel_posix}`** ({n_lines}L)")
        if symbols:
            lines.append("  - Symbols: " + ", ".join(symbols))
        if constants:
            lines.append("  - Constants: " + ", ".join(constants))
    lines.append("")

    lines.append("### Node & Frontend Modules (`sidecars/cockpit/**/*.mjs` & `sidecars/cockpit/**/*.js`)")
    lines.append("")
    js_paths = sorted(
        list((REPO_ROOT / "sidecars" / "cockpit").rglob("*.mjs"))
        + list((REPO_ROOT / "sidecars" / "cockpit").rglob("*.js")),
        key=lambda p: p.relative_to(REPO_ROOT).as_posix(),
    )
    for js_path in js_paths:
        if any(part in EXCLUDED_DIRS for part in js_path.parts):
            continue
        rel_posix = js_path.relative_to(REPO_ROOT).as_posix()
        n_lines = count_lines(js_path)
        is_facade, functions, exported_consts = extract_js_symbols(js_path)
        if is_facade:
            lines.append(f"- **`{rel_posix}`** ({n_lines}L): Backward-compatible re-export facade.")
            continue
        if rel_posix == "sidecars/cockpit/main.mjs":
            route_refs = ", ".join(f"`{m} {r}` (L{s})" for m, r, s, _ in api_routes)
            lines.append(f"- **`{rel_posix}`** ({n_lines}L): Sidecar bootstrap & HTTP API routes: {route_refs}")
            continue
        lines.append(f"- **`{rel_posix}`** ({n_lines}L)")
        if functions:
            lines.append("  - Functions: " + ", ".join(functions))
        if exported_consts:
            lines.append("  - Exports: " + ", ".join(exported_consts))
    lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for generating or checking PROJECT_INDEX.md."""
    parser = argparse.ArgumentParser(description="Generate or verify PROJECT_INDEX.md.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify PROJECT_INDEX.md matches generated output without modifying it.",
    )
    args = parser.parse_args(argv)

    expected = generate_markdown()
    if args.check:
        if not INDEX_PATH.is_file():
            print("PROJECT_INDEX.md does not exist. Run: python scripts/generate_index.py", file=sys.stderr)
            return 1
        actual = INDEX_PATH.read_text(encoding="utf-8").replace("\r\n", "\n")
        if actual == expected:
            print("PROJECT_INDEX.md is up to date.")
            return 0
        diff = "".join(
            difflib.unified_diff(
                actual.splitlines(keepends=True),
                expected.splitlines(keepends=True),
                fromfile="PROJECT_INDEX.md (current)",
                tofile="PROJECT_INDEX.md (generated)",
                n=2,
            )
        )
        print("PROJECT_INDEX.md is out of date! Diff:\n" + diff[:4000], file=sys.stderr)
        return 1

    INDEX_PATH.write_text(expected, encoding="utf-8", newline="\n")
    print(f"Wrote {INDEX_PATH} ({len(expected.splitlines())} lines).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
