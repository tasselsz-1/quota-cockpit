"""Empirical 429 ceiling calibration and persistent calibration ledger."""

import json
from pathlib import Path
import time
from typing import Any

from .constants import (
    BILLABLE_KINDS,
    CALIBRATION_LEDGER_VERSION,
    TRUNCATED_LOCK_GAP_SEC,
    WINDOW_5H_SEC,
    WINDOW_7D_SEC,
)
from .scanner import save_cache
from .schedules import summarize_bucket

def compute_calibration(events: list[dict[str, Any]], active_floor_ts: float = 0.0) -> dict[str, Any]:
    """Compute empirical 5h/7d 429 ceiling observations and 30d proven-safe sliding peaks."""
    # Group distinct 5h 429 windows by reset_ts bucket (rounded to nearest 60s)
    windows_by_reset: dict[int, dict[str, float]] = {}
    weekly_windows_by_reset: dict[int, dict[str, float]] = {}
    detected_model = ""
    rpm_burst_count = 0
    for e in events:
        if e.get("model") and e["kind"] in ("429", "429_RPM", "429_WEEKLY"):
            detected_model = e["model"]
        if e["kind"] == "429_RPM":
            rpm_burst_count += 1
        elif e["kind"] == "429":
            rt = float(e.get("reset_ts") or 0.0)
            if rt > 0:
                bucket = int(round(rt / 60.0)) * 60
                trig = float(e.get("end_ts") or e["ts"])
                cur = windows_by_reset.get(bucket)
                if cur is None:
                    windows_by_reset[bucket] = {"reset_ts": rt, "trigger_ts": trig}
                else:
                    cur["reset_ts"] = max(cur["reset_ts"], rt)
                    cur["trigger_ts"] = min(cur["trigger_ts"], trig)
        elif e["kind"] == "429_WEEKLY":
            rt = float(e.get("reset_ts") or 0.0)
            if rt > 0:
                bucket = int(round(rt / 600.0)) * 600
                trig = float(e.get("end_ts") or e["ts"])
                cur = weekly_windows_by_reset.get(bucket)
                if cur is None:
                    weekly_windows_by_reset[bucket] = {"reset_ts": rt, "trigger_ts": trig}
                else:
                    cur["reset_ts"] = max(cur["reset_ts"], rt)
                    cur["trigger_ts"] = min(cur["trigger_ts"], trig)

    observations: list[dict[str, Any]] = []
    for _, info in sorted(windows_by_reset.items()):
        reset_ts = info["reset_ts"]
        trigger_ts = info["trigger_ts"]
        w_start = reset_ts - WINDOW_5H_SEC
        in_win = [e for e in events if w_start - 15 <= e["ts"] <= reset_ts]
        stats = summarize_bucket(in_win)
        billable_in_win = [e for e in in_win if e["kind"] in BILLABLE_KINDS]
        first_billable_ts = min((float(e["ts"]) for e in billable_in_win), default=0.0)
        start_gap_sec = (
            max(0.0, first_billable_ts - w_start)
            if first_billable_ts > 0.0
            else float(WINDOW_5H_SEC)
        )
        is_truncated = start_gap_sec > TRUNCATED_LOCK_GAP_SEC
        is_clean = (not is_truncated and stats["eof_rate_pct"] <= 10.0 and stats["attempts"] >= 20)
        observations.append({
            "window_start": round(w_start, 3),
            "window_end": round(reset_ts, 3),
            "trigger_ts": round(trigger_ts, 3),
            "ok": stats["ok"],
            "eof": stats["eof"],
            "err": stats["err"],
            "attempts": stats["attempts"],
            "eof_rate_pct": stats["eof_rate_pct"],
            "rate_limited": stats["rate_limited"],
            "ref_images": stats["ref_images"],
            "tokens_raw": stats["tokens_raw"],
            "tokens_total": stats["tokens_total"],
            "est_usd": stats["est_usd"],
            "start_gap_sec": round(start_gap_sec, 1),
            "is_truncated": is_truncated,
            "is_clean": is_clean,
        })

    clean_locks = [o for o in observations if o["is_clean"]]
    truncated_locks = [o for o in observations if o["is_truncated"]]
    noisy_locks = [o for o in observations if not o["is_clean"]]

    # Two-pointer O(N) scan for proven-safe 5h sliding peak:
    # exclude windows that contain/precede a 5h 429 OR fall inside any known 5h 429 lock window
    lock_times = sorted(float(e.get("end_ts") or e["ts"]) for e in events if e["kind"] == "429")
    lock_spans = [
        (float(o["window_start"]) - 15.0, max(float(o["trigger_ts"]), float(o["window_start"])) + 60.0)
        for o in observations
    ]
    billable = [e for e in events if e["kind"] in BILLABLE_KINDS]

    def _empty_peak() -> dict[str, Any]:
        return {
            "lookback_days": 30,
            "max_attempts": 0,
            "max_attempts_at": 0.0,
            "max_attempts_ok": 0,
            "max_attempts_tokens_total": 0,
            "max_ok": 0,
            "max_ok_at": 0.0,
            "max_ok_attempts": 0,
            "max_ok_tokens_total": 0,
            "max_tokens_total": 0,
            "max_tokens_raw": 0,
            "max_tokens_at": 0.0,
            "max_tokens_attempts": 0,
            "max_tokens_ok": 0,
            "max_tokens_refs": 0,
        }

    def _update_peak(target: dict[str, Any], t_end: float, cur_attempts: int, cur_ok: int, cur_tok_raw: int, cur_tok_total: int, cur_refs: int) -> None:
        if cur_attempts > target["max_attempts"] or (
            cur_attempts == target["max_attempts"] and cur_tok_total > target["max_attempts_tokens_total"]
        ):
            target["max_attempts"] = cur_attempts
            target["max_attempts_at"] = round(t_end, 3)
            target["max_attempts_ok"] = cur_ok
            target["max_attempts_tokens_total"] = cur_tok_total

        if cur_ok > target["max_ok"] or (
            cur_ok == target["max_ok"] and cur_tok_total > target["max_ok_tokens_total"]
        ):
            target["max_ok"] = cur_ok
            target["max_ok_at"] = round(t_end, 3)
            target["max_ok_attempts"] = cur_attempts
            target["max_ok_tokens_total"] = cur_tok_total

        if cur_tok_total > target["max_tokens_total"]:
            target["max_tokens_total"] = cur_tok_total
            target["max_tokens_raw"] = cur_tok_raw
            target["max_tokens_at"] = round(t_end, 3)
            target["max_tokens_attempts"] = cur_attempts
            target["max_tokens_ok"] = cur_ok
            target["max_tokens_refs"] = cur_refs

    peak = _empty_peak()
    prior_peak = _empty_peak()

    if billable:
        left = 0
        lock_idx = 0
        cur_ok = 0
        cur_tok_raw = 0
        cur_tok_total = 0
        cur_refs = 0

        for right, ev in enumerate(billable):
            t_end = float(ev["ts"])
            if ev["kind"] == "OK":
                cur_ok += 1
            cur_tok_raw += int(ev.get("tokens_raw") or 0)
            cur_tok_total += int(ev.get("tokens_total") or 0)
            cur_refs += int(ev.get("n_refs") or 0)

            while left <= right and float(billable[left]["ts"]) < t_end - WINDOW_5H_SEC:
                old = billable[left]
                if old["kind"] == "OK":
                    cur_ok -= 1
                cur_tok_raw -= int(old.get("tokens_raw") or 0)
                cur_tok_total -= int(old.get("tokens_total") or 0)
                cur_refs -= int(old.get("n_refs") or 0)
                left += 1

            w_floor = t_end - WINDOW_5H_SEC
            while lock_idx < len(lock_times) and lock_times[lock_idx] < w_floor:
                lock_idx += 1
            # Exclude windows that fall inside an observed 5h 429 blame window or contain/precede a 5h 429
            in_lock_span = any(s <= t_end <= e for s, e in lock_spans)
            has_nearby_lock = (
                in_lock_span
                or (lock_idx < len(lock_times) and lock_times[lock_idx] <= t_end + 600.0)
            )
            if has_nearby_lock:
                continue

            cur_attempts = right - left + 1
            _update_peak(peak, t_end, cur_attempts, cur_ok, cur_tok_raw, cur_tok_total, cur_refs)
            if active_floor_ts <= 0.0 or t_end < active_floor_ts:
                _update_peak(prior_peak, t_end, cur_attempts, cur_ok, cur_tok_raw, cur_tok_total, cur_refs)

    # Weekly 429 observations (7-day rolling window ending at trigger_ts)
    weekly_observations: list[dict[str, Any]] = []
    for _, info in sorted(weekly_windows_by_reset.items()):
        reset_ts = info["reset_ts"]
        trigger_ts = info["trigger_ts"]
        w_start = trigger_ts - WINDOW_7D_SEC
        in_win = [e for e in events if w_start <= e["ts"] <= trigger_ts]
        stats = summarize_bucket(in_win)
        weekly_observations.append({
            "window_start": round(w_start, 3),
            "window_end": round(reset_ts, 3),
            "trigger_ts": round(trigger_ts, 3),
            "ok": stats["ok"],
            "eof": stats["eof"],
            "err": stats["err"],
            "attempts": stats["attempts"],
            "eof_rate_pct": stats["eof_rate_pct"],
            "ref_images": stats["ref_images"],
            "tokens_raw": stats["tokens_raw"],
            "tokens_total": stats["tokens_total"],
            "est_usd": stats["est_usd"],
        })

    weekly_lock_times = sorted(float(e.get("end_ts") or e["ts"]) for e in events if e["kind"] == "429_WEEKLY")
    peak_7d = _empty_peak()
    if billable:
        left7 = 0
        wlock_idx = 0
        cur_ok7 = 0
        cur_raw7 = 0
        cur_tok7 = 0
        cur_refs7 = 0
        for right7, ev in enumerate(billable):
            t_end = float(ev["ts"])
            if ev["kind"] == "OK":
                cur_ok7 += 1
            cur_raw7 += int(ev.get("tokens_raw") or 0)
            cur_tok7 += int(ev.get("tokens_total") or 0)
            cur_refs7 += int(ev.get("n_refs") or 0)
            while left7 <= right7 and float(billable[left7]["ts"]) < t_end - WINDOW_7D_SEC:
                old = billable[left7]
                if old["kind"] == "OK":
                    cur_ok7 -= 1
                cur_raw7 -= int(old.get("tokens_raw") or 0)
                cur_tok7 -= int(old.get("tokens_total") or 0)
                cur_refs7 -= int(old.get("n_refs") or 0)
                left7 += 1
            while wlock_idx < len(weekly_lock_times) and weekly_lock_times[wlock_idx] < t_end - WINDOW_7D_SEC:
                wlock_idx += 1
            if wlock_idx < len(weekly_lock_times) and weekly_lock_times[wlock_idx] <= t_end + 3600.0:
                continue
            _update_peak(peak_7d, t_end, right7 - left7 + 1, cur_ok7, cur_raw7, cur_tok7, cur_refs7)

    latest = observations[-1] if observations else None
    latest_weekly = weekly_observations[-1] if weekly_observations else None
    return {
        "detected_model": detected_model or "gemini-3.1-flash-image",
        "observed_lock_count": len(observations),
        "clean_lock_count": len(clean_locks),
        "noisy_lock_count": len(noisy_locks),
        "truncated_lock_count": len(truncated_locks),
        "rpm_burst_count": rpm_burst_count,
        "latest_lock": latest,
        "lock_observations": observations[-6:],
        "avg_ok_at_lock": round(sum(o["ok"] for o in observations) / len(observations), 1) if observations else None,
        "avg_attempts_at_lock": round(sum(o["attempts"] for o in observations) / len(observations), 1) if observations else None,
        "avg_tokens_at_lock": int(round(sum(o["tokens_total"] for o in observations) / len(observations))) if observations else None,
        "clean_avg_ok_at_lock": round(sum(o["ok"] for o in clean_locks) / len(clean_locks), 1) if clean_locks else None,
        "clean_avg_attempts_at_lock": round(sum(o["attempts"] for o in clean_locks) / len(clean_locks), 1) if clean_locks else None,
        "clean_avg_tokens_at_lock": int(round(sum(o["tokens_total"] for o in clean_locks) / len(clean_locks))) if clean_locks else None,
        "observed_safe_peak": peak,
        "prior_safe_peak": prior_peak,
        "weekly_lock_count": len(weekly_observations),
        "latest_weekly_lock": latest_weekly,
        "weekly_lock_observations": weekly_observations[-4:],
        "avg_attempts_at_weekly_lock": round(sum(o["attempts"] for o in weekly_observations) / len(weekly_observations), 1) if weekly_observations else None,
        "avg_tokens_at_weekly_lock": int(round(sum(o["tokens_total"] for o in weekly_observations) / len(weekly_observations))) if weekly_observations else None,
        "observed_7d_safe_peak": peak_7d,
        "pinned_peak": None,
        "persisted_safe_peak_used": False,
        "calibration_persisted": False,
    }


def _empty_peak_dict() -> dict[str, Any]:
    return {
        "lookback_days": 30,
        "max_attempts": 0,
        "max_attempts_at": 0.0,
        "max_attempts_ok": 0,
        "max_attempts_tokens_total": 0,
        "max_ok": 0,
        "max_ok_at": 0.0,
        "max_ok_attempts": 0,
        "max_ok_tokens_total": 0,
        "max_tokens_total": 0,
        "max_tokens_raw": 0,
        "max_tokens_at": 0.0,
        "max_tokens_attempts": 0,
        "max_tokens_ok": 0,
        "max_tokens_refs": 0,
    }


def _normalize_peak_dict(raw: Any) -> dict[str, Any]:
    base = _empty_peak_dict()
    if not isinstance(raw, dict):
        return base
    for k in (
        "lookback_days", "max_attempts", "max_attempts_ok", "max_attempts_tokens_total",
        "max_ok", "max_ok_attempts", "max_ok_tokens_total",
        "max_tokens_total", "max_tokens_raw", "max_tokens_attempts", "max_tokens_ok", "max_tokens_refs",
    ):
        try:
            base[k] = max(0, int(raw.get(k) or 0))
        except (TypeError, ValueError):
            pass
    for k in ("max_attempts_at", "max_ok_at", "max_tokens_at"):
        try:
            base[k] = round(max(0.0, float(raw.get(k) or 0.0)), 3)
        except (TypeError, ValueError):
            pass
    if base["lookback_days"] <= 0:
        base["lookback_days"] = 30
    return base


def _merge_peak_dicts(live_raw: Any, saved_raw: Any, max_saved_ts: float = 0.0) -> dict[str, Any]:
    """Monotonically keep the higher proven-safe peak across live scan and persistent ledger."""
    live = _normalize_peak_dict(live_raw)
    saved = _normalize_peak_dict(saved_raw)
    out = dict(live)

    def _saved_allowed(ts_val: float) -> bool:
        if max_saved_ts <= 0.0:
            return True
        return ts_val > 0.0 and ts_val < max_saved_ts

    if _saved_allowed(saved["max_attempts_at"]) and (
        saved["max_attempts"] > out["max_attempts"]
        or (
            saved["max_attempts"] == out["max_attempts"]
            and saved["max_attempts"] > 0
            and saved["max_attempts_tokens_total"] > out["max_attempts_tokens_total"]
        )
    ):
        out["max_attempts"] = saved["max_attempts"]
        out["max_attempts_at"] = saved["max_attempts_at"]
        out["max_attempts_ok"] = saved["max_attempts_ok"]
        out["max_attempts_tokens_total"] = saved["max_attempts_tokens_total"]

    if _saved_allowed(saved["max_ok_at"]) and (
        saved["max_ok"] > out["max_ok"]
        or (
            saved["max_ok"] == out["max_ok"]
            and saved["max_ok"] > 0
            and saved["max_ok_tokens_total"] > out["max_ok_tokens_total"]
        )
    ):
        out["max_ok"] = saved["max_ok"]
        out["max_ok_at"] = saved["max_ok_at"]
        out["max_ok_attempts"] = saved["max_ok_attempts"]
        out["max_ok_tokens_total"] = saved["max_ok_tokens_total"]

    if _saved_allowed(saved["max_tokens_at"]) and saved["max_tokens_total"] > out["max_tokens_total"]:
        out["max_tokens_total"] = saved["max_tokens_total"]
        out["max_tokens_raw"] = saved["max_tokens_raw"]
        out["max_tokens_at"] = saved["max_tokens_at"]
        out["max_tokens_attempts"] = saved["max_tokens_attempts"]
        out["max_tokens_ok"] = saved["max_tokens_ok"]
        out["max_tokens_refs"] = saved["max_tokens_refs"]

    return out


def _normalize_pinned_peak(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    try:
        calls = int(raw.get("calls") or 0)
    except (TypeError, ValueError):
        return None
    if calls <= 0:
        return None
    calls = max(1, min(5000, calls))
    try:
        tok = max(0, int(raw.get("tokens_total") or 0))
    except (TypeError, ValueError):
        tok = 0
    try:
        at = round(max(0.0, float(raw.get("pinned_at") or 0.0)), 3)
    except (TypeError, ValueError):
        at = 0.0
    return {"calls": calls, "tokens_total": tok, "pinned_at": at}


def _empty_calibration_ledger() -> dict[str, Any]:
    return {
        "version": CALIBRATION_LEDGER_VERSION,
        "updated_at": 0.0,
        "best_safe_peak": _empty_peak_dict(),
        "best_7d_safe_peak": _empty_peak_dict(),
        "lock_observations": [],
        "weekly_lock_observations": [],
        "pinned_peak": None,
    }


def load_calibration_ledger(ledger_path: Path | None) -> dict[str, Any]:
    if not ledger_path or not ledger_path.is_file():
        return _empty_calibration_ledger()
    try:
        raw = json.loads(ledger_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return _empty_calibration_ledger()
        locks_5h = [o for o in (raw.get("lock_observations") or []) if isinstance(o, dict) and float(o.get("window_end") or 0.0) > 0.0]
        locks_7d = [o for o in (raw.get("weekly_lock_observations") or []) if isinstance(o, dict) and float(o.get("window_end") or 0.0) > 0.0]
        return {
            "version": CALIBRATION_LEDGER_VERSION,
            "updated_at": float(raw.get("updated_at") or 0.0),
            "best_safe_peak": _normalize_peak_dict(raw.get("best_safe_peak")),
            "best_7d_safe_peak": _normalize_peak_dict(raw.get("best_7d_safe_peak")),
            "lock_observations": locks_5h[-24:],
            "weekly_lock_observations": locks_7d[-12:],
            "pinned_peak": _normalize_pinned_peak(raw.get("pinned_peak")),
        }
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return _empty_calibration_ledger()


def save_calibration_ledger(ledger_path: Path | None, ledger: dict[str, Any]) -> None:
    if not ledger_path:
        return
    save_cache(ledger_path, ledger)


def pin_calibration_peak(
    ledger_path: Path | None,
    calls: int,
    tokens_total: int = 0,
    now_ts: float = 0.0,
) -> dict[str, Any] | None:
    """Pin (calls > 0) or unpin (calls <= 0) the 5h image ceiling in the persistent ledger."""
    if not ledger_path:
        return None
    ledger = load_calibration_ledger(ledger_path)
    ts_now = round(now_ts if now_ts > 0.0 else time.time(), 3)
    if int(calls or 0) <= 0:
        pinned = None
    else:
        pinned = {
            "calls": max(1, min(5000, int(calls))),
            "tokens_total": max(0, int(tokens_total or 0)),
            "pinned_at": ts_now,
        }
    ledger["pinned_peak"] = pinned
    ledger["updated_at"] = ts_now
    save_calibration_ledger(ledger_path, ledger)
    return pinned


def reset_calibration_ledger(ledger_path: Path | None) -> bool:
    if not ledger_path:
        return False
    try:
        if ledger_path.exists():
            ledger_path.unlink()
        return True
    except OSError:
        return False


def _lock_obs_quality(obs: dict[str, Any]) -> tuple[int, int, int]:
    return (
        0 if obs.get("is_truncated") else 1,
        int(obs.get("attempts") or 0),
        int(obs.get("tokens_total") or 0),
    )


def merge_calibration_with_ledger(
    live_cal: dict[str, Any],
    ledger_path: Path | None,
    active_floor_ts: float = 0.0,
    now_ts: float = 0.0,
) -> dict[str, Any]:
    """Merge live 30d calibration with persistent ledger so DB GC never erases learned ceilings."""
    if not ledger_path:
        return live_cal

    saved = load_calibration_ledger(ledger_path)
    live_peak = _normalize_peak_dict(live_cal.get("observed_safe_peak"))
    live_prior = _normalize_peak_dict(live_cal.get("prior_safe_peak"))
    live_7d = _normalize_peak_dict(live_cal.get("observed_7d_safe_peak"))

    merged_peak = _merge_peak_dicts(live_peak, saved["best_safe_peak"])
    merged_prior = _merge_peak_dicts(live_prior, saved["best_safe_peak"], max_saved_ts=active_floor_ts)
    merged_7d = _merge_peak_dicts(live_7d, saved["best_7d_safe_peak"])

    persisted_used = (
        merged_peak["max_attempts"] > live_peak["max_attempts"]
        or merged_peak["max_ok"] > live_peak["max_ok"]
        or merged_peak["max_tokens_total"] > live_peak["max_tokens_total"]
    )

    # Deduplicate 5h 429 observations by reset_ts minute bucket, preferring complete/richer records
    lock_map: dict[int, dict[str, Any]] = {}
    for src in (saved.get("lock_observations") or [], live_cal.get("lock_observations") or []):
        for o in src:
            if not isinstance(o, dict):
                continue
            we = float(o.get("window_end") or 0.0)
            if we <= 0.0:
                continue
            bkey = int(round(we / 60.0)) * 60
            prev = lock_map.get(bkey)
            if prev is None or _lock_obs_quality(o) > _lock_obs_quality(prev):
                lock_map[bkey] = dict(o)
    merged_locks = sorted(lock_map.values(), key=lambda x: float(x.get("window_end") or 0.0))[-24:]

    # Deduplicate 7d weekly 429 observations by 10-minute reset bucket
    wlock_map: dict[int, dict[str, Any]] = {}
    for src in (saved.get("weekly_lock_observations") or [], live_cal.get("weekly_lock_observations") or []):
        for o in src:
            if not isinstance(o, dict):
                continue
            we = float(o.get("window_end") or 0.0)
            if we <= 0.0:
                continue
            bkey = int(round(we / 600.0)) * 600
            prev = wlock_map.get(bkey)
            if prev is None or (int(o.get("attempts") or 0), int(o.get("tokens_total") or 0)) > (
                int(prev.get("attempts") or 0), int(prev.get("tokens_total") or 0)
            ):
                wlock_map[bkey] = dict(o)
    merged_wlocks = sorted(wlock_map.values(), key=lambda x: float(x.get("window_end") or 0.0))[-12:]

    clean_locks = [o for o in merged_locks if o.get("is_clean")]
    truncated_locks = [o for o in merged_locks if o.get("is_truncated")]
    noisy_locks = [o for o in merged_locks if not o.get("is_clean")]

    pinned_peak = _normalize_pinned_peak(saved.get("pinned_peak"))
    next_ledger = {
        "version": CALIBRATION_LEDGER_VERSION,
        "updated_at": round(now_ts if now_ts > 0.0 else time.time(), 3),
        "best_safe_peak": merged_peak,
        "best_7d_safe_peak": merged_7d,
        "lock_observations": merged_locks,
        "weekly_lock_observations": merged_wlocks,
        "pinned_peak": pinned_peak,
    }
    cmp_keys = ("best_safe_peak", "best_7d_safe_peak", "lock_observations", "weekly_lock_observations", "pinned_peak")
    comparable_saved = {k: saved.get(k) for k in cmp_keys}
    comparable_next = {k: next_ledger.get(k) for k in cmp_keys}
    has_any_data = (
        merged_peak["max_attempts"] > 0
        or merged_7d["max_attempts"] > 0
        or bool(merged_locks)
        or bool(merged_wlocks)
        or pinned_peak is not None
    )
    if comparable_next != comparable_saved or (has_any_data and not ledger_path.is_file()):
        save_calibration_ledger(ledger_path, next_ledger)

    out = dict(live_cal)
    out.update({
        "observed_lock_count": len(merged_locks),
        "clean_lock_count": len(clean_locks),
        "noisy_lock_count": len(noisy_locks),
        "truncated_lock_count": len(truncated_locks),
        "latest_lock": merged_locks[-1] if merged_locks else None,
        "lock_observations": merged_locks[-6:],
        "avg_ok_at_lock": round(sum(o["ok"] for o in merged_locks) / len(merged_locks), 1) if merged_locks else None,
        "avg_attempts_at_lock": round(sum(o["attempts"] for o in merged_locks) / len(merged_locks), 1) if merged_locks else None,
        "avg_tokens_at_lock": int(round(sum(o["tokens_total"] for o in merged_locks) / len(merged_locks))) if merged_locks else None,
        "clean_avg_ok_at_lock": round(sum(o["ok"] for o in clean_locks) / len(clean_locks), 1) if clean_locks else None,
        "clean_avg_attempts_at_lock": round(sum(o["attempts"] for o in clean_locks) / len(clean_locks), 1) if clean_locks else None,
        "clean_avg_tokens_at_lock": int(round(sum(o["tokens_total"] for o in clean_locks) / len(clean_locks))) if clean_locks else None,
        "observed_safe_peak": merged_peak,
        "prior_safe_peak": merged_prior,
        "weekly_lock_count": len(merged_wlocks),
        "latest_weekly_lock": merged_wlocks[-1] if merged_wlocks else None,
        "weekly_lock_observations": merged_wlocks[-4:],
        "avg_attempts_at_weekly_lock": round(sum(o["attempts"] for o in merged_wlocks) / len(merged_wlocks), 1) if merged_wlocks else None,
        "avg_tokens_at_weekly_lock": int(round(sum(o["tokens_total"] for o in merged_wlocks) / len(merged_wlocks))) if merged_wlocks else None,
        "observed_7d_safe_peak": merged_7d,
        "pinned_peak": pinned_peak,
        "persisted_safe_peak_used": persisted_used,
        "calibration_persisted": True,
    })
    return out
