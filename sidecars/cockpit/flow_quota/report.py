"""Rolling window calculators, report aggregation, and CLI entrypoint."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import time
from typing import Any

from .constants import (
    ALL_FLOW_ACTIONS,
    DAY_SEC,
    DEFAULT_IMAGE_FINISH_FLOOR_SEC,
    DEFAULT_IMAGE_HEAT_REST_EVERY,
    DEFAULT_IMAGE_MIN_GAP_SEC,
    DEFAULT_IMAGE_PEAK_GAP_SEC,
    DEFAULT_IMAGE_S2S_GAP_SEC,
    DEFAULT_IMAGE_S2S_PEAK_GAP_SEC,
    DEFAULT_IMAGE_S2S_RECOVERY_GAP_SEC,
    DEFAULT_IMAGE_WAF_RECOVERY_GAP_SEC,
    DEFAULT_MAX_DAILY_CREDITS,
    DEFAULT_MAX_PER_DAY,
    DEFAULT_MAX_PER_HOUR,
    DEFAULT_MAX_RECAPTCHA_10M,
    DEFAULT_MIN_GAP_SEC,
    DEFAULT_MIN_REMAINING_CREDITS,
    HOUR_SEC,
    IMAGE_ACTIONS,
    IMAGE_POOL_BY_ID,
    IMAGE_QUOTA_POOLS,
    MAX_RECAPTCHA_HARD_CEILING,
    MODEL_BY_ID,
    MONTH_SEC,
    PEAK_MAX_RECAPTCHA_10M,
    RECAPTCHA_ACTIONS,
    REPORT_VERSION,
    VIDEO_MODELS,
    WAF_WINDOW_SEC,
    WEEK_SEC,
    canonical_model_id,
    default_gflow_data_dir,
    default_global_lock_path,
    default_ledger_path,
    infer_image_quota_pool,
    is_peak_hours,
    model_label,
    parse_iso_ts,
    safe_float,
    safe_int,
)
from .readers import (
    inspect_profile_locks,
    inspect_single_flight_lock,
    read_credits_cache,
    read_default_profile_from_config,
    read_gflow_db,
    read_ledger,
    scan_workspace_flow_runs,
)
from .sync import _resolve_gflow_bin, sync_live_credits

# --- Rolling window & recovery schedule calculators ---------------------------


def build_rolling_buckets(
    events: list[dict[str, Any]],
    window_sec: float,
    bucket_sec: float,
    now: float,
    max_buckets: int = 6,
    count_key: str | None = None,
) -> list[dict[str, Any]]:
    """Group active events into upcoming roll-off buckets (when slots/credits recover)."""
    buckets: dict[int, dict[str, Any]] = {}
    for ev in events:
        ts = safe_float(ev.get("ts"), 0.0)
        if ts <= 0:
            continue
        recover_ts = ts + window_sec
        if recover_ts <= now:
            continue
        bkey = int(math.floor(recover_ts / bucket_sec) * bucket_sec)
        slot = buckets.get(bkey)
        if slot is None:
            slot = {
                "recover_ts": recover_ts,
                "bucket_start": bkey,
                "count": 0,
                "credits": 0.0,
            }
            buckets[bkey] = slot
        slot["recover_ts"] = min(slot["recover_ts"], recover_ts)
        inc = max(1, safe_int(ev.get(count_key), 1)) if count_key else 1
        slot["count"] += inc
        slot["credits"] = round(slot["credits"] + max(0.0, safe_float(ev.get("credits"), 0.0)), 1)
    ordered = sorted(buckets.values(), key=lambda x: x["recover_ts"])
    return ordered[:max_buckets]


def compute_breaker(raw_breaker: dict[str, Any], now: float) -> dict[str, Any]:
    active = bool(raw_breaker.get("active"))
    manual = bool(raw_breaker.get("requires_manual_reset"))
    until_ts = safe_float(raw_breaker.get("until_ts"), 0.0)
    if until_ts <= 0 and raw_breaker.get("until_iso"):
        until_ts = parse_iso_ts(raw_breaker.get("until_iso"))
    tripped_at = str(raw_breaker.get("tripped_at") or "")
    tripped_ts = parse_iso_ts(tripped_at)
    reason = str(raw_breaker.get("reason") or "").strip()
    is_open = manual or (active and until_ts > now)
    remaining_sec = max(0, int(round(until_ts - now))) if (active and not manual and until_ts > now) else 0
    return {
        "open": is_open,
        "active": active,
        "requires_manual_reset": manual,
        "until_ts": round(until_ts) if until_ts > 0 else 0,
        "remaining_sec": remaining_sec,
        "reason": reason,
        "tripped_at": tripped_at,
        "tripped_ts": round(tripped_ts) if tripped_ts > 0 else 0,
    }


def compute_model_breakers(raw_breakers: dict[str, Any], now: float) -> dict[str, dict[str, Any]]:
    active_map: dict[str, dict[str, Any]] = {}
    if not isinstance(raw_breakers, dict):
        return active_map
    for pool_id, info in raw_breakers.items():
        if not isinstance(info, dict):
            continue
        until_ts = safe_float(info.get("until_ts"), 0.0)
        if until_ts <= 0 and info.get("until_iso"):
            until_ts = parse_iso_ts(info.get("until_iso"))
        if until_ts <= now:
            continue
        pid = str(pool_id)
        meta = IMAGE_POOL_BY_ID.get(pid, {})
        active_map[pid] = {
            "pool": pid,
            "label": str(meta.get("label") or pid),
            "until_ts": round(until_ts),
            "remaining_sec": max(0, int(round(until_ts - now))),
            "reason": str(info.get("reason") or "").strip(),
            "tripped_at": str(info.get("tripped_at") or ""),
        }
    return active_map


# --- Main report aggregation --------------------------------------------------


def merge_profiles(
    db_profiles: list[dict[str, Any]],
    cache_profiles: list[dict[str, Any]],
    default_profile: str,
) -> list[dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    for p in db_profiles:
        name = str(p.get("profile") or "").strip()
        if not name:
            continue
        by_name[name] = {
            "profile": name,
            "email": str(p.get("email") or ""),
            "is_default": name == default_profile,
            "authenticated": bool(p.get("cookies_present")),
            "cookies_present": bool(p.get("cookies_present")),
            "credits": None,
            "last_used_at": round(safe_float(p.get("last_used_at"), 0.0)),
        }
    for p in cache_profiles:
        name = str(p.get("profile") or "").strip()
        if not name:
            continue
        cur = by_name.get(name, {
            "profile": name,
            "email": "",
            "is_default": False,
            "authenticated": False,
            "cookies_present": False,
            "credits": None,
            "last_used_at": 0,
        })
        if p.get("email"):
            cur["email"] = str(p["email"])
        if p.get("is_default"):
            cur["is_default"] = True
        if p.get("authenticated") is not None:
            cur["authenticated"] = bool(p["authenticated"])
        if p.get("credits") is not None:
            cur["credits"] = round(safe_float(p["credits"], 0.0), 1)
        by_name[name] = cur

    profiles = list(by_name.values())
    if default_profile and not any(p["is_default"] for p in profiles):
        for p in profiles:
            if p["profile"] == default_profile:
                p["is_default"] = True
    profiles.sort(key=lambda x: (not x["is_default"], x["profile"]))
    return profiles


def _is_video_op(op: dict[str, Any]) -> bool:
    cmd = str(op.get("command") or "").lower()
    mode = str(op.get("mode") or "").lower()
    return cmd.startswith("video") or mode in ("i2v", "r2v", "t2v", "extend")


def _is_image_op(op: dict[str, Any]) -> bool:
    cmd = str(op.get("command") or "").lower()
    mode = str(op.get("mode") or "").lower()
    return cmd.startswith("image") or mode in ("t2i", "i2i", "image_t2i", "image_i2i", "image_upscale")


def _is_waf_error(err_type: str, err_detail: str) -> bool:
    hay = f"{err_type} {err_detail}".lower()
    return any(k in hay for k in ("waf", "unusual_activity", "captcha", "grpc 7"))


def _event_tag(outcome: str, exit_code: int) -> str:
    out = str(outcome or "").strip().upper()
    if not out:
        return "OK" if exit_code == 0 else "ERR"
    if out == "OK":
        return "OK"
    if out in ("AUTH_OR_CAPTCHA", "WAF_403", "AUTH_LOCK"):
        return "WAF"
    if out in ("RATE_LIMIT_OR_QUOTA", "RATE_LIMIT"):
        return "429"
    if out == "CREDIT_GUARD_ABORT":
        return "CAP"
    if out == "MODEL_QUOTA_EXCEEDED":
        return "POOL"
    if out == "CONTENT_POLICY":
        return "POL"
    if out == "SAME_IMAGE_SUSPECT":
        return "DUP"
    if out in ("TRANSPORT_TIMEOUT", "TIMEOUT"):
        return "TMO"
    if out == "HARD_BLOCK":
        return "BLK"
    return "ERR"


def build_report(
    ledger_path: Path,
    gflow_dir: Path,
    credits_cache_path: Path,
    workspace: str = "",
    max_per_hour: int = DEFAULT_MAX_PER_HOUR,
    max_per_day: int = DEFAULT_MAX_PER_DAY,
    max_daily_credits: float = DEFAULT_MAX_DAILY_CREDITS,
    min_remaining_credits: float = DEFAULT_MIN_REMAINING_CREDITS,
    min_gap_sec: float = DEFAULT_MIN_GAP_SEC,
    max_recaptcha_10m: int = DEFAULT_MAX_RECAPTCHA_10M,
    now: float | None = None,
    gflow_bin_override: str = "",
    sync_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now_ts = float(now) if now is not None else time.time()
    gflow_bin = _resolve_gflow_bin(gflow_bin_override)
    default_prof = read_default_profile_from_config(gflow_dir)
    busy, busy_detail = inspect_profile_locks(gflow_dir, now_ts)
    sf_lock = inspect_single_flight_lock(default_global_lock_path(ledger_path), now_ts)
    effective_busy = bool(busy or sf_lock["active"])
    effective_busy_detail = busy_detail or sf_lock["detail"]

    ledger = read_ledger(ledger_path)
    db = read_gflow_db(gflow_dir, now_ts)
    cache = read_credits_cache(credits_cache_path)
    workspace_runs = scan_workspace_flow_runs(workspace)

    # Filter ledger events (100% aligned with flow_traffic_guard.py v2 semantics)
    ledger_events: list[dict[str, Any]] = []
    for ev in ledger["events"]:
        act = str(ev.get("action") or "").strip().lower()
        ts = safe_float(ev.get("ts"), 0.0) or parse_iso_ts(ev.get("iso"))
        if ts <= 0 or act not in ALL_FLOW_ACTIONS:
            continue
        raw_tokens = ev.get("recaptcha_tokens")
        outcome = str(ev.get("outcome") or "").strip()
        tokens_for_unsub = 1 if raw_tokens is None else max(0, safe_int(raw_tokens, 0))
        is_local_unsubmitted = (tokens_for_unsub == 0 and outcome.lower() != "ok")
        recaptcha_tokens = (
            (1 if act in RECAPTCHA_ACTIONS else 0)
            if raw_tokens is None
            else max(0, safe_int(raw_tokens, 0))
        )
        submit_start_ts = safe_float(ev.get("submit_start_ts"), 0.0) or ts
        quota_units = max(1, safe_int(ev.get("quota_units"), 1))
        model_raw = str(ev.get("model") or "").strip()
        q_pool = str(ev.get("quota_pool") or "").strip()
        domain = "image" if act in IMAGE_ACTIONS else "video"
        if domain == "image" and not q_pool:
            q_pool = infer_image_quota_pool(act, model_raw)
        ledger_events.append({
            "ts": ts,
            "submit_start_ts": submit_start_ts,
            "action": act,
            "domain": domain,
            "episode": str(ev.get("episode") or ""),
            "segment": str(ev.get("segment") or ""),
            "credits": max(0.0, safe_float(ev.get("credits"), 0.0)),
            "outcome": outcome,
            "exit_code": safe_int(ev.get("exit_code"), 0),
            "recaptcha_tokens": recaptcha_tokens,
            "quota_units": quota_units,
            "model": canonical_model_id(model_raw) if model_raw else "",
            "quota_pool": q_pool,
            "is_local_unsubmitted": is_local_unsubmitted,
            "source": "ledger",
        })
    ledger_events.sort(key=lambda x: x["ts"])

    # Filter gflow.db operations
    db_ops = db["operations"]
    db_video_ops = [op for op in db_ops if _is_video_op(op)]
    db_image_ops = [op for op in db_ops if _is_image_op(op)]
    db_char_ops = [op for op in db_ops if str(op.get("command") or "").lower().startswith("character")]

    # Determine rolling rate-limit source
    if ledger["present"] and not ledger.get("parse_error"):
        rate_limit_source = "ledger"
        rolling_source_events = ledger_events
    elif db["present"]:
        rate_limit_source = "gflow_db"
        fallback_events: list[dict[str, Any]] = []
        for op in db_video_ops:
            fallback_events.append({
                "ts": op["ts"],
                "submit_start_ts": op["ts"],
                "action": op["mode"] or "video",
                "domain": "video",
                "episode": "",
                "segment": model_label(op["model"]),
                "credits": op["credits"] if op["credits"] > 0 else (
                    safe_float(MODEL_BY_ID.get(op["model"], {}).get("est_credits"), 10.0)
                    if op["status"] == "succeeded" else 0.0
                ),
                "outcome": "OK" if op["status"] == "succeeded" else (
                    "AUTH_OR_CAPTCHA" if _is_waf_error(op["error_type"], op["error_detail"]) else "OTHER_ERROR"
                ),
                "exit_code": 0 if op["status"] == "succeeded" else 1,
                "recaptcha_tokens": 1,
                "quota_units": 1,
                "model": op["model"],
                "quota_pool": "",
                "is_local_unsubmitted": False,
                "source": "gflow_db",
            })
        for op in db_image_ops:
            act = op["mode"] if op["mode"] in IMAGE_ACTIONS else "image_t2i"
            fallback_events.append({
                "ts": op["ts"],
                "submit_start_ts": op["ts"],
                "action": act,
                "domain": "image",
                "episode": "",
                "segment": model_label(op["model"]),
                "credits": 0.0,
                "outcome": "OK" if op["status"] == "succeeded" else (
                    "WAF_403" if _is_waf_error(op["error_type"], op["error_detail"]) else "OTHER_ERROR"
                ),
                "exit_code": 0 if op["status"] == "succeeded" else 1,
                "recaptcha_tokens": 1,
                "quota_units": 1,
                "model": op["model"],
                "quota_pool": infer_image_quota_pool(act, op["model"]),
                "is_local_unsubmitted": False,
                "source": "gflow_db",
            })
        fallback_events.sort(key=lambda x: x["ts"])
        rolling_source_events = fallback_events
    else:
        rate_limit_source = "none"
        rolling_source_events = []

    submitted_events = [e for e in rolling_source_events if not e["is_local_unsubmitted"]]
    video_submitted = [e for e in submitted_events if e["domain"] == "video"]

    # Peak hours (UTC 14-17 / CST 22-01) & 24h WAF recovery state
    last_waf_ts = safe_float(ledger.get("last_waf_ts"), 0.0)
    if last_waf_ts <= 0.0:
        last_waf_ts = max(
            (e["ts"] for e in rolling_source_events if e["exit_code"] == 10 or e["outcome"] == "WAF_403"),
            default=0.0,
        )
    recent_waf_recovery = (last_waf_ts > 0.0) and ((now_ts - last_waf_ts) < DAY_SEC)
    waf_recovery_until_ts = round(last_waf_ts + DAY_SEC) if recent_waf_recovery else 0
    peak_hours = is_peak_hours(now_ts)

    # Combined 10-minute WAF reCAPTCHA token window (video + image + upscale, excluding character & unsubmitted)
    configured_waf_cap = min(max(1, int(max_recaptcha_10m)), MAX_RECAPTCHA_HARD_CEILING)
    effective_waf_cap = (
        min(configured_waf_cap, PEAK_MAX_RECAPTCHA_10M)
        if (peak_hours or recent_waf_recovery)
        else configured_waf_cap
    )
    waf_events = [
        e for e in rolling_source_events
        if e["action"] in RECAPTCHA_ACTIONS
        and e["ts"] >= now_ts - WAF_WINDOW_SEC
        and e["recaptcha_tokens"] > 0
    ]
    waf_window_tokens = sum(e["recaptcha_tokens"] for e in waf_events)
    waf_video_tokens = sum(e["recaptcha_tokens"] for e in waf_events if e["domain"] == "video")
    waf_image_tokens = sum(e["recaptcha_tokens"] for e in waf_events if e["domain"] == "image")
    waf_pct = round((waf_window_tokens / effective_waf_cap) * 100.0, 1) if effective_waf_cap > 0 else 0.0
    waf_next_recover_ts = round(min(e["ts"] for e in waf_events) + WAF_WINDOW_SEC) if waf_events else 0
    waf_full_clear_ts = round(max(e["ts"] for e in waf_events) + WAF_WINDOW_SEC) if waf_events else 0
    waf_next_wait_sec = (
        max(0, int(math.ceil(waf_next_recover_ts - now_ts)))
        if (waf_window_tokens >= effective_waf_cap and waf_next_recover_ts > now_ts)
        else 0
    )
    waf_schedule = build_rolling_buckets(
        waf_events, WAF_WINDOW_SEC, 60.0, now_ts, max_buckets=6, count_key="recaptcha_tokens",
    )

    # Video 1h (optional; <=0 disables artificial hourly cap) & 24h rolling soft budget
    hour_events = [e for e in video_submitted if e["ts"] >= now_ts - HOUR_SEC]
    day_events = [e for e in video_submitted if e["ts"] >= now_ts - DAY_SEC]

    hour_used = len(hour_events)
    day_used = len(day_events)
    day_character_used = sum(1 for e in day_events if e["action"] == "character")
    day_credits_used = round(sum(e["credits"] for e in day_events), 1)
    hour_enabled = max_per_hour > 0

    hour_pct = round((hour_used / max_per_hour) * 100.0, 1) if hour_enabled else 0.0
    day_pct = round((day_used / max_per_day) * 100.0, 1) if max_per_day > 0 else 0.0
    day_credits_pct = round((day_credits_used / max_daily_credits) * 100.0, 1) if max_daily_credits > 0 else 0.0

    hour_next_recover_ts = round(min(e["ts"] for e in hour_events) + HOUR_SEC) if hour_events else 0
    hour_full_clear_ts = round(max(e["ts"] for e in hour_events) + HOUR_SEC) if hour_events else 0
    day_next_recover_ts = round(min(e["ts"] for e in day_events) + DAY_SEC) if day_events else 0
    day_full_clear_ts = round(max(e["ts"] for e in day_events) + DAY_SEC) if day_events else 0

    hour_schedule = build_rolling_buckets(hour_events, HOUR_SEC, 300.0, now_ts, max_buckets=6) if hour_enabled else []
    day_schedule = build_rolling_buckets(day_events, DAY_SEC, 3600.0, now_ts, max_buckets=6)

    # Cross-modal shared gap & Start-to-Start pacing
    last_event_ts = max((e["ts"] for e in submitted_events), default=0.0)
    last_submit_ts = max((e["submit_start_ts"] for e in submitted_events), default=0.0)
    min_gap_ready_ts = round(last_event_ts + min_gap_sec) if last_event_ts > 0 and min_gap_sec > 0 else 0
    min_gap_wait_sec = max(0, int(math.ceil(min_gap_ready_ts - now_ts))) if min_gap_ready_ts > now_ts else 0

    eff_img_min_gap = DEFAULT_IMAGE_WAF_RECOVERY_GAP_SEC if recent_waf_recovery else DEFAULT_IMAGE_MIN_GAP_SEC
    if peak_hours:
        eff_img_min_gap = max(eff_img_min_gap, DEFAULT_IMAGE_PEAK_GAP_SEC)
    if recent_waf_recovery:
        eff_s2s_gap = DEFAULT_IMAGE_S2S_RECOVERY_GAP_SEC
    elif peak_hours:
        eff_s2s_gap = DEFAULT_IMAGE_S2S_PEAK_GAP_SEC
    else:
        eff_s2s_gap = DEFAULT_IMAGE_S2S_GAP_SEC
    s2s_ready_ts = round(last_submit_ts + eff_s2s_gap) if last_submit_ts > 0 else 0
    s2s_wait_sec = max(0, int(math.ceil(s2s_ready_ts - now_ts))) if s2s_ready_ts > now_ts else 0
    finish_floor_ready_ts = round(last_event_ts + DEFAULT_IMAGE_FINISH_FLOOR_SEC) if last_event_ts > 0 else 0
    finish_floor_wait_sec = max(0, int(math.ceil(finish_floor_ready_ts - now_ts))) if finish_floor_ready_ts > now_ts else 0

    # Compare gflow.db video/image ops vs ledger to detect direct CLI/MCP runs
    db_video_1h = sum(1 for op in db_video_ops if op["ts"] >= now_ts - HOUR_SEC)
    db_video_24h = sum(1 for op in db_video_ops if op["ts"] >= now_ts - DAY_SEC)
    db_image_24h = sum(1 for op in db_image_ops if op["ts"] >= now_ts - DAY_SEC)
    db_image_7d = sum(1 for op in db_image_ops if op["ts"] >= now_ts - WEEK_SEC)
    db_image_30d = len(db_image_ops)
    ledger_video_24h = sum(
        1 for e in ledger_events
        if e["ts"] >= now_ts - DAY_SEC and e["domain"] == "video" and e["action"] != "character" and not e["is_local_unsubmitted"]
    )
    ledger_image_24h = sum(
        1 for e in ledger_events
        if e["ts"] >= now_ts - DAY_SEC and e["domain"] == "image" and not e["is_local_unsubmitted"]
    )
    untracked_db_24h = max(0, db_video_24h - ledger_video_24h) if rate_limit_source == "ledger" else 0
    untracked_db_image_24h = max(0, db_image_24h - ledger_image_24h) if rate_limit_source == "ledger" else 0

    # Circuit breaker & per-model image pool breakers
    breaker = compute_breaker(ledger["breaker"], now_ts)
    model_breakers = compute_model_breakers(ledger.get("model_breakers", {}), now_ts)

    # Image 0-credit daily pools & 80-submit daily heat rest (aligned with flow_traffic_guard.py)
    pool_used: dict[str, int] = {p["id"]: 0 for p in IMAGE_QUOTA_POOLS}
    pool_submits: dict[str, int] = {p["id"]: 0 for p in IMAGE_QUOTA_POOLS}
    img_day_counted = [
        e for e in rolling_source_events
        if e["domain"] == "image"
        and e["ts"] >= now_ts - DAY_SEC
        and e["outcome"].upper() in ("OK", "CONTENT_POLICY")
    ]
    for e in img_day_counted:
        pid = e["quota_pool"] or infer_image_quota_pool(e["action"], e["model"])
        if pid in pool_used:
            pool_used[pid] += max(1, safe_int(e.get("quota_units"), 1))
            pool_submits[pid] += 1
    daily_image_submits = sum(
        1 for e in submitted_events
        if e["domain"] == "image"
        and e["action"] != "image_upscale"
        and e["ts"] >= now_ts - DAY_SEC
        and e["recaptcha_tokens"] > 0
    )
    last_heat_rest_ts = safe_float(ledger.get("last_heat_rest_ts"), 0.0)
    heat_every = DEFAULT_IMAGE_HEAT_REST_EVERY
    heat_rest_due = (
        heat_every > 0
        and daily_image_submits > 0
        and (daily_image_submits % heat_every == 0)
        and ((now_ts - last_heat_rest_ts) > 600.0)
    )
    image_pools_list: list[dict[str, Any]] = []
    max_image_pool_pct = 0.0
    for meta in IMAGE_QUOTA_POOLS:
        pid = meta["id"]
        cap = int(meta["cap"])
        used = pool_used.get(pid, 0)
        pct = round((used / cap) * 100.0, 1) if cap > 0 else 0.0
        max_image_pool_pct = max(max_image_pool_pct, pct)
        image_pools_list.append({
            "id": pid,
            "label": meta["label"],
            "used": used,
            "submits": pool_submits.get(pid, 0),
            "cap": cap,
            "remaining": max(0, cap - used),
            "pct": pct,
            "unit": meta["unit"],
            "note": meta["note"],
            "breaker": model_breakers.get(pid),
        })
    image_quota_summary = {
        "total_units_24h": sum(pool_used.values()),
        "total_submits_24h": sum(pool_submits.values()),
        "daily_image_submits": daily_image_submits,
        "heat_rest_every": heat_every,
        "heat_rest_due": heat_rest_due,
        "last_heat_rest_ts": round(last_heat_rest_ts) if last_heat_rest_ts > 0 else 0,
        "model_breakers": model_breakers,
        "active_model_breaker_count": len(model_breakers),
        "pools": image_pools_list,
        "max_pool_pct": max_image_pool_pct,
        "db_image_24h": db_image_24h,
        "db_image_7d": db_image_7d,
        "db_image_30d": db_image_30d,
        "untracked_db_image_24h": untracked_db_image_24h,
    }

    # Credits & post-sync spend deduction
    synced = bool(cache.get("synced"))
    synced_at = safe_float(cache.get("synced_at"), 0.0)
    synced_total = cache.get("total_credits")
    spent_since_sync = 0.0
    if synced and synced_total is not None:
        if ledger_events:
            spent_since_sync = round(
                sum(e["credits"] for e in ledger_events if e["ts"] > synced_at and e["credits"] > 0),
                1,
            )
        elif db_video_ops:
            spent_since_sync = round(
                sum(op["credits"] for op in db_video_ops if op["ts"] > synced_at and op["credits"] > 0),
                1,
            )
        estimated_total = max(0.0, round(safe_float(synced_total, 0.0) - spent_since_sync, 1))
        spendable = max(0.0, round(estimated_total - min_remaining_credits, 1))
        below_floor = estimated_total < min_remaining_credits
    else:
        estimated_total = None
        spendable = None
        below_floor = False

    profiles = merge_profiles(
        db["profiles"],
        cache.get("profiles", []),
        cache.get("default_profile") or default_prof,
    )

    # 30d / 7d / 24h usage statistics from gflow.db + ledger
    by_model_counts: dict[str, dict[str, int]] = {}
    recent_waf_errors: list[dict[str, Any]] = []
    for op in db_video_ops:
        mid = op["model"] or "unknown"
        st = op["status"]
        rec = by_model_counts.setdefault(mid, {"ok": 0, "failed": 0, "total": 0})
        rec["total"] += 1
        if st == "succeeded":
            rec["ok"] += 1
        elif st in ("failed", "error", "canceled", "cancelled"):
            rec["failed"] += 1
        if _is_waf_error(op["error_type"], op["error_detail"]) and len(recent_waf_errors) < 5:
            recent_waf_errors.append({
                "ts": round(op["ts"]),
                "model": mid,
                "label": model_label(mid),
                "error_type": op["error_type"] or "waf",
                "detail": op["error_detail"][:160],
            })

    def _summarize_window_ops(ops: list[dict[str, Any]], cutoff: float) -> dict[str, Any]:
        sub = [o for o in ops if o["ts"] >= cutoff]
        ok_n = sum(1 for o in sub if o["status"] == "succeeded")
        fail_n = sum(1 for o in sub if o["status"] in ("failed", "error"))
        waf_n = sum(1 for o in sub if _is_waf_error(o["error_type"], o["error_detail"]))
        return {"total": len(sub), "ok": ok_n, "failed": fail_n, "waf": waf_n}

    w24_db = _summarize_window_ops(db_video_ops, now_ts - DAY_SEC)
    w7_db = _summarize_window_ops(db_video_ops, now_ts - WEEK_SEC)
    w30_db = _summarize_window_ops(db_video_ops, now_ts - MONTH_SEC)
    w7_db["character_total"] = sum(1 for o in db_char_ops if o["ts"] >= now_ts - WEEK_SEC)
    w30_db["character_total"] = sum(1 for o in db_char_ops if o["ts"] >= now_ts - MONTH_SEC)
    w7_db["image_total"] = db_image_7d
    w30_db["image_total"] = db_image_30d

    w24_ok = sum(1 for e in day_events if e["outcome"] == "OK") if rate_limit_source == "ledger" else w24_db["ok"]
    w24_fail = sum(1 for e in day_events if e["outcome"] and e["outcome"] != "OK") if rate_limit_source == "ledger" else w24_db["failed"]
    w24_waf = sum(1 for e in day_events if e["outcome"] in ("AUTH_OR_CAPTCHA", "WAF_403")) if rate_limit_source == "ledger" else w24_db["waf"]

    by_model_30d = [
        {
            "model": mid,
            "label": model_label(mid),
            "ok": c["ok"],
            "failed": c["failed"],
            "total": c["total"],
        }
        for mid, c in sorted(by_model_counts.items(), key=lambda kv: (-kv[1]["total"], kv[0]))
    ]
    by_image_counts: dict[str, dict[str, int]] = {}
    for op in db_image_ops:
        mid = op["model"] or "nano2"
        st = op["status"]
        rec = by_image_counts.setdefault(mid, {"ok": 0, "failed": 0, "total": 0})
        rec["total"] += 1
        if st == "succeeded":
            rec["ok"] += 1
        elif st in ("failed", "error", "canceled", "cancelled"):
            rec["failed"] += 1
    by_image_model_30d = [
        {
            "model": mid,
            "label": model_label(mid),
            "ok": c["ok"],
            "failed": c["failed"],
            "total": c["total"],
        }
        for mid, c in sorted(by_image_counts.items(), key=lambda kv: (-kv[1]["total"], kv[0]))
    ]

    # Build model segment estimates (active vs retiring on 2026-12-05)
    model_estimates: list[dict[str, Any]] = []
    retiring_models: list[dict[str, Any]] = []
    for m in VIDEO_MODELS:
        mid = m["id"]
        unit = safe_float(m["est_credits"], 10.0)
        mc = by_model_counts.get(mid, {"ok": 0, "failed": 0, "total": 0})
        est_seg = int(spendable // unit) if (spendable is not None and unit > 0) else None
        est_tot = int(estimated_total // unit) if (estimated_total is not None and unit > 0) else None
        item = {
            "id": mid,
            "alias": m["alias"],
            "label": m["label"],
            "unit_credits": unit,
            "cost_source": "estimated",
            "max_duration": m["max_duration"],
            "ref_cap": m["ref_cap"],
            "retires_on": m["retires_on"],
            "is_retiring": bool(m["retires_on"]),
            "est_segments": est_seg,
            "est_segments_total": est_tot,
            "recent_30d_ok": mc["ok"],
            "recent_30d_fail": mc["failed"],
        }
        if m["active"]:
            model_estimates.append(item)
        else:
            retiring_models.append(item)

    # Recent events timeline (merge ledger or fallback to gflow.db)
    recent_events: list[dict[str, Any]] = []
    if ledger_events:
        for e in reversed(ledger_events[-15:]):
            out = e["outcome"] or ("OK" if e["exit_code"] == 0 else "ERR")
            recent_events.append({
                "ts": round(e["ts"]),
                "tag": _event_tag(out, e["exit_code"]),
                "action": e["action"],
                "domain": e["domain"],
                "title": " · ".join(x for x in (e["episode"], e["segment"]) if x) or e["action"],
                "credits": e["credits"],
                "model": e["model"],
                "model_label": model_label(e["model"]) if e["model"] else "",
                "quota_pool": e["quota_pool"],
                "quota_units": e["quota_units"],
                "recaptcha_tokens": e["recaptcha_tokens"],
                "is_local_unsubmitted": e["is_local_unsubmitted"],
                "outcome": out,
                "exit_code": e["exit_code"],
            })
    elif db_video_ops or db_image_ops:
        merged_db_ops = sorted(db_video_ops + db_image_ops, key=lambda x: x["ts"], reverse=True)[:15]
        for op in merged_db_ops:
            st = op["status"]
            waf = _is_waf_error(op["error_type"], op["error_detail"])
            tag = "OK" if st == "succeeded" else ("WAF" if waf else "ERR")
            is_img = _is_image_op(op)
            recent_events.append({
                "ts": round(op["ts"]),
                "tag": tag,
                "action": op["mode"] or ("image_t2i" if is_img else "video"),
                "domain": "image" if is_img else "video",
                "title": f"{model_label(op['model'])} ({op['mode'] or op['command']})",
                "credits": 0.0 if is_img else op["credits"],
                "model": op["model"],
                "model_label": model_label(op["model"]) if op["model"] else "",
                "quota_pool": infer_image_quota_pool(op["mode"] or "image_t2i", op["model"]) if is_img else "",
                "quota_units": 1,
                "recaptcha_tokens": 1,
                "is_local_unsubmitted": False,
                "outcome": op["error_type"] or st,
                "exit_code": 0 if st == "succeeded" else 1,
            })

    # Overall availability & health status
    available = bool(gflow_bin or db["present"] or ledger["present"] or synced)
    bottleneck_pct = max(waf_pct, hour_pct if hour_enabled else 0.0, day_pct, day_credits_pct)
    if not available:
        health = "unavailable"
        status_label = "未检测到 Flow"
        status_reason = "未发现 gflow-cli、本地 gflow.db 或滚动限流账本"
    elif breaker["open"] and breaker["requires_manual_reset"]:
        health = "locked"
        status_label = "人工熔断"
        status_reason = breaker["reason"] or "已触发人工熔断，需验证并手动解锁"
    elif breaker["open"]:
        health = "locked"
        status_label = "熔断冷却"
        status_reason = breaker["reason"] or "命中限流熔断冷却中"
    elif synced and below_floor:
        health = "critical"
        status_label = "低于底线"
        status_reason = f"当前估算余额 {estimated_total:g} 低于安全底线 {min_remaining_credits:g} 积分"
    elif waf_window_tokens >= effective_waf_cap:
        health = "critical"
        status_label = "10m 满窗"
        status_reason = f"视频+生图合并 10m WAF 令牌已达上限 ({waf_window_tokens}/{effective_waf_cap})，请等待槽位回血"
    elif (hour_enabled and hour_used >= max_per_hour) or day_used >= max_per_day or day_credits_used >= max_daily_credits:
        health = "critical"
        status_label = "已达限流"
        status_reason = "已触及 24h 视频软预算或自定义小时上限，请等待滚轴回血"
    elif bottleneck_pct >= 80.0 or (synced and spendable is not None and spendable < 60.0):
        health = "warning"
        status_label = "逼近上限"
        status_reason = "10m WAF 令牌、24h 预算或可用积分接近预警线"
    elif model_breakers:
        health = "warning"
        status_label = "单池隔离"
        status_reason = f"有 {len(model_breakers)} 个生图模型池已隔离至次日 08:00"
    elif heat_rest_due:
        health = "warning"
        status_label = "建议大休"
        status_reason = f"24h 生图提交已满 {daily_image_submits} 次（每 {heat_every} 次建议深休 20–30 分钟）"
    else:
        health = "ok"
        status_label = "正常" if synced else "待同步"
        status_reason = ""

    report: dict[str, Any] = {
        "version": REPORT_VERSION,
        "generated_at": round(now_ts),
        "available": available,
        "gflow_installed": bool(gflow_bin),
        "gflow_bin": gflow_bin,
        "data_sources": {
            "ledger_present": ledger["present"],
            "ledger_version": ledger.get("version", 1),
            "ledger_path": str(ledger_path),
            "db_present": db["present"],
            "db_path": db["path"],
            "credits_cache_present": cache["present"],
            "rate_limit_source": rate_limit_source,
        },
        "health": health,
        "status_label": status_label,
        "status_reason": status_reason,
        "policy": {
            "waf_window_sec": int(WAF_WINDOW_SEC),
            "max_recaptcha_10m": configured_waf_cap,
            "effective_max_recaptcha_10m": effective_waf_cap,
            "peak_max_recaptcha_10m": PEAK_MAX_RECAPTCHA_10M,
            "max_per_hour": max_per_hour,
            "max_per_day": max_per_day,
            "max_daily_credits": max_daily_credits,
            "min_remaining_credits": min_remaining_credits,
            "min_gap_sec": min_gap_sec,
            "effective_image_min_gap_sec": eff_img_min_gap,
            "effective_s2s_gap_sec": eff_s2s_gap,
        },
        "locks": {
            "profile_busy": busy,
            "profile_detail": busy_detail,
            "single_flight": sf_lock,
        },
        "credits": {
            "synced": synced,
            "synced_at": round(synced_at) if synced_at > 0 else 0,
            "synced_total": round(safe_float(synced_total, 0.0), 1) if synced_total is not None else None,
            "spent_since_sync": spent_since_sync,
            "estimated_total": estimated_total,
            "reserve_floor": min_remaining_credits,
            "spendable": spendable,
            "below_floor": below_floor,
            "default_profile": cache.get("default_profile") or default_prof,
            "profiles": profiles,
            "history": cache.get("history", [])[-12:],
            "last_error": cache.get("last_error", ""),
            "last_error_at": round(safe_float(cache.get("last_error_at"), 0.0)),
            "profile_busy": effective_busy,
            "busy_detail": effective_busy_detail,
        },
        "model_estimates": model_estimates,
        "retiring_models": retiring_models,
        "rolling": {
            "waf_window_sec": int(WAF_WINDOW_SEC),
            "waf_window_tokens": waf_window_tokens,
            "waf_video_tokens": waf_video_tokens,
            "waf_image_tokens": waf_image_tokens,
            "waf_configured_limit": configured_waf_cap,
            "waf_effective_limit": effective_waf_cap,
            "waf_remaining": max(0, effective_waf_cap - waf_window_tokens),
            "waf_pct": waf_pct,
            "waf_next_recover_ts": waf_next_recover_ts,
            "waf_full_clear_ts": waf_full_clear_ts,
            "waf_next_wait_sec": waf_next_wait_sec,
            "waf_schedule": waf_schedule,
            "is_peak_hours": peak_hours,
            "recent_waf_recovery": recent_waf_recovery,
            "last_waf_ts": round(last_waf_ts) if last_waf_ts > 0 else 0,
            "waf_recovery_until_ts": waf_recovery_until_ts,
            "hour_enabled": hour_enabled,
            "hour_used": hour_used,
            "hour_limit": max_per_hour,
            "hour_remaining": max(0, max_per_hour - hour_used) if hour_enabled else None,
            "hour_pct": hour_pct,
            "hour_next_recover_ts": hour_next_recover_ts,
            "hour_full_clear_ts": hour_full_clear_ts,
            "hour_schedule": hour_schedule,
            "day_used": day_used,
            "day_character_used": day_character_used,
            "day_limit": max_per_day,
            "day_remaining": max(0, max_per_day - day_used),
            "day_pct": day_pct,
            "day_credits_used": day_credits_used,
            "day_credits_limit": max_daily_credits,
            "day_credits_remaining": max(0.0, round(max_daily_credits - day_credits_used, 1)),
            "day_credits_pct": day_credits_pct,
            "day_next_recover_ts": day_next_recover_ts,
            "day_full_clear_ts": day_full_clear_ts,
            "day_schedule": day_schedule,
            "bottleneck_pct": bottleneck_pct,
            "last_event_ts": round(last_event_ts) if last_event_ts > 0 else 0,
            "last_submit_ts": round(last_submit_ts) if last_submit_ts > 0 else 0,
            "min_gap_ready_ts": min_gap_ready_ts,
            "min_gap_wait_sec": min_gap_wait_sec,
            "effective_s2s_gap_sec": eff_s2s_gap,
            "s2s_ready_ts": s2s_ready_ts,
            "s2s_wait_sec": s2s_wait_sec,
            "finish_floor_ready_ts": finish_floor_ready_ts,
            "finish_floor_wait_sec": finish_floor_wait_sec,
            "db_video_1h": db_video_1h,
            "db_video_24h": db_video_24h,
            "db_image_24h": db_image_24h,
            "untracked_db_24h": untracked_db_24h,
            "untracked_db_image_24h": untracked_db_image_24h,
        },
        "image_quota": image_quota_summary,
        "breaker": breaker,
        "model_breakers": model_breakers,
        "usage": {
            "window_24h": {
                "total": day_used,
                "ok": w24_ok,
                "failed": w24_fail,
                "waf": w24_waf,
                "credits": day_credits_used,
                "image_units": image_quota_summary["total_units_24h"],
                "image_submits": daily_image_submits,
            },
            "window_7d": w7_db,
            "window_30d": w30_db,
            "by_model_30d": by_model_30d,
            "by_image_model_30d": by_image_model_30d,
            "recent_waf_errors": recent_waf_errors,
        },
        "workspace_runs": workspace_runs,
        "recent_events": recent_events,
    }
    if sync_result is not None:
        report["sync_result"] = sync_result
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Google Flow cross-modal quota & rate-limit reporter")
    parser.add_argument("--ledger", default=str(default_ledger_path()))
    parser.add_argument("--gflow-dir", default=str(default_gflow_data_dir()))
    parser.add_argument("--credits-cache", required=True)
    parser.add_argument("--workspace", default="")
    parser.add_argument("--gflow-bin", default="")
    parser.add_argument("--max-recaptcha-10m", type=int, default=DEFAULT_MAX_RECAPTCHA_10M)
    parser.add_argument("--max-per-hour", type=int, default=DEFAULT_MAX_PER_HOUR)
    parser.add_argument("--max-per-day", type=int, default=DEFAULT_MAX_PER_DAY)
    parser.add_argument("--max-daily-credits", type=float, default=DEFAULT_MAX_DAILY_CREDITS)
    parser.add_argument("--min-remaining-credits", type=float, default=DEFAULT_MIN_REMAINING_CREDITS)
    parser.add_argument("--min-gap-sec", type=float, default=DEFAULT_MIN_GAP_SEC)
    parser.add_argument("--now", type=float, default=None)
    parser.add_argument("--sync-credits", action="store_true")
    parser.add_argument("--force-sync", action="store_true")
    parser.add_argument("--sync-timeout", type=int, default=45)
    args = parser.parse_args(argv)

    now_ts = float(args.now) if args.now is not None else time.time()
    ledger_path = Path(args.ledger)
    gflow_dir = Path(args.gflow_dir)
    cache_path = Path(args.credits_cache)

    sync_res = None
    if args.sync_credits:
        gbin = _resolve_gflow_bin(args.gflow_bin)
        sync_res = sync_live_credits(
            gflow_bin=gbin,
            gflow_dir=gflow_dir,
            cache_path=cache_path,
            now=now_ts,
            force_ignore_busy=args.force_sync,
            timeout_sec=max(10, args.sync_timeout),
        )

    report = build_report(
        ledger_path=ledger_path,
        gflow_dir=gflow_dir,
        credits_cache_path=cache_path,
        workspace=args.workspace,
        max_per_hour=max(0, args.max_per_hour),
        max_per_day=max(1, args.max_per_day),
        max_daily_credits=max(1.0, args.max_daily_credits),
        min_remaining_credits=max(0.0, args.min_remaining_credits),
        min_gap_sec=max(0.0, args.min_gap_sec),
        max_recaptcha_10m=min(MAX_RECAPTCHA_HARD_CEILING, max(1, args.max_recaptcha_10m)),
        now=now_ts,
        gflow_bin_override=args.gflow_bin,
        sync_result=sync_res,
    )
    sys.stdout.write(json.dumps(report, ensure_ascii=False))
    return 0
