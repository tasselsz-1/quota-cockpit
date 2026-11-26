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
    DAY_SEC,
    DEFAULT_MAX_DAILY_CREDITS,
    DEFAULT_MAX_PER_DAY,
    DEFAULT_MAX_PER_HOUR,
    DEFAULT_MIN_GAP_SEC,
    DEFAULT_MIN_REMAINING_CREDITS,
    HOUR_SEC,
    LEDGER_COUNTED_ACTIONS,
    MODEL_BY_ID,
    MONTH_SEC,
    REPORT_VERSION,
    VIDEO_MODELS,
    WEEK_SEC,
    default_gflow_data_dir,
    default_ledger_path,
    find_gflow_bin,
    model_label,
    parse_iso_ts,
    safe_float,
    safe_int,
)
from .readers import (
    inspect_profile_locks,
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
        slot["count"] += 1
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


def _is_waf_error(err_type: str, err_detail: str) -> bool:
    hay = f"{err_type} {err_detail}".lower()
    return any(k in hay for k in ("waf", "unusual_activity", "captcha", "grpc 7"))


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
    now: float | None = None,
    gflow_bin_override: str = "",
    sync_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now_ts = float(now) if now is not None else time.time()
    gflow_bin = _resolve_gflow_bin(gflow_bin_override)
    default_prof = read_default_profile_from_config(gflow_dir)
    busy, busy_detail = inspect_profile_locks(gflow_dir, now_ts)

    ledger = read_ledger(ledger_path)
    db = read_gflow_db(gflow_dir, now_ts)
    cache = read_credits_cache(credits_cache_path)
    workspace_runs = scan_workspace_flow_runs(workspace)

    # Filter ledger events (matching flow_traffic_guard.py semantics)
    ledger_events: list[dict[str, Any]] = []
    for ev in ledger["events"]:
        act = str(ev.get("action") or "").lower()
        ts = safe_float(ev.get("ts"), 0.0) or parse_iso_ts(ev.get("iso"))
        if ts <= 0 or act not in LEDGER_COUNTED_ACTIONS:
            continue
        ledger_events.append({
            "ts": ts,
            "action": act,
            "episode": str(ev.get("episode") or ""),
            "segment": str(ev.get("segment") or ""),
            "credits": max(0.0, safe_float(ev.get("credits"), 0.0)),
            "outcome": str(ev.get("outcome") or ""),
            "exit_code": safe_int(ev.get("exit_code"), 0),
            "source": "ledger",
        })
    ledger_events.sort(key=lambda x: x["ts"])

    # Filter gflow.db operations
    db_ops = db["operations"]
    db_video_ops = [op for op in db_ops if _is_video_op(op)]
    db_char_ops = [op for op in db_ops if str(op.get("command") or "").lower().startswith("character")]

    # Determine rolling rate-limit source
    if ledger["present"] and not ledger.get("parse_error"):
        rate_limit_source = "ledger"
        rolling_source_events = ledger_events
    elif db["present"]:
        rate_limit_source = "gflow_db"
        rolling_source_events = [
            {
                "ts": op["ts"],
                "action": op["mode"] or "video",
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
                "model": op["model"],
                "source": "gflow_db",
            }
            for op in sorted(db_video_ops, key=lambda x: x["ts"])
        ]
    else:
        rate_limit_source = "none"
        rolling_source_events = []

    hour_events = [e for e in rolling_source_events if e["ts"] >= now_ts - HOUR_SEC]
    day_events = [e for e in rolling_source_events if e["ts"] >= now_ts - DAY_SEC]

    hour_used = len(hour_events)
    day_used = len(day_events)
    day_credits_used = round(sum(e["credits"] for e in day_events), 1)

    hour_pct = round((hour_used / max_per_hour) * 100.0, 1) if max_per_hour > 0 else 0.0
    day_pct = round((day_used / max_per_day) * 100.0, 1) if max_per_day > 0 else 0.0
    day_credits_pct = round((day_credits_used / max_daily_credits) * 100.0, 1) if max_daily_credits > 0 else 0.0

    hour_next_recover_ts = round(min(e["ts"] for e in hour_events) + HOUR_SEC) if hour_events else 0
    hour_full_clear_ts = round(max(e["ts"] for e in hour_events) + HOUR_SEC) if hour_events else 0
    day_next_recover_ts = round(min(e["ts"] for e in day_events) + DAY_SEC) if day_events else 0
    day_full_clear_ts = round(max(e["ts"] for e in day_events) + DAY_SEC) if day_events else 0

    hour_schedule = build_rolling_buckets(hour_events, HOUR_SEC, 300.0, now_ts, max_buckets=6)
    day_schedule = build_rolling_buckets(day_events, DAY_SEC, 3600.0, now_ts, max_buckets=6)

    last_event_ts = max((e["ts"] for e in rolling_source_events), default=0.0)
    min_gap_ready_ts = round(last_event_ts + min_gap_sec) if last_event_ts > 0 and min_gap_sec > 0 else 0
    min_gap_wait_sec = max(0, int(math.ceil(min_gap_ready_ts - now_ts))) if min_gap_ready_ts > now_ts else 0

    # Compare gflow.db video ops vs ledger video ops to detect direct CLI/MCP runs
    db_video_1h = sum(1 for op in db_video_ops if op["ts"] >= now_ts - HOUR_SEC)
    db_video_24h = sum(1 for op in db_video_ops if op["ts"] >= now_ts - DAY_SEC)
    ledger_video_24h = sum(1 for e in ledger_events if e["ts"] >= now_ts - DAY_SEC and e["action"] != "character")
    untracked_db_24h = max(0, db_video_24h - ledger_video_24h) if rate_limit_source == "ledger" else 0

    # Circuit breaker
    breaker = compute_breaker(ledger["breaker"], now_ts)

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

    w24_ok = sum(1 for e in day_events if e["outcome"] == "OK") if rate_limit_source == "ledger" else w24_db["ok"]
    w24_fail = sum(1 for e in day_events if e["outcome"] and e["outcome"] != "OK") if rate_limit_source == "ledger" else w24_db["failed"]
    w24_waf = sum(1 for e in day_events if e["outcome"] == "AUTH_OR_CAPTCHA") if rate_limit_source == "ledger" else w24_db["waf"]

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
            tag = "OK" if out == "OK" else (
                "WAF" if out == "AUTH_OR_CAPTCHA" else (
                    "429" if out == "RATE_LIMIT_OR_QUOTA" else (
                        "CAP" if out == "CREDIT_GUARD_ABORT" else "ERR"
                    )
                )
            )
            recent_events.append({
                "ts": round(e["ts"]),
                "tag": tag,
                "action": e["action"],
                "title": " · ".join(x for x in (e["episode"], e["segment"]) if x) or e["action"],
                "credits": e["credits"],
                "outcome": out,
                "exit_code": e["exit_code"],
            })
    elif db_video_ops:
        for op in db_video_ops[:15]:
            st = op["status"]
            waf = _is_waf_error(op["error_type"], op["error_detail"])
            tag = "OK" if st == "succeeded" else ("WAF" if waf else "ERR")
            recent_events.append({
                "ts": round(op["ts"]),
                "tag": tag,
                "action": op["mode"] or "video",
                "title": f"{model_label(op['model'])} ({op['mode'] or op['command']})",
                "credits": op["credits"],
                "outcome": op["error_type"] or st,
                "exit_code": 0 if st == "succeeded" else 1,
            })

    # Overall availability & health status
    available = bool(gflow_bin or db["present"] or ledger["present"] or synced)
    bottleneck_pct = max(hour_pct, day_pct, day_credits_pct)
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
    elif hour_used >= max_per_hour or day_used >= max_per_day or day_credits_used >= max_daily_credits:
        health = "critical"
        status_label = "已达限流"
        status_reason = "已触及 1h/24h 防风控限流上限，请等待滚轴回血"
    elif bottleneck_pct >= 80.0 or (synced and spendable is not None and spendable < 60.0):
        health = "warning"
        status_label = "逼近上限"
        status_reason = "防限流水位或可用积分接近预警线"
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
            "max_per_hour": max_per_hour,
            "max_per_day": max_per_day,
            "max_daily_credits": max_daily_credits,
            "min_remaining_credits": min_remaining_credits,
            "min_gap_sec": min_gap_sec,
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
            "profile_busy": busy,
            "busy_detail": busy_detail,
        },
        "model_estimates": model_estimates,
        "retiring_models": retiring_models,
        "rolling": {
            "hour_used": hour_used,
            "hour_limit": max_per_hour,
            "hour_remaining": max(0, max_per_hour - hour_used),
            "hour_pct": hour_pct,
            "hour_next_recover_ts": hour_next_recover_ts,
            "hour_full_clear_ts": hour_full_clear_ts,
            "hour_schedule": hour_schedule,
            "day_used": day_used,
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
            "min_gap_ready_ts": min_gap_ready_ts,
            "min_gap_wait_sec": min_gap_wait_sec,
            "db_video_1h": db_video_1h,
            "db_video_24h": db_video_24h,
            "untracked_db_24h": untracked_db_24h,
        },
        "breaker": breaker,
        "usage": {
            "window_24h": {
                "total": day_used,
                "ok": w24_ok,
                "failed": w24_fail,
                "waf": w24_waf,
                "credits": day_credits_used,
            },
            "window_7d": w7_db,
            "window_30d": w30_db,
            "by_model_30d": by_model_30d,
            "recent_waf_errors": recent_waf_errors,
        },
        "workspace_runs": workspace_runs,
        "recent_events": recent_events,
    }
    if sync_result is not None:
        report["sync_result"] = sync_result
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Google Flow video quota & rate-limit reporter")
    parser.add_argument("--ledger", default=str(default_ledger_path()))
    parser.add_argument("--gflow-dir", default=str(default_gflow_data_dir()))
    parser.add_argument("--credits-cache", required=True)
    parser.add_argument("--workspace", default="")
    parser.add_argument("--gflow-bin", default="")
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
        max_per_hour=max(1, args.max_per_hour),
        max_per_day=max(1, args.max_per_day),
        max_daily_credits=max(1.0, args.max_daily_credits),
        min_remaining_credits=max(0.0, args.min_remaining_credits),
        min_gap_sec=max(0.0, args.min_gap_sec),
        now=now_ts,
        gflow_bin_override=args.gflow_bin,
        sync_result=sync_res,
    )
    sys.stdout.write(json.dumps(report, ensure_ascii=False))
    return 0
