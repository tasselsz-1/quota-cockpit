"""Bucket summaries, per-image averages, and 5h/7d rolling schedules."""

import datetime
from typing import Any

from .constants import (
    BILLABLE_KINDS,
    OUTPUT_1K_IMAGE_TOKENS,
    PRICE_INPUT_PER_M,
    PRICE_OUTPUT_IMG_PER_M,
    PRICE_THINKING_PER_M,
    REF_IMAGE_TOKENS,
    THINKING_MINIMAL_TOKENS,
    WINDOW_5H_SEC,
    WINDOW_7D_SEC,
)

def summarize_bucket(events: list[dict[str, Any]]) -> dict[str, Any]:
    ok = sum(1 for e in events if e["kind"] == "OK")
    eof = sum(1 for e in events if e["kind"] == "EOF")
    err = sum(1 for e in events if e["kind"] == "ERR")
    rate_limited = sum(1 for e in events if e["kind"] == "429")
    weekly_rate_limited = sum(1 for e in events if e["kind"] == "429_WEEKLY")
    rpm_limited = sum(1 for e in events if e["kind"] == "429_RPM")
    client_err = sum(1 for e in events if e["kind"] == "CLIENT_ERR")
    running = sum(1 for e in events if e["kind"] == "RUNNING")
    attempts = ok + eof + err + running
    eof_rate = round(eof / attempts * 100.0, 1) if attempts > 0 else 0.0

    billable = [e for e in events if e["kind"] in BILLABLE_KINDS]
    ref_images = sum(int(e.get("n_refs") or 0) for e in billable)
    prompt_tokens = sum(int(e.get("prompt_tokens") or 0) for e in billable)
    input_image_tokens = sum(int(e.get("input_image_tokens") or 0) for e in billable)
    output_image_tokens = sum(int(e.get("output_image_tokens") or 0) for e in billable)
    thinking_tokens = sum(int(e.get("thinking_tokens") or 0) for e in billable)
    tokens_raw = sum(int(e.get("tokens_raw") or 0) for e in billable)
    tokens_total = sum(int(e.get("tokens_total") or 0) for e in billable)
    est_usd = round(sum(float(e.get("est_usd") or 0.0) for e in billable), 4)

    return {
        "ok": ok,
        "eof": eof,
        "err": err,
        "rate_limited": rate_limited,
        "weekly_rate_limited": weekly_rate_limited,
        "rpm_limited": rpm_limited,
        "client_err": client_err,
        "running": running,
        "attempts": attempts,
        "total": len(events),
        "eof_rate_pct": eof_rate,
        "ref_images": ref_images,
        "prompt_tokens": prompt_tokens,
        "input_image_tokens": input_image_tokens,
        "output_image_tokens": output_image_tokens,
        "thinking_tokens": thinking_tokens,
        "tokens_raw": tokens_raw,
        "tokens_total": tokens_total,
        "est_usd": est_usd,
    }


def compute_per_image_avg(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute empirical per-image token & API-equiv USD averages overall and grouped by ref count."""
    billable = [e for e in events if e["kind"] in BILLABLE_KINDS]
    n = len(billable)
    ok_count = sum(1 for e in billable if e["kind"] == "OK")
    out_img_usd = OUTPUT_1K_IMAGE_TOKENS * PRICE_OUTPUT_IMG_PER_M / 1_000_000.0

    formula_meta = {
        "model": "gemini-3.1-flash-image",
        "output_resolution": "1K",
        "ref_image_tokens": REF_IMAGE_TOKENS,
        "output_1k_tokens": OUTPUT_1K_IMAGE_TOKENS,
        "thinking_minimal_tokens": THINKING_MINIMAL_TOKENS,
        "price_input_per_m": PRICE_INPUT_PER_M,
        "price_thinking_per_m": PRICE_THINKING_PER_M,
        "price_output_img_per_m": PRICE_OUTPUT_IMG_PER_M,
        "output_1k_usd": round(out_img_usd, 4),
    }

    if n == 0:
        default_prompt = 480
        default_raw = default_prompt + OUTPUT_1K_IMAGE_TOKENS
        default_total = default_raw + THINKING_MINIMAL_TOKENS
        default_usd = (
            default_prompt * PRICE_INPUT_PER_M
            + THINKING_MINIMAL_TOKENS * PRICE_THINKING_PER_M
            + OUTPUT_1K_IMAGE_TOKENS * PRICE_OUTPUT_IMG_PER_M
        ) / 1_000_000.0
        wr_raw = default_prompt + REF_IMAGE_TOKENS + OUTPUT_1K_IMAGE_TOKENS
        wr_total = wr_raw + THINKING_MINIMAL_TOKENS
        wr_usd = (
            (default_prompt + REF_IMAGE_TOKENS) * PRICE_INPUT_PER_M
            + THINKING_MINIMAL_TOKENS * PRICE_THINKING_PER_M
            + OUTPUT_1K_IMAGE_TOKENS * PRICE_OUTPUT_IMG_PER_M
        ) / 1_000_000.0
        return {
            "sample_count": 0,
            "ok_count": 0,
            "avg_refs": 0.0,
            "avg_prompt_tokens": default_prompt,
            "avg_input_image_tokens": 0,
            "avg_output_image_tokens": OUTPUT_1K_IMAGE_TOKENS,
            "avg_thinking_tokens": THINKING_MINIMAL_TOKENS,
            "avg_tokens_raw": default_raw,
            "avg_tokens_total": default_total,
            "avg_est_usd": round(default_usd, 4),
            "output_cost_share_pct": round(out_img_usd / default_usd * 100.0, 1),
            "with_refs": {
                "sample_count": 0,
                "ok_count": 0,
                "share_pct": 0.0,
                "avg_refs": 1.0,
                "avg_prompt_tokens": default_prompt,
                "avg_input_image_tokens": REF_IMAGE_TOKENS,
                "avg_output_image_tokens": OUTPUT_1K_IMAGE_TOKENS,
                "avg_thinking_tokens": THINKING_MINIMAL_TOKENS,
                "avg_tokens_raw": wr_raw,
                "avg_tokens_total": wr_total,
                "avg_est_usd": round(wr_usd, 4),
                "is_fallback": True,
            },
            "by_refs": [],
            "formula_meta": formula_meta,
        }

    avg_refs = sum(int(e.get("n_refs") or 0) for e in billable) / n
    avg_prompt = sum(int(e.get("prompt_tokens") or 0) for e in billable) / n
    avg_in_img = sum(int(e.get("input_image_tokens") or 0) for e in billable) / n
    avg_out_img = sum(int(e.get("output_image_tokens") or 0) for e in billable) / n
    avg_think = sum(int(e.get("thinking_tokens") or 0) for e in billable) / n
    avg_raw = sum(int(e.get("tokens_raw") or 0) for e in billable) / n
    avg_total = sum(int(e.get("tokens_total") or 0) for e in billable) / n
    avg_usd = sum(float(e.get("est_usd") or 0.0) for e in billable) / n
    share_pct = round(out_img_usd / avg_usd * 100.0, 1) if avg_usd > 0 else 0.0

    with_ref_events = [e for e in billable if int(e.get("n_refs") or 0) >= 1]
    wr_n = len(with_ref_events)
    if wr_n > 0:
        wr_ok = sum(1 for e in with_ref_events if e["kind"] == "OK")
        with_refs_summary: dict[str, Any] = {
            "sample_count": wr_n,
            "ok_count": wr_ok,
            "share_pct": round(wr_n / n * 100.0, 1),
            "avg_refs": round(sum(int(e.get("n_refs") or 0) for e in with_ref_events) / wr_n, 2),
            "avg_prompt_tokens": int(round(sum(int(e.get("prompt_tokens") or 0) for e in with_ref_events) / wr_n)),
            "avg_input_image_tokens": int(round(sum(int(e.get("input_image_tokens") or 0) for e in with_ref_events) / wr_n)),
            "avg_output_image_tokens": int(round(sum(int(e.get("output_image_tokens") or 0) for e in with_ref_events) / wr_n)),
            "avg_thinking_tokens": int(round(sum(int(e.get("thinking_tokens") or 0) for e in with_ref_events) / wr_n)),
            "avg_tokens_raw": int(round(sum(int(e.get("tokens_raw") or 0) for e in with_ref_events) / wr_n)),
            "avg_tokens_total": int(round(sum(int(e.get("tokens_total") or 0) for e in with_ref_events) / wr_n)),
            "avg_est_usd": round(sum(float(e.get("est_usd") or 0.0) for e in with_ref_events) / wr_n, 4),
            "is_fallback": False,
        }
    else:
        fb_prompt = int(round(avg_prompt))
        fb_raw = fb_prompt + REF_IMAGE_TOKENS + OUTPUT_1K_IMAGE_TOKENS
        fb_total = fb_raw + THINKING_MINIMAL_TOKENS
        fb_usd = (
            (fb_prompt + REF_IMAGE_TOKENS) * PRICE_INPUT_PER_M
            + THINKING_MINIMAL_TOKENS * PRICE_THINKING_PER_M
            + OUTPUT_1K_IMAGE_TOKENS * PRICE_OUTPUT_IMG_PER_M
        ) / 1_000_000.0
        with_refs_summary = {
            "sample_count": 0,
            "ok_count": 0,
            "share_pct": 0.0,
            "avg_refs": 1.0,
            "avg_prompt_tokens": fb_prompt,
            "avg_input_image_tokens": REF_IMAGE_TOKENS,
            "avg_output_image_tokens": OUTPUT_1K_IMAGE_TOKENS,
            "avg_thinking_tokens": THINKING_MINIMAL_TOKENS,
            "avg_tokens_raw": fb_raw,
            "avg_tokens_total": fb_total,
            "avg_est_usd": round(fb_usd, 4),
            "is_fallback": True,
        }

    groups: dict[str, list[dict[str, Any]]] = {"0": [], "1": [], "2": [], "3+": []}
    for e in billable:
        r = int(e.get("n_refs") or 0)
        key = "3+" if r >= 3 else str(max(0, r))
        groups[key].append(e)

    by_refs: list[dict[str, Any]] = []
    for label in ("0", "1", "2", "3+"):
        g = groups[label]
        if not g:
            continue
        gn = len(g)
        by_refs.append({
            "label": label,
            "count": gn,
            "avg_refs": round(sum(int(e.get("n_refs") or 0) for e in g) / gn, 2),
            "avg_prompt_tokens": int(round(sum(int(e.get("prompt_tokens") or 0) for e in g) / gn)),
            "avg_tokens_raw": int(round(sum(int(e.get("tokens_raw") or 0) for e in g) / gn)),
            "avg_tokens_total": int(round(sum(int(e.get("tokens_total") or 0) for e in g) / gn)),
            "avg_est_usd": round(sum(float(e.get("est_usd") or 0.0) for e in g) / gn, 4),
        })

    return {
        "sample_count": n,
        "ok_count": ok_count,
        "avg_refs": round(avg_refs, 2),
        "avg_prompt_tokens": int(round(avg_prompt)),
        "avg_input_image_tokens": int(round(avg_in_img)),
        "avg_output_image_tokens": int(round(avg_out_img)),
        "avg_thinking_tokens": int(round(avg_think)),
        "avg_tokens_raw": int(round(avg_raw)),
        "avg_tokens_total": int(round(avg_total)),
        "avg_est_usd": round(avg_usd, 4),
        "output_cost_share_pct": share_pct,
        "with_refs": with_refs_summary,
        "by_refs": by_refs,
        "formula_meta": formula_meta,
    }


def build_rolling_schedule(
    win_events: list[dict[str, Any]],
    now_ts: float,
    mode: str = "attempts",
    active_reset_ts: float = 0.0,
    avg_with_refs_tokens: float = 0.0,
) -> dict[str, Any]:
    """Build clock-aligned 30-minute rolling roll-off recovery schedule for active 5h events."""
    if mode == "ok_only":
        tracked = [e for e in win_events if e["kind"] == "OK"]
    else:
        tracked = [e for e in win_events if e["kind"] in BILLABLE_KINDS]

    def _to_wr_imgs(tok: int) -> float:
        if avg_with_refs_tokens <= 0 or tok <= 0:
            return 0.0
        return round(tok / avg_with_refs_tokens, 1)

    items: list[tuple[float, dict[str, Any]]] = []
    for e in tracked:
        free_ts = float(e["ts"]) + WINDOW_5H_SEC
        if free_ts <= now_ts:
            if active_reset_ts > now_ts:
                free_ts = active_reset_ts
            else:
                continue
        items.append((free_ts, e))

    items.sort(key=lambda pair: (pair[0], pair[1].get("step_idx", 0)))

    empty_milestone = {"calls": 0, "tokens_raw": 0, "tokens_total": 0, "est_imgs_with_refs": 0.0}
    if not items:
        return {
            "bucket_minutes": 30,
            "total_calls": 0,
            "total_tokens_raw": 0,
            "total_tokens_total": 0,
            "next_recover_ts": round(active_reset_ts, 3) if active_reset_ts > now_ts else 0.0,
            "next_recover_sec": round(max(0.0, active_reset_ts - now_ts), 1) if active_reset_ts > now_ts else 0.0,
            "full_clear_ts": round(active_reset_ts, 3) if active_reset_ts > now_ts else 0.0,
            "full_clear_sec": round(max(0.0, active_reset_ts - now_ts), 1) if active_reset_ts > now_ts else 0.0,
            "first_batch": None,
            "recover_next_30m": dict(empty_milestone),
            "recover_next_1h": dict(empty_milestone),
            "recover_next_2h": dict(empty_milestone),
            "buckets": [],
        }

    total_calls = len(items)
    total_raw = sum(int(e.get("tokens_raw") or 0) for _, e in items)
    total_tok = sum(int(e.get("tokens_total") or 0) for _, e in items)

    first_free_ts = items[0][0]
    last_free_ts = items[-1][0]
    next_recover_ts = max(first_free_ts, active_reset_ts) if active_reset_ts > now_ts else first_free_ts
    full_clear_ts = max(last_free_ts, active_reset_ts) if active_reset_ts > now_ts else last_free_ts

    # First batch: all events expiring within 5 minutes of the earliest expiration
    fb_items = [(ft, e) for ft, e in items if ft <= first_free_ts + 300.0]
    fb_tok = sum(int(e.get("tokens_total") or 0) for _, e in fb_items)
    first_batch = {
        "free_ts": round(next_recover_ts, 3),
        "remaining_sec": round(max(0.0, next_recover_ts - now_ts), 1),
        "calls": len(fb_items),
        "tokens_raw": sum(int(e.get("tokens_raw") or 0) for _, e in fb_items),
        "tokens_total": fb_tok,
        "est_imgs_with_refs": _to_wr_imgs(fb_tok),
    }

    def milestone(horizon_sec: float) -> dict[str, Any]:
        cut = now_ts + horizon_sec
        sub = [e for ft, e in items if ft <= cut]
        sub_tok = sum(int(e.get("tokens_total") or 0) for e in sub)
        return {
            "calls": len(sub),
            "tokens_raw": sum(int(e.get("tokens_raw") or 0) for e in sub),
            "tokens_total": sub_tok,
            "est_imgs_with_refs": _to_wr_imgs(sub_tok),
        }

    step = 1800
    bucket_map: dict[int, list[tuple[float, dict[str, Any]]]] = {}
    for ft, e in items:
        b_start = int(ft // step) * step
        bucket_map.setdefault(b_start, []).append((ft, e))

    buckets: list[dict[str, Any]] = []
    cum_calls = 0
    cum_tok = 0
    for b_start in sorted(bucket_map.keys()):
        b_items = bucket_map[b_start]
        b_calls = len(b_items)
        b_ok = sum(1 for _, e in b_items if e["kind"] == "OK")
        b_eof = sum(1 for _, e in b_items if e["kind"] == "EOF")
        b_err = sum(1 for _, e in b_items if e["kind"] == "ERR")
        b_run = sum(1 for _, e in b_items if e["kind"] == "RUNNING")
        b_raw = sum(int(e.get("tokens_raw") or 0) for _, e in b_items)
        b_tok = sum(int(e.get("tokens_total") or 0) for _, e in b_items)
        cum_calls += b_calls
        cum_tok += b_tok
        buckets.append({
            "bucket_start_ts": float(b_start),
            "bucket_end_ts": float(b_start + step),
            "first_free_ts": round(b_items[0][0], 3),
            "last_free_ts": round(b_items[-1][0], 3),
            "calls": b_calls,
            "ok": b_ok,
            "eof": b_eof,
            "err": b_err,
            "running": b_run,
            "tokens_raw": b_raw,
            "tokens_total": b_tok,
            "est_imgs_with_refs": _to_wr_imgs(b_tok),
            "cum_calls": cum_calls,
            "cum_tokens_total": cum_tok,
            "cum_pct": round(cum_calls / total_calls * 100.0, 1) if total_calls > 0 else 0.0,
        })

    return {
        "bucket_minutes": 30,
        "total_calls": total_calls,
        "total_tokens_raw": total_raw,
        "total_tokens_total": total_tok,
        "next_recover_ts": round(next_recover_ts, 3),
        "next_recover_sec": round(max(0.0, next_recover_ts - now_ts), 1),
        "full_clear_ts": round(full_clear_ts, 3),
        "full_clear_sec": round(max(0.0, full_clear_ts - now_ts), 1),
        "first_batch": first_batch,
        "recover_next_30m": milestone(1800.0),
        "recover_next_1h": milestone(3600.0),
        "recover_next_2h": milestone(7200.0),
        "buckets": buckets,
    }


def build_weekly_rolling_schedule(
    events_7d: list[dict[str, Any]],
    now_ts: float,
    mode: str = "attempts",
    active_weekly_reset_ts: float = 0.0,
    avg_with_refs_tokens: float = 0.0,
    last_weekly_429_at: float = 0.0,
    last_weekly_429_msg: str = "",
) -> dict[str, Any]:
    """Build local-day aligned 7-day rolling roll-off recovery schedule for image generation."""
    if mode == "ok_only":
        tracked = [e for e in events_7d if e["kind"] == "OK"]
    else:
        tracked = [e for e in events_7d if e["kind"] in BILLABLE_KINDS]

    def _to_wr_imgs(tok: int) -> float:
        if avg_with_refs_tokens <= 0 or tok <= 0:
            return 0.0
        return round(tok / avg_with_refs_tokens, 1)

    cooldown_active = active_weekly_reset_ts > now_ts
    items: list[tuple[float, dict[str, Any]]] = []
    for e in tracked:
        free_ts = float(e["ts"]) + WINDOW_7D_SEC
        if free_ts <= now_ts:
            if cooldown_active:
                free_ts = active_weekly_reset_ts
            else:
                continue
        items.append((free_ts, e))

    items.sort(key=lambda pair: (pair[0], pair[1].get("step_idx", 0)))

    empty_milestone = {"calls": 0, "tokens_raw": 0, "tokens_total": 0, "est_imgs_with_refs": 0.0}
    if not items:
        return {
            "bucket_granularity": "day",
            "cooldown_active": cooldown_active,
            "active_reset_ts": round(active_weekly_reset_ts, 3) if cooldown_active else 0.0,
            "last_weekly_429_at": round(last_weekly_429_at, 3) if last_weekly_429_at else 0.0,
            "last_weekly_429_msg": last_weekly_429_msg,
            "total_calls": 0,
            "total_tokens_raw": 0,
            "total_tokens_total": 0,
            "total_est_imgs_with_refs": 0.0,
            "oldest_ts": 0.0,
            "newest_ts": 0.0,
            "next_recover_ts": round(active_weekly_reset_ts, 3) if cooldown_active else 0.0,
            "next_recover_sec": round(max(0.0, active_weekly_reset_ts - now_ts), 1) if cooldown_active else 0.0,
            "full_clear_ts": round(active_weekly_reset_ts, 3) if cooldown_active else 0.0,
            "full_clear_sec": round(max(0.0, active_weekly_reset_ts - now_ts), 1) if cooldown_active else 0.0,
            "first_batch": None,
            "recover_next_24h": dict(empty_milestone),
            "recover_next_48h": dict(empty_milestone),
            "recover_next_72h": dict(empty_milestone),
            "buckets": [],
        }

    total_calls = len(items)
    total_raw = sum(int(e.get("tokens_raw") or 0) for _, e in items)
    total_tok = sum(int(e.get("tokens_total") or 0) for _, e in items)
    oldest_ts = min(float(e["ts"]) for _, e in items)
    newest_ts = max(float(e["ts"]) for _, e in items)

    first_free_ts = items[0][0]
    last_free_ts = items[-1][0]
    next_recover_ts = max(first_free_ts, active_weekly_reset_ts) if cooldown_active else first_free_ts
    full_clear_ts = max(last_free_ts, active_weekly_reset_ts) if cooldown_active else last_free_ts

    # First batch: all events expiring within 1 hour of the earliest 7-day roll-off
    fb_items = [(ft, e) for ft, e in items if ft <= first_free_ts + 3600.0]
    fb_tok = sum(int(e.get("tokens_total") or 0) for _, e in fb_items)
    first_batch = {
        "free_ts": round(next_recover_ts, 3),
        "remaining_sec": round(max(0.0, next_recover_ts - now_ts), 1),
        "calls": len(fb_items),
        "tokens_raw": sum(int(e.get("tokens_raw") or 0) for _, e in fb_items),
        "tokens_total": fb_tok,
        "est_imgs_with_refs": _to_wr_imgs(fb_tok),
    }

    def milestone(horizon_sec: float) -> dict[str, Any]:
        cut = now_ts + horizon_sec
        sub = [e for ft, e in items if ft <= cut]
        sub_tok = sum(int(e.get("tokens_total") or 0) for e in sub)
        return {
            "calls": len(sub),
            "tokens_raw": sum(int(e.get("tokens_raw") or 0) for e in sub),
            "tokens_total": sub_tok,
            "est_imgs_with_refs": _to_wr_imgs(sub_tok),
        }

    day_map: dict[datetime.date, list[tuple[float, dict[str, Any]]]] = {}
    for ft, e in items:
        day_key = datetime.datetime.fromtimestamp(ft).date()
        day_map.setdefault(day_key, []).append((ft, e))

    buckets: list[dict[str, Any]] = []
    cum_calls = 0
    cum_tok = 0
    for day_key in sorted(day_map.keys()):
        b_items = day_map[day_key]
        b_calls = len(b_items)
        b_ok = sum(1 for _, e in b_items if e["kind"] == "OK")
        b_eof = sum(1 for _, e in b_items if e["kind"] == "EOF")
        b_err = sum(1 for _, e in b_items if e["kind"] == "ERR")
        b_run = sum(1 for _, e in b_items if e["kind"] == "RUNNING")
        b_raw = sum(int(e.get("tokens_raw") or 0) for _, e in b_items)
        b_tok = sum(int(e.get("tokens_total") or 0) for _, e in b_items)
        cum_calls += b_calls
        cum_tok += b_tok
        day_start = datetime.datetime(day_key.year, day_key.month, day_key.day).timestamp()
        next_day = day_key + datetime.timedelta(days=1)
        day_end = datetime.datetime(next_day.year, next_day.month, next_day.day).timestamp()
        buckets.append({
            "date_key": day_key.isoformat(),
            "label": day_key.strftime("%m-%d"),
            "bucket_start_ts": round(day_start, 3),
            "bucket_end_ts": round(day_end, 3),
            "first_free_ts": round(b_items[0][0], 3),
            "last_free_ts": round(b_items[-1][0], 3),
            "calls": b_calls,
            "ok": b_ok,
            "eof": b_eof,
            "err": b_err,
            "running": b_run,
            "tokens_raw": b_raw,
            "tokens_total": b_tok,
            "est_imgs_with_refs": _to_wr_imgs(b_tok),
            "cum_calls": cum_calls,
            "cum_tokens_total": cum_tok,
            "cum_pct": round(cum_calls / total_calls * 100.0, 1) if total_calls > 0 else 0.0,
        })

    return {
        "bucket_granularity": "day",
        "cooldown_active": cooldown_active,
        "active_reset_ts": round(active_weekly_reset_ts, 3) if cooldown_active else 0.0,
        "last_weekly_429_at": round(last_weekly_429_at, 3) if last_weekly_429_at else 0.0,
        "last_weekly_429_msg": last_weekly_429_msg,
        "total_calls": total_calls,
        "total_tokens_raw": total_raw,
        "total_tokens_total": total_tok,
        "total_est_imgs_with_refs": _to_wr_imgs(total_tok),
        "oldest_ts": round(oldest_ts, 3),
        "newest_ts": round(newest_ts, 3),
        "next_recover_ts": round(next_recover_ts, 3),
        "next_recover_sec": round(max(0.0, next_recover_ts - now_ts), 1),
        "full_clear_ts": round(full_clear_ts, 3),
        "full_clear_sec": round(max(0.0, full_clear_ts - now_ts), 1),
        "first_batch": first_batch,
        "recover_next_24h": milestone(86400.0),
        "recover_next_48h": milestone(2 * 86400.0),
        "recover_next_72h": milestone(3 * 86400.0),
        "buckets": buckets,
    }
