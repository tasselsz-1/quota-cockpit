#!/usr/bin/env python3
"""Read-only Google Flow video quota & rate-limit monitor for quota-cockpit.

Design goals:
- Default mode is 100% local, read-only (?mode=ro SQLite), zero network, zero
  browser launches, zero profile lock contention (< 50 ms).
- Reads ~/.gemini/gflow_traffic_ledger.json (rolling 1h/24h caps, daily credit
  budget, min-gap cooldown, circuit breaker) and local gflow-cli SQLite catalog
  (gflow.db: profiles, operations, model stats, WAF/error history).
- Optional --sync-credits mode is triggered ONLY by explicit user action to run
  `gflow credits list --json` (after checking active profile locks) and persist
  the snapshot into flow-credits-cache.json. Between manual syncs, post-sync
  ledger spend is deducted automatically to show an up-to-date estimate.
"""

from __future__ import annotations

from datetime import datetime, timezone  # noqa: F401
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from flow_quota import (  # noqa: E402, F401
    CREDITS_CACHE_VERSION,
    CREDITS_HISTORY_MAX,
    DAY_SEC,
    DEFAULT_MAX_DAILY_CREDITS,
    DEFAULT_MAX_PER_DAY,
    DEFAULT_MAX_PER_HOUR,
    DEFAULT_MIN_GAP_SEC,
    DEFAULT_MIN_REMAINING_CREDITS,
    HOUR_SEC,
    LEDGER_COUNTED_ACTIONS,
    LOCK_STALE_SEC,
    MODEL_ALIAS_TO_ID,
    MODEL_BY_ID,
    MONTH_SEC,
    REPORT_VERSION,
    VIDEO_MODELS,
    WEEK_SEC,
    WORKSPACE_SKIP_DIRS,
    _extract_profiles_from_credits_payload,
    _is_structlog_dict,
    _is_video_op,
    _is_waf_error,
    _open_sqlite_ro,
    build_report,
    build_rolling_buckets,
    canonical_model_id,
    compute_breaker,
    default_gflow_data_dir,
    default_ledger_path,
    find_gflow_bin,
    inspect_profile_locks,
    is_pid_alive,
    main,
    merge_profiles,
    model_label,
    parse_gflow_json_stdout,
    parse_iso_ts,
    read_credits_cache,
    read_default_profile_from_config,
    read_gflow_db,
    read_ledger,
    safe_float,
    safe_int,
    save_credits_cache,
    scan_workspace_flow_runs,
    sync_live_credits,
)

if __name__ == "__main__":
    raise SystemExit(main())
