"""Workspace ledger scan, window anchor, report assembly, and CLI entrypoint."""

import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

from .calibration import (
    compute_calibration,
    merge_calibration_with_ledger,
    pin_calibration_peak,
    reset_calibration_ledger,
)
from .constants import (
    BILLABLE_KINDS,
    DEFAULT_HARD_CAP,
    DEFAULT_SOFT_CAP,
    OUTPUT_1K_IMAGE_TOKENS,
    SKIP_DIRS,
    THINKING_MINIMAL_TOKENS,
    WINDOW_24H_SEC,
    WINDOW_30D_SEC,
    WINDOW_5H_SEC,
    WINDOW_7D_SEC,
)
from .scanner import scan_conversations
from .schedules import (
    build_rolling_schedule,
    build_weekly_rolling_schedule,
    compute_per_image_avg,
    summarize_bucket,
)

def scan_workspace_ledgers(workspace: str | None, now: float, max_depth: int = 6) -> list[dict[str, Any]]:
    """Find and summarize optional `api_call_ledger.json` files inside the active workspace."""
    if not workspace:
        return []
    root = Path(workspace)
    if not root.is_dir():
        return []

    found_paths: list[Path] = []
    root_depth = len(root.parts)

    for dirpath, dirnames, filenames in os.walk(root):
        cur = Path(dirpath)
        depth = len(cur.parts) - root_depth
        # Prune heavy or irrelevant directories
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS and not d.startswith(".")
        ]
        if depth >= max_depth:
            dirnames[:] = []
        if "api_call_ledger.json" in filenames:
            found_paths.append(cur / "api_call_ledger.json")

    ledgers: list[dict[str, Any]] = []
    for p in sorted(found_paths, key=lambda x: x.stat().st_mtime if x.is_file() else 0.0, reverse=True)[:8]:
        try:
            raw = json.loads(p.read_text(encoding="utf-8-sig"))
            if not isinstance(raw, dict):
                continue
        except (OSError, json.JSONDecodeError):
            continue

        rel = str(p.relative_to(root)).replace("\\", "/")
        global_cd = float(raw.get("global_cooldown_until") or 0.0)
        wins_raw = raw.get("windows") if isinstance(raw.get("windows"), dict) else {}
        win_items: list[dict[str, Any]] = []
        for wid, wobj in sorted(wins_raw.items()):
            if not isinstance(wobj, dict):
                continue
            rsvs = wobj.get("reservations") if isinstance(wobj.get("reservations"), list) else []
            states: dict[str, int] = {"OK": 0, "429": 0, "TIMEOUT": 0, "ERROR": 0, "PENDING": 0}
            for r in rsvs:
                if not isinstance(r, dict):
                    continue
                st = str(r.get("state") or r.get("status") or "").strip().upper()
                if st in states:
                    states[st] += 1
            cd_until = max(global_cd, float(wobj.get("cooldown_until") or 0.0))
            hard_cap = int(wobj.get("hard_cap") or DEFAULT_HARD_CAP)
            used_calls = int(wobj.get("used_calls") if wobj.get("used_calls") is not None else (states["OK"] + states["429"] + states["TIMEOUT"] + states["ERROR"]))
            pending_calls = int(wobj.get("pending_calls") if wobj.get("pending_calls") is not None else states["PENDING"])
            win_items.append({
                "window": str(wid),
                "used_calls": used_calls,
                "pending_calls": pending_calls,
                "hard_cap": hard_cap,
                "remaining_calls": max(0, hard_cap - used_calls - pending_calls),
                "cooldown_until": round(cd_until, 3),
                "cooldown_remaining_sec": round(max(0.0, cd_until - now), 1),
                "states": states,
            })

        ledgers.append({
            "path": rel,
            "episode_label": p.parent.name,
            "schema_version": str(raw.get("schema_version") or ""),
            "updated_at": round(p.stat().st_mtime, 3),
            "global_cooldown_until": round(global_cd, 3),
            "global_cooldown_remaining_sec": round(max(0.0, global_cd - now), 1),
            "windows": win_items,
        })

    return ledgers


def compute_anchor(
    anchor_mode: str,
    count_mode: str,
    w5: dict[str, Any],
    cooldown_active: bool,
    calibration: dict[str, Any],
    per_image_avg: dict[str, Any],
    soft_cap: int,
    hard_cap: int,
    token_cap: int,
) -> dict[str, Any]:
    """Compute self-learning watermark band, estimated ceiling, and with-ref remaining image capacity."""
    metric_used = int(w5["ok"] if count_mode == "ok_only" else w5["attempts"])
    tokens_used = int(w5["tokens_total"])
    avg_tok = float(per_image_avg.get("avg_tokens_total") or (OUTPUT_1K_IMAGE_TOKENS + THINKING_MINIMAL_TOKENS + 480))
    wr_info = per_image_avg.get("with_refs") or {}
    avg_wr_tok = float(wr_info.get("avg_tokens_total") or avg_tok)
    peak = calibration.get("observed_safe_peak") or {}
    prior_peak = calibration.get("prior_safe_peak") or peak
    prior_calls = int(prior_peak.get("max_ok") if count_mode == "ok_only" else prior_peak.get("max_attempts") or 0)
    prior_tokens = int(prior_peak.get("max_tokens_total") or 0)
    all_calls = int(peak.get("max_ok") if count_mode == "ok_only" else peak.get("max_attempts") or 0)
    all_tokens = int(peak.get("max_tokens_total") or 0)

    # Always adopt the latest 30d proven-safe maximum so newly proven peaks immediately update the ceiling;
    # keep prior_safe_* strictly to detect whether the active window just surpassed pre-window history.
    safe_lower_calls = max(all_calls, prior_calls)
    safe_lower_tokens = max(all_tokens, prior_tokens)

    clean_count = int(calibration.get("clean_lock_count") or 0)
    obs_count = int(calibration.get("observed_lock_count") or 0)
    if clean_count >= 1:
        lock_upper_calls = int(round(
            calibration["clean_avg_ok_at_lock"] if count_mode == "ok_only" else calibration["clean_avg_attempts_at_lock"]
        ))
        lock_upper_tokens = int(calibration.get("clean_avg_tokens_at_lock") or 0)
        lock_upper_is_noisy = False
    elif obs_count >= 1:
        lock_upper_calls = int(round(
            calibration["avg_ok_at_lock"] if count_mode == "ok_only" else calibration["avg_attempts_at_lock"]
        ))
        lock_upper_tokens = int(calibration.get("avg_tokens_at_lock") or 0)
        lock_upper_is_noisy = True
    else:
        lock_upper_calls = None
        lock_upper_tokens = None
        lock_upper_is_noisy = False

    pinned_info = calibration.get("pinned_peak") if isinstance(calibration.get("pinned_peak"), dict) else None
    pinned_calls = int(pinned_info.get("calls") or 0) if pinned_info else 0
    pinned_tokens = int(pinned_info.get("tokens_total") or 0) if pinned_info else 0

    notes: list[str] = []
    exceeded_safe_peak = False
    is_hard_ceiling = True
    ref_tokens_source = "default_baseline"
    used_pct_basis = "calls"

    if anchor_mode == "manual":
        source = "manual"
        label = "手动阈值"
        ref_calls = max(1, int(hard_cap))
        soft_calls = max(1, min(ref_calls, int(soft_cap)))
        if int(token_cap) > 0:
            ref_tokens = int(token_cap)
            ref_tokens_source = "manual_token_cap"
        else:
            ref_tokens = int(round(ref_calls * avg_wr_tok))
            ref_tokens_source = "manual_calls_x_with_refs"
        raw_calls_pct = round(metric_used / ref_calls * 100.0, 1) if ref_calls > 0 else 0.0
        raw_tokens_pct = round(tokens_used / ref_tokens * 100.0, 1) if ref_tokens > 0 else 0.0
        if int(token_cap) > 0:
            used_pct = round(min(100.0, max(raw_calls_pct, raw_tokens_pct)), 1)
            used_pct_basis = "tokens" if raw_tokens_pct >= raw_calls_pct else "calls"
        else:
            used_pct = round(min(100.0, raw_calls_pct), 1)
            used_pct_basis = "calls"
        remaining_calls = max(0, ref_calls - metric_used)
        remaining_tokens = max(0, ref_tokens - tokens_used)
        if cooldown_active:
            health = "locked"
        elif metric_used >= ref_calls or (int(token_cap) > 0 and tokens_used >= int(token_cap)):
            health = "critical"
        elif metric_used >= soft_calls or used_pct >= 80.0:
            health = "warning"
        else:
            health = "ok"
    elif pinned_calls >= 1:
        source = "pinned_peak"
        label = "固定峰值上限（带垫图折算）"
        is_hard_ceiling = True
        ref_calls = pinned_calls
        soft_calls = max(1, int(round(ref_calls * 0.85)))
        est_tokens_from_calls = int(round(ref_calls * avg_wr_tok))
        if pinned_tokens > 0 and pinned_tokens >= est_tokens_from_calls:
            ref_tokens = pinned_tokens
            ref_tokens_source = "pinned_tokens"
        else:
            ref_tokens = max(1, est_tokens_from_calls)
            ref_tokens_source = "pinned_calls_x_with_refs"
        raw_calls_pct = round(metric_used / ref_calls * 100.0, 1)
        raw_tokens_pct = round(tokens_used / ref_tokens * 100.0, 1)
        used_pct = round(min(100.0, max(raw_calls_pct, raw_tokens_pct)), 1)
        used_pct_basis = "tokens" if raw_tokens_pct >= raw_calls_pct else "calls"
        exceeded_safe_peak = metric_used > ref_calls
        remaining_calls = max(0, ref_calls - metric_used)
        remaining_tokens = max(0, ref_tokens - tokens_used)
        if cooldown_active:
            health = "locked"
        elif metric_used >= ref_calls or tokens_used >= ref_tokens:
            health = "critical"
        elif metric_used >= soft_calls or used_pct >= 85.0:
            health = "warning"
        else:
            health = "ok"
        notes.append(
            f"已固定 5h 峰值上限为 {ref_calls} 次（按带垫图均图 {avg_wr_tok / 1000.0:.2f}k tok 折算 ~{ref_tokens / 1000.0:.1f}k tok），不受旧对话清理或时间滚出影响"
        )
        if safe_lower_calls > ref_calls:
            notes.append(f"另实测到更高 0-429 安全峰 {safe_lower_calls} 次，可在面板一键更新固定峰值")
    elif clean_count >= 1 and (
        safe_lower_calls < 10
        or (lock_upper_calls is not None and lock_upper_calls >= safe_lower_calls)
    ):
        source = "calibrated_429"
        label = "实测 429 上限校准"
        ref_calls = max(1, int(lock_upper_calls or hard_cap))
        soft_calls = max(1, int(round(ref_calls * 0.85)))
        ref_tokens = max(1, int(lock_upper_tokens or round(ref_calls * avg_wr_tok)), safe_lower_tokens)
        ref_tokens_source = "calibrated_429"
        raw_calls_pct = round(metric_used / ref_calls * 100.0, 1)
        raw_tokens_pct = round(tokens_used / ref_tokens * 100.0, 1)
        used_pct = round(min(100.0, max(raw_calls_pct, raw_tokens_pct)), 1)
        used_pct_basis = "tokens" if raw_tokens_pct >= raw_calls_pct else "calls"
        remaining_calls = max(0, ref_calls - metric_used)
        remaining_tokens = max(0, ref_tokens - tokens_used)
        if cooldown_active:
            health = "locked"
        elif metric_used >= ref_calls or tokens_used >= ref_tokens:
            health = "critical"
        elif metric_used >= soft_calls or used_pct >= 85.0:
            health = "warning"
        else:
            health = "ok"
        notes.append(f"已基于 {clean_count} 次干净 5h 429 实测锁定自动校准上限")
    elif safe_lower_calls >= 10:
        source = "safe_watermark"
        label = "自学习预估上限（带垫图折算）"
        is_hard_ceiling = False
        ref_calls = safe_lower_calls
        soft_calls = max(1, int(round(ref_calls * 0.85)))
        est_tokens_from_calls = int(round(safe_lower_calls * avg_wr_tok))
        if safe_lower_tokens >= est_tokens_from_calls:
            ref_tokens = max(1, safe_lower_tokens)
            ref_tokens_source = "observed_safe_tokens"
        else:
            ref_tokens = max(1, est_tokens_from_calls)
            ref_tokens_source = "calls_x_with_refs"
        raw_calls_pct = round(metric_used / ref_calls * 100.0, 1)
        raw_tokens_pct = round(tokens_used / ref_tokens * 100.0, 1)
        used_pct = round(min(100.0, max(raw_calls_pct, raw_tokens_pct)), 1)
        used_pct_basis = "tokens" if raw_tokens_pct >= raw_calls_pct else "calls"
        exceeded_safe_peak = (
            prior_calls >= 10
            and (metric_used > prior_calls or (prior_tokens > 0 and tokens_used > prior_tokens))
        )
        remaining_calls = max(0, ref_calls - metric_used)
        remaining_tokens = max(0, ref_tokens - tokens_used)
        if cooldown_active:
            health = "locked"
        elif exceeded_safe_peak or raw_calls_pct >= 90.0 or raw_tokens_pct >= 90.0:
            health = "warning"
        else:
            health = "ok"
        if ref_tokens_source == "calls_x_with_refs":
            notes.append(
                f"预估 5h 上限按 30 天已证安全峰 {safe_lower_calls} 次 × 带垫图均图 {avg_wr_tok / 1000.0:.2f}k tok "
                f"推算为 ~{ref_tokens / 1000.0:.1f}k tok（实测 0-429 Token 峰 {safe_lower_tokens / 1000.0:.1f}k）"
            )
        else:
            notes.append(
                f"预估 5h 上限锚定近 30 天实测 0-429 安全峰值 {safe_lower_calls} 次 / {safe_lower_tokens / 1000.0:.1f}k tok"
            )
        if exceeded_safe_peak:
            notes.append("当前滑动 5h 用量已刷新 5h 前历史安全峰，继续出图可自然探索更高边界")
        elif clean_count >= 1 and lock_upper_calls is not None and lock_upper_calls < safe_lower_calls:
            notes.append(f"历史 429 观测（{lock_upper_calls} 次）低于已证安全峰（{safe_lower_calls} 次），可能受配额变动或跨端调用影响，当前锚定实证安全上限")
        elif lock_upper_is_noisy and lock_upper_calls:
            latest_lock = calibration.get("latest_lock") or {}
            if latest_lock.get("is_truncated"):
                notes.append(f"最近 5h 429 窗口缺少开头时段记录（实见 {lock_upper_calls} 次，可能因旧对话清理或跨端调用），未作为硬天花板")
            else:
                notes.append(f"历史上曾记录高 EOF 噪声锁（{lock_upper_calls} 次），未作为硬天花板")
        if calibration.get("persisted_safe_peak_used"):
            notes.append("已由永久校准账本锁定历史安全峰，旧对话自动清理不会回退上限")
    else:
        source = "default_baseline"
        label = "默认基准（样本积累中）"
        ref_calls = max(1, int(hard_cap))
        soft_calls = max(1, min(ref_calls, int(soft_cap)))
        ref_tokens = int(round(ref_calls * avg_wr_tok))
        ref_tokens_source = "default_baseline"
        raw_calls_pct = round(metric_used / ref_calls * 100.0, 1) if ref_calls > 0 else 0.0
        raw_tokens_pct = round(tokens_used / ref_tokens * 100.0, 1) if ref_tokens > 0 else 0.0
        used_pct = round(min(100.0, raw_calls_pct), 1)
        used_pct_basis = "calls"
        remaining_calls = max(0, ref_calls - metric_used)
        remaining_tokens = max(0, ref_tokens - tokens_used)
        if cooldown_active:
            health = "locked"
        elif metric_used >= ref_calls:
            health = "critical"
        elif metric_used >= soft_calls or used_pct >= 80.0:
            health = "warning"
        else:
            health = "ok"
        notes.append("近 30 天样本不足 10 次，暂按默认基准（54/60 次）展示")

    if cooldown_active:
        used_pct = 100.0
        used_pct_basis = "locked"
        remaining_calls = 0
        remaining_tokens = 0
        health = "locked"

    eof_warning = (not cooldown_active and int(w5["eof"]) >= 3 and float(w5["eof_rate_pct"]) >= 20.0)
    if eof_warning:
        if health == "ok":
            health = "warning"
        notes.append(f"近 5h EOF 率达 {w5['eof_rate_pct']}%（{w5['eof']} 次），断连重试可能放大隐性配额消耗")

    if cooldown_active:
        status = "locked"
    elif exceeded_safe_peak:
        status = "exceeded_safe_peak"
    elif health == "critical":
        status = "near_ceiling"
    elif eof_warning:
        status = "eof_elevated"
    elif health == "warning":
        status = "near_watermark"
    else:
        status = "ok"

    est_rem_imgs_by_tok = int(remaining_tokens // avg_tok) if (avg_tok > 0 and remaining_tokens > 0) else 0
    est_rem_imgs_wr = int(remaining_tokens // avg_wr_tok) if (avg_wr_tok > 0 and remaining_tokens > 0) else 0
    est_rem_imgs_wr_exact = round(remaining_tokens / avg_wr_tok, 1) if (avg_wr_tok > 0 and remaining_tokens > 0) else 0.0
    est_rem_by_refs: dict[str, int] = {}
    for g in per_image_avg.get("by_refs") or []:
        gtok = float(g.get("avg_tokens_total") or 0)
        if gtok > 0:
            est_rem_by_refs[str(g["label"])] = int(remaining_tokens // gtok) if remaining_tokens > 0 else 0
    safe_tokens_headroom = 0 if cooldown_active else max(0, safe_lower_tokens - tokens_used)

    return {
        "mode": anchor_mode,
        "source": source,
        "label": label,
        "status": status,
        "health": health,
        "is_hard_ceiling": is_hard_ceiling,
        "exceeded_safe_peak": exceeded_safe_peak,
        "eof_warning": eof_warning,
        "ref_calls": ref_calls,
        "soft_calls": soft_calls,
        "ref_tokens": ref_tokens,
        "ref_tokens_source": ref_tokens_source,
        "metric_used": metric_used,
        "tokens_used": tokens_used,
        "calls_pct": raw_calls_pct,
        "tokens_pct": raw_tokens_pct,
        "used_pct": used_pct,
        "used_pct_basis": used_pct_basis,
        "remaining_calls": remaining_calls,
        "remaining_tokens": remaining_tokens,
        "safe_tokens_headroom": safe_tokens_headroom,
        "avg_with_refs_tokens": int(round(avg_wr_tok)),
        "estimated_remaining_imgs_by_tokens": est_rem_imgs_by_tok,
        "est_remaining_imgs_with_refs": est_rem_imgs_wr,
        "est_remaining_imgs_with_refs_exact": est_rem_imgs_wr_exact,
        "est_remaining_imgs_by_refs": est_rem_by_refs,
        "safe_lower_calls": safe_lower_calls,
        "safe_lower_tokens": safe_lower_tokens,
        "prior_safe_calls": prior_calls,
        "prior_safe_tokens": prior_tokens,
        "lock_upper_calls": lock_upper_calls,
        "lock_upper_tokens": lock_upper_tokens,
        "lock_upper_is_noisy": lock_upper_is_noisy,
        "pinned_calls": pinned_calls,
        "pinned_tokens": pinned_tokens,
        "notes": notes,
    }


def build_report(
    conv_dir: Path,
    workspace: str | None = None,
    soft_cap: int = DEFAULT_SOFT_CAP,
    hard_cap: int = DEFAULT_HARD_CAP,
    count_mode: str = "attempts",
    anchor_mode: str = "auto",
    token_cap: int = 0,
    cache_path: Path | None = None,
    calibration_path: Path | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    now_ts = float(now if now is not None else time.time())
    soft_cap = max(1, int(soft_cap))
    hard_cap = max(soft_cap, int(hard_cap))
    token_cap = max(0, int(token_cap or 0))
    mode = "ok_only" if count_mode == "ok_only" else "attempts"
    a_mode = "manual" if anchor_mode == "manual" else "auto"

    events_30d, scan_meta = scan_conversations(conv_dir, now_ts, WINDOW_30D_SEC, cache_path)
    recent_7d = [e for e in events_30d if e["ts"] >= now_ts - WINDOW_7D_SEC]
    recent_24h = [e for e in events_30d if e["ts"] >= now_ts - WINDOW_24H_SEC]

    # Identify active or most recent official 5h 429 reset timestamp
    latest_429_event = None
    active_reset_ts = 0.0
    latest_429_end_ts = 0.0
    for e in recent_24h:
        if e["kind"] == "429":
            rt = float(e.get("reset_ts") or 0.0)
            et = float(e.get("end_ts") or e["ts"])
            if rt > active_reset_ts or (rt == active_reset_ts and et >= latest_429_end_ts):
                active_reset_ts = rt
                latest_429_end_ts = et
                latest_429_event = e

    # Identify active or most recent official weekly (>6h) 429 reset timestamp
    latest_weekly_429_event = None
    active_weekly_reset_ts = 0.0
    latest_weekly_429_end_ts = 0.0
    for e in recent_7d:
        if e["kind"] == "429_WEEKLY":
            rt = float(e.get("reset_ts") or 0.0)
            et = float(e.get("end_ts") or e["ts"])
            if rt > active_weekly_reset_ts or (rt == active_weekly_reset_ts and et >= latest_weekly_429_end_ts):
                active_weekly_reset_ts = rt
                latest_weekly_429_end_ts = et
                latest_weekly_429_event = e

    # If a later OK succeeded after the 429 lock, backend already rolled open capacity
    ok_after_lock = (
        latest_429_event is not None
        and any(e["kind"] == "OK" and float(e["ts"]) > latest_429_end_ts + 5.0 for e in recent_24h)
    )
    cooldown_active = (active_reset_ts > now_ts) and not ok_after_lock

    ok_after_weekly_lock = (
        latest_weekly_429_event is not None
        and any(e["kind"] == "OK" and float(e["ts"]) > latest_weekly_429_end_ts + 5.0 for e in recent_7d)
    )
    weekly_cooldown_active = (active_weekly_reset_ts > now_ts) and not ok_after_weekly_lock

    if cooldown_active:
        window_mode = "official_429_lock"
        roll_floor = max(now_ts - WINDOW_5H_SEC, active_reset_ts - WINDOW_5H_SEC - 15.0)
        win_events = [e for e in events_30d if roll_floor <= e["ts"] <= now_ts]
        window_start = active_reset_ts - WINDOW_5H_SEC
        window_end = active_reset_ts
    else:
        # True continuous 5h sliding time-rolling window [now - 5h, now]
        roll_floor = now_ts - WINDOW_5H_SEC
        candidate = [e for e in events_30d if roll_floor <= e["ts"] <= now_ts]
        billable_in_win = [e for e in candidate if e["kind"] in BILLABLE_KINDS]
        if billable_in_win:
            window_mode = "active_rolling"
            window_start = float(billable_in_win[0]["ts"])
            window_end = window_start + WINDOW_5H_SEC
            win_events = candidate
        else:
            window_mode = "idle_full"
            window_start = 0.0
            window_end = 0.0
            win_events = []

    w5 = summarize_bucket(win_events)
    w24 = summarize_bucket(recent_24h)
    w7 = summarize_bucket(recent_7d)
    w30 = summarize_bucket(events_30d)
    eff_cal_path = calibration_path if calibration_path is not None else (
        cache_path.with_name("image-calibration.json") if cache_path is not None else None
    )
    calibration = compute_calibration(events_30d, active_floor_ts=now_ts - WINDOW_5H_SEC)
    calibration = merge_calibration_with_ledger(
        calibration,
        eff_cal_path,
        active_floor_ts=now_ts - WINDOW_5H_SEC,
        now_ts=now_ts,
    )
    per_image_avg = compute_per_image_avg(events_30d)
    avg_wr_tok = float((per_image_avg.get("with_refs") or {}).get("avg_tokens_total") or per_image_avg.get("avg_tokens_total") or 0)
    rolling_schedule = build_rolling_schedule(
        win_events,
        now_ts,
        mode=mode,
        active_reset_ts=active_reset_ts if cooldown_active else 0.0,
        avg_with_refs_tokens=avg_wr_tok,
    )
    weekly_rolling_schedule = build_weekly_rolling_schedule(
        recent_7d,
        now_ts,
        mode=mode,
        active_weekly_reset_ts=active_weekly_reset_ts if weekly_cooldown_active else 0.0,
        avg_with_refs_tokens=avg_wr_tok,
        last_weekly_429_at=latest_weekly_429_event["ts"] if latest_weekly_429_event else 0.0,
        last_weekly_429_msg=latest_weekly_429_event["error_summary"] if latest_weekly_429_event else "",
    )
    anchor = compute_anchor(
        anchor_mode=a_mode,
        count_mode=mode,
        w5=w5,
        cooldown_active=cooldown_active,
        calibration=calibration,
        per_image_avg=per_image_avg,
        soft_cap=soft_cap,
        hard_cap=hard_cap,
        token_cap=token_cap,
    )

    billable_win = [e for e in win_events if e["kind"] in BILLABLE_KINDS]
    oldest_ts = round(float(billable_win[0]["ts"]), 3) if billable_win else 0.0
    newest_ts = round(float(billable_win[-1]["ts"]), 3) if billable_win else 0.0

    next_recover_ts = round(active_reset_ts, 3) if cooldown_active else float(rolling_schedule["next_recover_ts"])
    next_recover_sec = round(max(0.0, next_recover_ts - now_ts), 1) if next_recover_ts > now_ts else 0.0
    full_clear_ts = float(rolling_schedule["full_clear_ts"])
    full_clear_sec = round(max(0.0, full_clear_ts - now_ts), 1) if full_clear_ts > now_ts else 0.0
    if not cooldown_active and next_recover_ts > 0.0:
        window_end = next_recover_ts

    recent_list = list(reversed(events_30d[-20:]))
    ledgers = scan_workspace_ledgers(workspace, now_ts)

    return {
        "generated_at": round(now_ts, 3),
        "model": (latest_429_event.get("model") if latest_429_event and latest_429_event.get("model") else calibration["detected_model"]),
        "settings": {
            "soft_cap": soft_cap,
            "hard_cap": hard_cap,
            "token_cap": token_cap,
            "count_mode": mode,
            "anchor_mode": a_mode,
        },
        "window_5h": {
            **w5,
            "mode": window_mode,
            "health": anchor["health"],
            "cooldown_active": cooldown_active,
            "window_start": round(window_start, 3),
            "window_end": round(window_end, 3),
            "oldest_ts": oldest_ts,
            "newest_ts": newest_ts,
            "next_recover_ts": round(next_recover_ts, 3),
            "next_recover_sec": next_recover_sec,
            "full_clear_ts": round(full_clear_ts, 3),
            "full_clear_sec": full_clear_sec,
            "reset_remaining_sec": round(max(0.0, window_end - now_ts), 1) if window_end > now_ts else 0.0,
            "metric_used": anchor["metric_used"],
            "remaining_est": anchor["remaining_calls"],
            "calls_pct": anchor["calls_pct"],
            "tokens_pct": anchor["tokens_pct"],
            "used_pct": anchor["used_pct"],
            "used_pct_basis": anchor["used_pct_basis"],
            "soft_cap": soft_cap,
            "hard_cap": hard_cap,
            "token_cap": token_cap,
            "anchor_mode": a_mode,
            "anchor_source": anchor["source"],
            "eof_warning": anchor["eof_warning"],
            "exceeded_safe_peak": anchor["exceeded_safe_peak"],
            "last_429_at": latest_429_event["ts"] if latest_429_event else 0.0,
            "last_429_msg": latest_429_event["error_summary"] if latest_429_event else "",
            "weekly_cooldown_active": weekly_cooldown_active,
            "weekly_reset_ts": round(active_weekly_reset_ts, 3) if weekly_cooldown_active else 0.0,
            "last_weekly_429_at": latest_weekly_429_event["ts"] if latest_weekly_429_event else 0.0,
            "last_weekly_429_msg": latest_weekly_429_event["error_summary"] if latest_weekly_429_event else "",
        },
        "rolling_schedule": rolling_schedule,
        "weekly_rolling_schedule": weekly_rolling_schedule,
        "per_image_avg": per_image_avg,
        "anchor": anchor,
        "window_24h": w24,
        "window_7d": w7,
        "window_30d": w30,
        "calibration": calibration,
        "recent_events": recent_list,
        "workspace_ledgers": ledgers,
        "scan_meta": scan_meta,
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Analyze Antigravity image generation quota and workspace ledgers.")
    parser.add_argument("--conv-dir", default=str(Path.home() / ".gemini" / "antigravity" / "conversations"))
    parser.add_argument("--workspace", default="")
    parser.add_argument("--soft-cap", type=int, default=DEFAULT_SOFT_CAP)
    parser.add_argument("--hard-cap", type=int, default=DEFAULT_HARD_CAP)
    parser.add_argument("--token-cap", type=int, default=0)
    parser.add_argument("--count-mode", choices=["attempts", "ok_only"], default="attempts")
    parser.add_argument("--anchor-mode", choices=["auto", "manual"], default="auto")
    parser.add_argument("--cache", default="")
    parser.add_argument("--calibration", default="")
    parser.add_argument("--reset-calibration", action="store_true")
    parser.add_argument("--pin-peak", type=int, default=None)
    parser.add_argument("--unpin-peak", action="store_true")
    args = parser.parse_args()

    cache_p = Path(args.cache) if args.cache else None
    cal_p = Path(args.calibration) if args.calibration else None
    target_cal = cal_p if cal_p is not None else (
        cache_p.with_name("image-calibration.json") if cache_p is not None else None
    )
    if args.reset_calibration:
        reset_calibration_ledger(target_cal)
    if args.unpin_peak:
        pin_calibration_peak(target_cal, 0)
    elif args.pin_peak is not None:
        pin_calibration_peak(target_cal, args.pin_peak)
    report = build_report(
        conv_dir=Path(args.conv_dir),
        workspace=args.workspace or None,
        soft_cap=args.soft_cap,
        hard_cap=args.hard_cap,
        token_cap=args.token_cap,
        count_mode=args.count_mode,
        anchor_mode=args.anchor_mode,
        cache_path=cache_p,
        calibration_path=cal_p,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
