"""Conversation SQLite scanner and event cache management."""

import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from typing import Any

_COCKPIT_DIR = Path(__file__).resolve().parent.parent
if str(_COCKPIT_DIR) not in sys.path:
    sys.path.insert(0, str(_COCKPIT_DIR))

from context_parser.protobuf import Msg  # noqa: E402

from .constants import (
    BILLABLE_KINDS,
    CACHE_VERSION,
    KNOWN_EVENT_KINDS,
    REQUIRED_CACHED_EVENT_FIELDS,
    RE_ERR_MODEL,
    RE_ERR_MSG,
    RE_RESET_TS,
    RE_RETRY_DELAY,
    WEEKLY_LOCK_THRESHOLD_SEC,
    WINDOW_30D_SEC,
    compute_event_tokens,
    db_combined_stamp,
    db_latest_mtime,
    estimate_text_tokens,
    parse_iso_utc,
)

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
