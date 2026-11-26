#!/usr/bin/env python3
"""Image generation quota & workspace ledger analyzer for Antigravity Quota Cockpit.

Scans local Antigravity conversation databases (read-only) for `generate_image`
tool calls, extracts official 429 `quotaResetTimeStamp` / model metadata, computes
5-hour / 24-hour / 7-day rolling statistics and empirical 429 ceilings, and links
optional workspace `api_call_ledger.json` files when present.
"""

from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from context_parser.protobuf import Msg  # noqa: E402, F401
from image_quota import (  # noqa: E402, F401
    BILLABLE_KINDS,
    CACHE_VERSION,
    CALIBRATION_LEDGER_VERSION,
    DEFAULT_HARD_CAP,
    DEFAULT_SOFT_CAP,
    KNOWN_EVENT_KINDS,
    OUTPUT_1K_IMAGE_TOKENS,
    PRICE_INPUT_PER_M,
    PRICE_OUTPUT_IMG_PER_M,
    PRICE_THINKING_PER_M,
    REF_IMAGE_TOKENS,
    REQUIRED_CACHED_EVENT_FIELDS,
    RE_CJK,
    RE_ERR_MODEL,
    RE_ERR_MSG,
    RE_RESET_DELAY,
    RE_RESET_TS,
    RE_RETRY_DELAY,
    SKIP_DIRS,
    THINKING_MINIMAL_TOKENS,
    TRUNCATED_LOCK_GAP_SEC,
    WEEKLY_LOCK_THRESHOLD_SEC,
    WINDOW_24H_SEC,
    WINDOW_30D_SEC,
    WINDOW_5H_SEC,
    WINDOW_7D_SEC,
    _cleanup_stale_cache_temps,
    _empty_calibration_ledger,
    _empty_peak_dict,
    _is_valid_cached_event,
    _is_valid_cached_events,
    _load_cache_raw,
    _lock_obs_quality,
    _merge_peak_dicts,
    _normalize_peak_dict,
    _normalize_pinned_peak,
    _salvage_archived_event,
    build_report,
    build_rolling_schedule,
    build_weekly_rolling_schedule,
    classify_error,
    compute_anchor,
    compute_calibration,
    compute_event_tokens,
    compute_per_image_avg,
    db_combined_stamp,
    db_latest_mtime,
    estimate_text_tokens,
    extract_tool_image_info,
    file_stamp,
    load_cache,
    load_calibration_ledger,
    main,
    merge_calibration_with_ledger,
    parse_iso_utc,
    pin_calibration_peak,
    reset_calibration_ledger,
    save_cache,
    save_calibration_ledger,
    scan_conversations,
    scan_single_db,
    scan_workspace_ledgers,
    summarize_bucket,
)

if __name__ == "__main__":
    main()
