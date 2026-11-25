#!/usr/bin/env python3
"""Image generation quota & workspace ledger analyzer for Antigravity Quota Cockpit.

Scans local Antigravity conversation databases (read-only) for `generate_image`
tool calls, extracts official 429 `quotaResetTimeStamp` / model metadata, computes
5-hour / 24-hour / 7-day rolling statistics and empirical 429 ceilings, and links
optional workspace `api_call_ledger.json` files when present.
"""

import argparse
import datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from context_report import Msg  # noqa: E402

WINDOW_5H_SEC = 5 * 3600
WINDOW_24H_SEC = 24 * 3600
WINDOW_7D_SEC = 7 * 86400
WINDOW_30D_SEC = 30 * 86400
WEEKLY_LOCK_THRESHOLD_SEC = 6 * 3600
TRUNCATED_LOCK_GAP_SEC = 900.0  # >15m gap between reset_ts-5h and first local billable call => incomplete window

DEFAULT_SOFT_CAP = 54   # Default conservative soft warning cap (5h window)
DEFAULT_HARD_CAP = 60   # Default conservative baseline cap (5h window)
CACHE_VERSION = 6
CALIBRATION_LEDGER_VERSION = 1
REQUIRED_CACHED_EVENT_FIELDS = (
    "ts",
    "end_ts",
    "kind",
    "n_refs",
    "prompt_tokens",
    "input_image_tokens",
    "output_image_tokens",
    "thinking_tokens",
    "tokens_raw",
    "tokens_total",
    "est_usd",
)

# Official gemini-3.1-flash-image (Nano Banana 2) token & pricing model (1K output)
REF_IMAGE_TOKENS = 1120
OUTPUT_1K_IMAGE_TOKENS = 1120
THINKING_MINIMAL_TOKENS = 250
PRICE_INPUT_PER_M = 0.50       # USD per 1M text / ref-image input tokens
PRICE_THINKING_PER_M = 3.00    # USD per 1M minimal thinking tokens
PRICE_OUTPUT_IMG_PER_M = 60.00 # USD per 1M output image tokens ($0.0672 / 1K img)

BILLABLE_KINDS = ("OK", "EOF", "ERR", "RUNNING")

RE_RESET_TS = re.compile(r'"quotaResetTimeStamp"\s*:\s*"([^"]+)"')
RE_RESET_DELAY = re.compile(r'"quotaResetDelay"\s*:\s*"([^"]+)"')
RE_RETRY_DELAY = re.compile(r'"retryDelay"\s*:\s*"([0-9.]+)s"')
RE_ERR_MODEL = re.compile(r'"model"\s*:\s*"([^"]+)"')
RE_ERR_MSG = re.compile(r'"message"\s*:\s*"([^"]+)"')
RE_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef]")

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", "dist", "build", ".next", ".cache",
}


def estimate_text_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = len(RE_CJK.findall(text))
    non_cjk = max(0, len(text) - cjk)
    return max(1, int(round(cjk * 1.1 + non_cjk / 4.0)))


def compute_event_tokens(kind: str, prompt_tokens: int, n_refs: int) -> dict[str, Any]:
    """Compute official gemini-3.1-flash-image token & API-equiv USD estimate for one call."""
    if kind not in BILLABLE_KINDS:
        return {
            "input_image_tokens": 0,
            "output_image_tokens": 0,
            "thinking_tokens": 0,
            "tokens_raw": 0,
            "tokens_total": 0,
            "est_usd": 0.0,
        }
    p_tok = max(0, int(prompt_tokens))
    refs = max(0, int(n_refs))
    in_img = refs * REF_IMAGE_TOKENS
    out_img = OUTPUT_1K_IMAGE_TOKENS
    think = THINKING_MINIMAL_TOKENS
    raw = p_tok + in_img + out_img
    total = raw + think
    usd = (
        (p_tok + in_img) * PRICE_INPUT_PER_M
        + think * PRICE_THINKING_PER_M
        + out_img * PRICE_OUTPUT_IMG_PER_M
    ) / 1_000_000.0
    return {
        "input_image_tokens": in_img,
        "output_image_tokens": out_img,
        "thinking_tokens": think,
        "tokens_raw": raw,
        "tokens_total": total,
        "est_usd": round(usd, 5),
    }


def parse_iso_utc(text: str) -> float:
    if not text:
        return 0.0
    s = text.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.timestamp()
    except ValueError:
        return 0.0


def file_stamp(path: Path) -> str:
    try:
        st = path.stat()
        return f"{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        return "-"


def db_combined_stamp(db_path: Path) -> str:
    wal = db_path.with_name(db_path.name + "-wal")
    return f"{file_stamp(db_path)}|{file_stamp(wal)}"


def db_latest_mtime(db_path: Path) -> float:
    mt = 0.0
    for p in (db_path, db_path.with_name(db_path.name + "-wal")):
        try:
            mt = max(mt, p.stat().st_mtime)
        except OSError:
            pass
    return mt


def classify_error(status: int, err_text: str, end_ts: float = 0.0) -> tuple[str, str, float, str, float, str]:
    """Return (kind, model, reset_ts, reset_iso, retry_sec, summary)."""
    low = err_text.lower()
    model = ""
    reset_ts = 0.0
    reset_iso = ""
    retry_sec = 0.0
    summary = ""

    if status == 3:
        return ("OK", "", 0.0, "", 0.0, "")
    if status in (1, 2):
        return ("RUNNING", "", 0.0, "", 0.0, "生成中")

    m_mod = RE_ERR_MODEL.search(err_text)
    if m_mod:
        model = m_mod.group(1)

    m_rst = RE_RESET_TS.search(err_text)
    if m_rst:
        reset_iso = m_rst.group(1)
        reset_ts = parse_iso_utc(reset_iso)

    m_retry = RE_RETRY_DELAY.search(err_text)
    if m_retry:
        try:
            retry_sec = float(m_retry.group(1))
        except ValueError:
            retry_sec = 0.0

    m_msg = RE_ERR_MSG.search(err_text)
    if m_msg:
        summary = m_msg.group(1)
    elif err_text:
        first_line = err_text.strip().splitlines()[0]
        summary = first_line[:120]

    # Local client-side argument validation failures never hit backend quota
    is_client_err = (
        "invalid arguments:" in low
        or "jsonschema" in low
        or "failed to parse tool arguments" in low
    )
    if is_client_err:
        return ("CLIENT_ERR", model, 0.0, "", 0.0, summary or "本地参数校验失败")

    has_quota_marker = (
        "quota_exhausted" in low
        or "exhausted your capacity" in low
    )
    has_rpm_marker = (
        "rate_limit_exceeded" in low
        or "per minute" in low
        or "too many requests per" in low
    )
    is_429 = (
        "429" in err_text
        or has_quota_marker
        or has_rpm_marker
        or "resource_exhausted" in low
    )
    if is_429:
        short_reset = (
            reset_ts <= 0.0
            or (end_ts > 0.0 and -5.0 <= (reset_ts - end_ts) < 120.0)
        )
        # Note: Google's backend reuses "You have exhausted your capacity" even for 0.4s-1.5s RPM bursts,
        # so short retryDelay (<120s) paired with a short reset window (<120s) is always a burst RPM throttle.
        is_rpm = (
            (has_rpm_marker and short_reset)
            or (0.0 < retry_sec < 120.0 and short_reset)
        )
        if is_rpm:
            return ("429_RPM", model, reset_ts, reset_iso, retry_sec, summary or "429 突发速率限流 (RPM)")
        is_weekly_lock = (
            reset_ts > 0.0
            and end_ts > 0.0
            and (reset_ts - end_ts) > WEEKLY_LOCK_THRESHOLD_SEC
        )
        if is_weekly_lock:
            return ("429_WEEKLY", model, reset_ts, reset_iso, retry_sec, summary or "429 周配额已耗尽")
        return ("429", model, reset_ts, reset_iso, retry_sec, summary or "429 配额已耗尽")

    is_eof = (
        "eof" in low
        or "closed idle connection" in low
        or "connection reset" in low
        or "broken pipe" in low
        or "deadline exceeded" in low
        or "context canceled" in low
    )
    if is_eof:
        return ("EOF", model, 0.0, "", 0.0, summary or "网关断连 / EOF")

    return ("ERR", model, 0.0, "", 0.0, summary or f"失败 (status={status})")


def extract_tool_image_info(args_json: str) -> tuple[str, str, int, int, int, str]:
    """Return (name, prompt_preview, prompt_chars, prompt_tokens, n_refs, aspect_ratio)."""
    if not args_json:
        return ("", "", 0, 0, 0, "")
    try:
        obj = json.loads(args_json)
        if not isinstance(obj, dict):
            return ("", "", 0, 0, 0, "")
    except json.JSONDecodeError:
        return ("", "", 0, 0, 0, "")

    name = ""
    for k in ("ImageName", "image_name", "name", "filename", "output_name"):
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            name = v.strip()
            break

    prompt_full = ""
    for k in ("Prompt", "prompt", "description", "instruction"):
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            prompt_full = v.strip()
            break
    prompt_preview = re.sub(r"\s+", " ", prompt_full)[:72] if prompt_full else ""
    prompt_chars = len(prompt_full)
    prompt_tokens = estimate_text_tokens(prompt_full) if prompt_full else 0

    n_refs = 0
    for k in ("ImagePaths", "image_paths", "reference_images", "ReferenceImages", "input_images"):
        v = obj.get(k)
        if isinstance(v, list):
            n_refs = sum(1 for item in v if isinstance(item, str) and item.strip())
            break
        if isinstance(v, str) and v.strip():
            n_refs = 1
            break
    if n_refs == 0:
        for k in ("ImagePath", "image_path", "reference_image", "input_image"):
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                n_refs = 1
                break

    aspect_ratio = ""
    for k in ("AspectRatio", "aspect_ratio", "ratio"):
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            aspect_ratio = v.strip()
            break

    return (name, prompt_preview, prompt_chars, prompt_tokens, n_refs, aspect_ratio)


def scan_single_db(db_path: Path) -> list[dict[str, Any]]:
    uri = "file:" + db_path.as_posix() + "?mode=ro"
    events: list[dict[str, Any]] = []
    conv_id = db_path.stem
    fallback_ts = db_latest_mtime(db_path)

    conn = sqlite3.connect(uri, uri=True, timeout=1.0)
    try:
        rows = conn.execute(
            "SELECT idx, step_type, status, metadata, error_details "
            "FROM steps WHERE step_type IN (91, 132)"
        ).fetchall()
    except sqlite3.Error:
        return events
    finally:
        conn.close()

    for idx, stype, status, md_b, err_b in rows:
        md = Msg(md_b) if md_b else Msg(b"")
        tc = md.msg(4)
        tname = tc.str(2) if tc is not None else ("generate_image" if stype == 91 else "")
        if tname != "generate_image":
            continue

        ts_msg = md.msg(1)
        ts = float(ts_msg.int(1)) if (ts_msg is not None and ts_msg.int(1) > 0) else fallback_ts
        end_msg = md.msg(8)
        end_ts = float(end_msg.int(1)) if (end_msg is not None and end_msg.int(1) > 0) else ts
        if end_ts < ts:
            end_ts = ts
        duration_sec = round(max(0.0, end_ts - ts), 1)

        args_json = tc.str(3) if tc is not None else ""
        img_name, prompt_preview, prompt_chars, prompt_tokens, n_refs, aspect_ratio = extract_tool_image_info(args_json)

        err_text = ""
        if err_b:
            em = Msg(err_b)
            err_text = em.str(2) or em.str(3) or ""
            if not err_text:
                # Fallback: grab first non-empty UTF-8 string field
                for vals in em.fields.values():
                    for v in vals:
                        if isinstance(v, bytes) and len(v) > 4:
                            s = v.decode("utf-8", errors="ignore").strip()
                            if s:
                                err_text = s
                                break
                    if err_text:
                        break

        kind, model, reset_ts, reset_iso, retry_sec, summary = classify_error(
            int(status or 0), err_text, end_ts=end_ts
        )
        tok = compute_event_tokens(kind, prompt_tokens, n_refs)
        events.append({
            "ts": round(ts, 3),
            "end_ts": round(end_ts, 3),
            "duration_sec": duration_sec,
            "conv_id": conv_id,
            "step_idx": int(idx),
            "kind": kind,
            "raw_status": int(status or 0),
            "image_name": img_name,
            "prompt_preview": prompt_preview,
            "prompt_chars": prompt_chars,
            "prompt_tokens": prompt_tokens,
            "n_refs": n_refs,
            "aspect_ratio": aspect_ratio,
            "input_image_tokens": tok["input_image_tokens"],
            "output_image_tokens": tok["output_image_tokens"],
            "thinking_tokens": tok["thinking_tokens"],
            "tokens_raw": tok["tokens_raw"],
            "tokens_total": tok["tokens_total"],
            "est_usd": tok["est_usd"],
            "model": model,
            "reset_ts": round(reset_ts, 3) if reset_ts else 0.0,
            "reset_iso": reset_iso,
            "retry_sec": round(retry_sec, 1) if retry_sec else 0.0,
            "error_summary": summary,
        })

    return events


def _is_valid_cached_event(ev: Any) -> bool:
    if not isinstance(ev, dict):
        return False
    for field in REQUIRED_CACHED_EVENT_FIELDS:
        if field not in ev:
            return False
    if ev.get("kind") in BILLABLE_KINDS and int(ev.get("tokens_total") or 0) <= 0:
        return False
    return True


def _is_valid_cached_events(events: Any) -> bool:
    return isinstance(events, list) and all(_is_valid_cached_event(e) for e in events)


KNOWN_EVENT_KINDS = ("OK", "EOF", "ERR", "RUNNING", "429", "429_WEEKLY", "429_RPM", "CLIENT_ERR")


def _salvage_archived_event(ev: Any) -> dict[str, Any] | None:
    """Validate or migrate a cached event from a DB that Antigravity has already GC'd."""
    if not isinstance(ev, dict):
        return None
    if _is_valid_cached_event(ev):
        return ev
    ts = float(ev.get("ts") or 0.0)
    kind = str(ev.get("kind") or "")
    if ts <= 0.0 or kind not in KNOWN_EVENT_KINDS:
        return None
    end_ts = float(ev.get("end_ts") or ts)
    if end_ts < ts:
        end_ts = ts
    n_refs = max(0, int(ev.get("n_refs") or 0))
    prompt_tokens = max(0, int(ev.get("prompt_tokens") or 0))
    if kind in BILLABLE_KINDS and prompt_tokens == 0 and ev.get("prompt_preview"):
        prompt_tokens = estimate_text_tokens(str(ev.get("prompt_preview") or ""))
    tok = compute_event_tokens(kind, prompt_tokens, n_refs)
    merged = dict(ev)
    merged.setdefault("step_idx", 0)
    merged.setdefault("conv_id", "")
    merged.setdefault("raw_status", 3 if kind == "OK" else 4)
    merged.setdefault("duration_sec", round(max(0.0, end_ts - ts), 1))
    merged.setdefault("image_name", "")
    merged.setdefault("prompt_preview", "")
    merged.setdefault("prompt_chars", len(str(merged.get("prompt_preview") or "")))
    merged.setdefault("aspect_ratio", "")
    merged.setdefault("model", "")
    merged.setdefault("reset_ts", 0.0)
    merged.setdefault("reset_iso", "")
    merged.setdefault("retry_sec", 0.0)
    merged.setdefault("error_summary", "")
    merged["ts"] = round(ts, 3)
    merged["end_ts"] = round(end_ts, 3)
    merged["n_refs"] = n_refs
    merged["prompt_tokens"] = prompt_tokens
    merged.update(tok)
    return merged if _is_valid_cached_event(merged) else None


def _load_cache_raw(cache_path: Path | None) -> tuple[dict[str, Any], bool]:
    if not cache_path or not cache_path.is_file():
        return {"version": CACHE_VERSION, "files": {}}, False
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("files"), dict):
            is_cur = data.get("version") == CACHE_VERSION
            return {"version": data.get("version", 0), "files": data["files"]}, is_cur
    except (OSError, json.JSONDecodeError):
        pass
    return {"version": CACHE_VERSION, "files": {}}, False


def load_cache(cache_path: Path | None) -> dict[str, Any]:
    data, is_cur = _load_cache_raw(cache_path)
    if is_cur:
        return data
    return {"version": CACHE_VERSION, "files": {}}


def _cleanup_stale_cache_temps(cache_path: Path, now_ts: float) -> None:
    prefix = f"{cache_path.stem}.tmp."
    try:
        for p in cache_path.parent.iterdir():
            if not p.name.startswith(prefix):
                continue
            try:
                if now_ts - p.stat().st_mtime > 60.0:
                    p.unlink(missing_ok=True)
            except OSError:
                pass
    except OSError:
        pass


def save_cache(cache_path: Path | None, cache: dict[str, Any]) -> None:
    if not cache_path:
        return
    tmp: Path | None = None
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(cache, ensure_ascii=False)
        tmp = cache_path.with_name(f"{cache_path.stem}.tmp.{os.getpid()}.{time.time_ns()}")
        tmp.write_text(payload, encoding="utf-8")
        replaced = False
        for attempt in range(4):
            try:
                os.replace(str(tmp), str(cache_path))
                replaced = True
                break
            except OSError:
                if attempt < 3:
                    time.sleep(0.025 * (attempt + 1))
        if not replaced and tmp.exists():
            tmp.unlink(missing_ok=True)
        _cleanup_stale_cache_temps(cache_path, time.time())
    except OSError:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def scan_conversations(
    conv_dir: Path,
    now: float,
    lookback_sec: float = WINDOW_30D_SEC,
    cache_path: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    raw_cache, version_matches = _load_cache_raw(cache_path)
    cached_files: dict[str, Any] = raw_cache.get("files", {})
    cutoff = now - lookback_sec - 3600
    ev_cutoff = now - lookback_sec

    all_events: list[dict[str, Any]] = []
    scanned_dbs = 0
    cache_hits = 0
    archived_dbs = 0
    archived_events = 0
    cache_dirty = not version_matches and bool(cached_files)
    active_keys: set[str] = set()

    if conv_dir.is_dir():
        for db_path in conv_dir.glob("*.db"):
            mt = db_latest_mtime(db_path)
            if mt < cutoff:
                continue
            key = db_path.name
            active_keys.add(key)
            stamp = db_combined_stamp(db_path)
            entry = cached_files.get(key)
            if (
                version_matches
                and isinstance(entry, dict)
                and entry.get("stamp") == stamp
                and _is_valid_cached_events(entry.get("events"))
            ):
                evs = entry["events"]
                cache_hits += 1
            else:
                evs = scan_single_db(db_path)
                cached_files[key] = {"stamp": stamp, "events": evs}
                scanned_dbs += 1
            for e in evs:
                if float(e.get("ts") or 0.0) >= ev_cutoff:
                    all_events.append(e)

    # Preserve events from GC'd/deleted conversation DBs while still inside the lookback window;
    # prune only empty entries or entries whose events have fully aged past lookback_sec.
    for k in list(cached_files.keys()):
        if k in active_keys:
            continue
        entry = cached_files.get(k)
        raw_evs = entry.get("events") if isinstance(entry, dict) else None
        surviving: list[dict[str, Any]] = []
        if isinstance(raw_evs, list):
            for e in raw_evs:
                norm = _salvage_archived_event(e)
                if norm is not None and float(norm.get("ts") or 0.0) >= ev_cutoff:
                    surviving.append(norm)
        if surviving:
            was_archived = isinstance(entry, dict) and bool(entry.get("archived"))
            prev_len = len(raw_evs) if isinstance(raw_evs, list) else -1
            if not was_archived or prev_len != len(surviving) or not version_matches:
                cache_dirty = True
            cached_files[k] = {
                "stamp": str(entry.get("stamp") or "") if isinstance(entry, dict) else "",
                "archived": True,
                "events": surviving,
            }
            all_events.extend(surviving)
            archived_dbs += 1
            archived_events += len(surviving)
        else:
            del cached_files[k]
            cache_dirty = True

    if scanned_dbs > 0 or cache_dirty or (cache_path and not cache_path.is_file()):
        save_cache(cache_path, {"version": CACHE_VERSION, "files": cached_files})

    all_events.sort(key=lambda e: (e["ts"], e.get("step_idx", 0)))
    return all_events, {
        "scanned_dbs": scanned_dbs,
        "cache_hits": cache_hits,
        "active_dbs": len(active_keys),
        "archived_dbs": archived_dbs,
        "archived_events": archived_events,
    }


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


if __name__ == "__main__":
    main()
