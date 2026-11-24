#!/usr/bin/env python3
"""Local 7-day per-pool conversation usage scanner for Antigravity Quota Cockpit.

Scans local Antigravity conversation SQLite databases (strictly read-only) for
`PLANNER_RESPONSE` (`step_type = 15`) native `model_usage` records, aggregates
hourly weighted compute proxies per model pool (`pool_a` Google Gemini vs
`pool_b` Claude / 3P), and persists an incremental mtime+size cache so steady-state
refreshes only touch active conversation DBs.
"""

import argparse
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

from context_report import Msg, model_id  # noqa: E402

WINDOW_5H_SEC = 5 * 3600
WINDOW_7D_SEC = 7 * 86400
LOOKBACK_MARGIN_SEC = 86400
CACHE_VERSION = 1

PROVIDER_GOOGLE = 24
PROVIDER_ANTHROPIC = 26

RE_LIGHT_TIER = re.compile(r"(flash|lite|argon)", re.IGNORECASE)
RE_MID_TIER = re.compile(r"(hydrogen|sonnet|gpt-oss)", re.IGNORECASE)
RE_HEAVY_TIER = re.compile(r"(neon|opus|carbon|pro)", re.IGNORECASE)


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


def pool_key_for_usage(provider_enum: int, slug: str = "") -> str:
    if provider_enum == PROVIDER_GOOGLE:
        return "pool_a"
    if provider_enum == PROVIDER_ANTHROPIC:
        return "pool_b"
    low = (slug or "").strip().lower()
    if low.startswith("gemini-"):
        return "pool_a"
    if low.startswith(("claude-", "gpt-")):
        return "pool_b"
    return ""


def model_tier_multiplier(slug: str) -> float:
    low = (slug or "").strip().lower()
    if not low:
        return 0.7
    if RE_HEAVY_TIER.search(low):
        return 1.0
    if RE_MID_TIER.search(low):
        return 0.65
    if RE_LIGHT_TIER.search(low):
        return 0.25
    return 0.7


def _step_ts(md: Msg) -> float:
    for fn in (1, 7, 6):
        m = md.msg(fn)
        if m is not None and m.int(1) > 0:
            return float(m.int(1))
    return 0.0


def scan_single_db_usage(db_path: Path) -> list[dict[str, Any]]:
    """Return compact hourly pool-usage rollups for one conversation DB."""
    uri = "file:" + db_path.as_posix() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=1.0)
    try:
        rows = conn.execute(
            "SELECT metadata FROM steps WHERE step_type = 15"
        ).fetchall()
        gm_row = None
        try:
            gm_row = conn.execute(
                "SELECT data FROM gen_metadata ORDER BY idx DESC LIMIT 1"
            ).fetchone()
        except sqlite3.Error:
            gm_row = None
    except sqlite3.Error:
        return []
    finally:
        conn.close()

    slug = model_id(Msg(gm_row[0])) if (gm_row and gm_row[0]) else ""
    tier_mult = model_tier_multiplier(slug)

    buckets: dict[tuple[str, int], dict[str, Any]] = {}
    for (md_b,) in rows:
        if not md_b:
            continue
        md = Msg(md_b)
        mu = md.msg(9)
        if mu is None:
            continue
        ts = _step_ts(md)
        if ts <= 0.0:
            continue
        pool_key = pool_key_for_usage(mu.int(6), slug)
        if not pool_key:
            continue

        uncached_in = max(0, mu.int(2))
        out_tok = max(0, mu.int(3))
        cache_read = max(0, mu.int(5))
        # Relative compute weight: cached input ~0.1x, uncached input 1x, output+thinking ~5x
        weighted = max(1.0, (uncached_in + cache_read * 0.1 + out_tok * 5.0) * tier_mult)

        hour_ts = int(ts // 3600) * 3600
        key = (pool_key, hour_ts)
        cur = buckets.get(key)
        if cur is None:
            buckets[key] = {
                "pool": pool_key,
                "hour_ts": hour_ts,
                "first_ts": round(ts, 3),
                "last_ts": round(ts, 3),
                "turns": 1,
                "uncached_in": uncached_in,
                "cache_read": cache_read,
                "out": out_tok,
                "weighted": round(weighted, 2),
            }
        else:
            if ts < cur["first_ts"]:
                cur["first_ts"] = round(ts, 3)
            if ts > cur["last_ts"]:
                cur["last_ts"] = round(ts, 3)
            cur["turns"] += 1
            cur["uncached_in"] += uncached_in
            cur["cache_read"] += cache_read
            cur["out"] += out_tok
            cur["weighted"] = round(cur["weighted"] + weighted, 2)

    return list(buckets.values())


def _is_valid_cached_buckets(buckets: Any) -> bool:
    if not isinstance(buckets, list):
        return False
    for b in buckets:
        if not isinstance(b, dict):
            return False
        if not all(k in b for k in ("pool", "hour_ts", "first_ts", "last_ts", "turns", "weighted")):
            return False
    return True


def load_cache(cache_path: Path | None) -> dict[str, Any]:
    if not cache_path or not cache_path.is_file():
        return {"version": CACHE_VERSION, "files": {}}
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("version") == CACHE_VERSION and isinstance(data.get("files"), dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"version": CACHE_VERSION, "files": {}}


def _cleanup_stale_temps(cache_path: Path, now_ts: float) -> None:
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
        _cleanup_stale_temps(cache_path, time.time())
    except OSError:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def build_pool_usage_report(
    conv_dir: Path,
    now: float | None = None,
    cache_path: Path | None = None,
) -> dict[str, Any]:
    now_ts = float(now if now is not None else time.time())
    cutoff_7d = now_ts - WINDOW_7D_SEC
    cutoff_5h = now_ts - WINDOW_5H_SEC
    scan_cutoff = cutoff_7d - LOOKBACK_MARGIN_SEC

    cache = load_cache(cache_path)
    cached_files: dict[str, Any] = cache.get("files", {})

    merged: dict[str, dict[int, dict[str, Any]]] = {"pool_a": {}, "pool_b": {}}
    turns_5h: dict[str, int] = {"pool_a": 0, "pool_b": 0}
    scanned_dbs = 0
    cache_hits = 0
    active_keys: set[str] = set()

    if conv_dir.is_dir():
        for db_path in conv_dir.glob("*.db"):
            mt = db_latest_mtime(db_path)
            if mt < scan_cutoff:
                continue
            key = db_path.name
            active_keys.add(key)
            stamp = db_combined_stamp(db_path)
            entry = cached_files.get(key)
            if isinstance(entry, dict) and entry.get("stamp") == stamp and _is_valid_cached_buckets(entry.get("buckets")):
                db_buckets = entry["buckets"]
                cache_hits += 1
            else:
                db_buckets = scan_single_db_usage(db_path)
                cached_files[key] = {"stamp": stamp, "buckets": db_buckets}
                scanned_dbs += 1

            for b in db_buckets:
                if float(b["last_ts"]) < cutoff_7d:
                    continue
                pk = str(b["pool"])
                h_ts = int(b["hour_ts"])
                pool_map = merged.setdefault(pk, {})
                cur = pool_map.get(h_ts)
                if cur is None:
                    pool_map[h_ts] = {
                        "hour_ts": h_ts,
                        "first_ts": float(b["first_ts"]),
                        "last_ts": float(b["last_ts"]),
                        "turns": int(b["turns"]),
                        "uncached_in": int(b.get("uncached_in") or 0),
                        "cache_read": int(b.get("cache_read") or 0),
                        "out": int(b.get("out") or 0),
                        "weighted": float(b["weighted"]),
                    }
                else:
                    if float(b["first_ts"]) < cur["first_ts"]:
                        cur["first_ts"] = float(b["first_ts"])
                    if float(b["last_ts"]) > cur["last_ts"]:
                        cur["last_ts"] = float(b["last_ts"])
                    cur["turns"] += int(b["turns"])
                    cur["uncached_in"] += int(b.get("uncached_in") or 0)
                    cur["cache_read"] += int(b.get("cache_read") or 0)
                    cur["out"] += int(b.get("out") or 0)
                    cur["weighted"] += float(b["weighted"])

                if float(b["last_ts"]) >= cutoff_5h:
                    turns_5h[pk] = turns_5h.get(pk, 0) + int(b["turns"])

    for k in list(cached_files.keys()):
        if k not in active_keys:
            del cached_files[k]

    if scanned_dbs > 0 or (cache_path and not cache_path.is_file()):
        save_cache(cache_path, {"version": CACHE_VERSION, "files": cached_files})

    pools_out: dict[str, Any] = {}
    for pk, hmap in merged.items():
        hourly = [hmap[h] for h in sorted(hmap.keys())]
        for item in hourly:
            item["first_ts"] = round(item["first_ts"], 3)
            item["last_ts"] = round(item["last_ts"], 3)
            item["weighted"] = round(item["weighted"], 2)
        pools_out[pk] = {
            "turns_7d": sum(x["turns"] for x in hourly),
            "turns_5h": turns_5h.get(pk, 0),
            "uncached_in_7d": sum(x["uncached_in"] for x in hourly),
            "cache_read_7d": sum(x["cache_read"] for x in hourly),
            "out_7d": sum(x["out"] for x in hourly),
            "weighted_7d": round(sum(x["weighted"] for x in hourly), 2),
            "oldest_ts": hourly[0]["first_ts"] if hourly else 0.0,
            "newest_ts": hourly[-1]["last_ts"] if hourly else 0.0,
            "hourly": hourly,
        }

    return {
        "generated_at": round(now_ts, 3),
        "lookback_days": 7,
        "scan_meta": {
            "scanned_dbs": scanned_dbs,
            "cache_hits": cache_hits,
            "active_dbs": len(active_keys),
        },
        "pools": pools_out,
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Analyze local 7-day conversation usage per model pool.")
    parser.add_argument("--conv-dir", default=str(Path.home() / ".gemini" / "antigravity" / "conversations"))
    parser.add_argument("--cache", default="")
    args = parser.parse_args()

    cache_p = Path(args.cache) if args.cache else None
    report = build_pool_usage_report(conv_dir=Path(args.conv_dir), cache_path=cache_p)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
