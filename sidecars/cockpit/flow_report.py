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

import argparse
import ctypes
import json
import math
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPORT_VERSION = 1
CREDITS_CACHE_VERSION = 1
CREDITS_HISTORY_MAX = 60

HOUR_SEC = 3600.0
DAY_SEC = 86400.0
WEEK_SEC = 7 * 86400.0
MONTH_SEC = 30 * 86400.0
LOCK_STALE_SEC = 600.0

DEFAULT_MAX_PER_HOUR = 6
DEFAULT_MAX_PER_DAY = 20
DEFAULT_MAX_DAILY_CREDITS = 400.0
DEFAULT_MIN_REMAINING_CREDITS = 100.0
DEFAULT_MIN_GAP_SEC = 35.0

# Built-in Google Flow video model catalog & fallback credit estimates.
# Keep aligned with `gflow models --json`; actual per-run quotes vary by tier
# and duration, so real ledger spend overrides when observed.
VIDEO_MODELS: list[dict[str, Any]] = [
    {
        "id": "veo_3_1_lite",
        "alias": "veo-lite",
        "label": "Veo 3.1 Lite",
        "est_credits": 10.0,
        "max_duration": 8,
        "ref_cap": 3,
        "retires_on": None,
        "active": True,
    },
    {
        "id": "omni_1_2_flash",
        "alias": "omni-1.2-flash",
        "label": "Omni Flash 1.2",
        "est_credits": 20.0,
        "max_duration": 15,
        "ref_cap": 7,
        "retires_on": None,
        "active": True,
    },
    {
        "id": "veo_3_1_lite_lower_priority",
        "alias": "veo-lite-lp",
        "label": "Veo 3.1 Lite LP",
        "est_credits": 10.0,
        "max_duration": 8,
        "ref_cap": 3,
        "retires_on": None,
        "active": True,
    },
    {
        "id": "veo_3_1_fast",
        "alias": "veo-fast",
        "label": "Veo 3.1 Fast",
        "est_credits": 20.0,
        "max_duration": 8,
        "ref_cap": 3,
        "retires_on": "2026-12-05",
        "active": False,
    },
    {
        "id": "omni_flash",
        "alias": "omni-flash",
        "label": "Omni Flash 1.1",
        "est_credits": 20.0,
        "max_duration": 10,
        "ref_cap": 7,
        "retires_on": "2026-12-05",
        "active": False,
    },
    {
        "id": "veo_3_1_quality",
        "alias": "veo-quality",
        "label": "Veo 3.1 Quality",
        "est_credits": 100.0,
        "max_duration": 8,
        "ref_cap": 0,
        "retires_on": "2026-12-05",
        "active": False,
    },
]

MODEL_ALIAS_TO_ID: dict[str, str] = {}
MODEL_BY_ID: dict[str, dict[str, Any]] = {}
for _m in VIDEO_MODELS:
    MODEL_BY_ID[_m["id"]] = _m
    MODEL_ALIAS_TO_ID[_m["id"].lower()] = _m["id"]
    MODEL_ALIAS_TO_ID[_m["alias"].lower()] = _m["id"]
MODEL_ALIAS_TO_ID["omni-1.2"] = "omni_1_2_flash"
MODEL_ALIAS_TO_ID["veo_3_1_lite_lp"] = "veo_3_1_lite_lower_priority"

LEDGER_COUNTED_ACTIONS = {"i2v", "r2v", "t2v", "upscale", "character", "video"}
WORKSPACE_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".agent-tasks", ".agents", "dist", "build", ".next",
}


def default_gflow_data_dir() -> Path:
    env_dir = os.environ.get("GFLOW_DATA_DIR", "").strip()
    if env_dir:
        return Path(env_dir)
    sys_name = platform.system().lower()
    if sys_name == "windows":
        base = os.environ.get("LOCALAPPDATA", "").strip()
        if base:
            return Path(base) / "ffroliva" / "gflow-cli"
        return Path.home() / "AppData" / "Local" / "ffroliva" / "gflow-cli"
    if sys_name == "darwin":
        return Path.home() / "Library" / "Application Support" / "ffroliva" / "gflow-cli"
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    base_dir = Path(xdg) if xdg else (Path.home() / ".local" / "share")
    return base_dir / "ffroliva" / "gflow-cli"


def default_ledger_path() -> Path:
    env_p = os.environ.get("GFLOW_TRAFFIC_LEDGER", "").strip()
    if env_p:
        return Path(env_p)
    gemini_dir = os.environ.get("ANTIGRAVITY_GEMINI_DIR", "").strip()
    root = Path(gemini_dir) if gemini_dir else (Path.home() / ".gemini")
    return root / "gflow_traffic_ledger.json"


def find_gflow_bin(explicit: str = "") -> str:
    if explicit and Path(explicit).is_file():
        return str(Path(explicit))
    found = shutil.which("gflow") or shutil.which("gflow-cli")
    if found:
        return found
    candidates = [
        Path.home() / ".local" / "bin" / "gflow.exe",
        Path.home() / ".local" / "bin" / "gflow",
        Path.home() / ".local" / "bin" / "gflow-cli.exe",
        Path.home() / ".local" / "bin" / "gflow-cli",
    ]
    for cand in candidates:
        if cand.is_file():
            return str(cand)
    return ""


def parse_iso_ts(val: Any) -> float:
    if isinstance(val, (int, float)):
        return float(val) if math.isfinite(float(val)) and float(val) > 0 else 0.0
    s = str(val or "").strip()
    if not s:
        return 0.0
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return 0.0


def safe_float(val: Any, default: float = 0.0) -> float:
    try:
        f = float(val)
        return f if math.isfinite(f) else default
    except Exception:
        return default


def safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(round(float(val)))
    except Exception:
        return default


def canonical_model_id(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    low = s.lower()
    if low in MODEL_ALIAS_TO_ID:
        return MODEL_ALIAS_TO_ID[low]
    norm = low.replace("-", "_")
    if norm in MODEL_ALIAS_TO_ID:
        return MODEL_ALIAS_TO_ID[norm]
    return s


def model_label(model_id: str) -> str:
    cid = canonical_model_id(model_id)
    if cid in MODEL_BY_ID:
        return str(MODEL_BY_ID[cid]["label"])
    return model_id or "Unknown"


def is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return code.value == STILL_ACTIVE
                return True
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


# --- Local read-only data readers ---------------------------------------------


def read_default_profile_from_config(gflow_dir: Path) -> str:
    cfg = gflow_dir / "config.toml"
    if not cfg.is_file():
        return ""
    try:
        text = cfg.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r'^\s*default_profile\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
        return m.group(1).strip() if m else ""
    except Exception:
        return ""


def inspect_profile_locks(gflow_dir: Path, now: float) -> tuple[bool, str]:
    """Detect whether another gflow process currently holds a live profile lock."""
    locks_dir = gflow_dir / "locks"
    if not locks_dir.is_dir():
        return False, ""
    for lock_file in sorted(locks_dir.glob("*.lock")):
        try:
            raw = lock_file.read_text(encoding="utf-8", errors="ignore").strip()
            if not raw:
                continue
            pid = 0
            detail = lock_file.stem
            if raw.startswith("{"):
                data = json.loads(raw)
                pid = safe_int(data.get("pid"), 0)
                cmd = str(data.get("command") or data.get("purpose") or "").strip()
                prof = str(data.get("profile") or lock_file.stem).strip()
                detail = f"{prof} ({cmd})" if cmd else prof
            else:
                m = re.search(r"\b(\d{2,7})\b", raw)
                if m:
                    pid = safe_int(m.group(1), 0)
            if pid > 0 and pid != os.getpid() and is_pid_alive(pid):
                return True, f"PID {pid} 正在占用 {detail}"
            # Fallback if lock has no parseable PID: only treat very fresh (<30s) locks as active.
            if pid == 0:
                mtime = lock_file.stat().st_mtime
                if (now - mtime) < 30.0:
                    return True, f"活跃锁 {lock_file.name}"
        except Exception:
            continue
    return False, ""


def read_ledger(ledger_path: Path) -> dict[str, Any]:
    if not ledger_path.is_file():
        return {"present": False, "breaker": {}, "events": [], "updated_at": ""}
    try:
        raw = json.loads(ledger_path.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(raw, dict):
            return {"present": True, "breaker": {}, "events": [], "updated_at": "", "parse_error": "invalid_root"}
        events = raw.get("events") if isinstance(raw.get("events"), list) else []
        breaker = raw.get("breaker") if isinstance(raw.get("breaker"), dict) else {}
        return {
            "present": True,
            "breaker": breaker,
            "events": [e for e in events if isinstance(e, dict)],
            "updated_at": str(raw.get("updated_at") or ""),
        }
    except Exception as exc:
        return {"present": True, "breaker": {}, "events": [], "updated_at": "", "parse_error": str(exc)}


def read_credits_cache(cache_path: Path) -> dict[str, Any]:
    if not cache_path.is_file():
        return {"present": False, "synced": False, "profiles": [], "history": []}
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(raw, dict):
            return {"present": False, "synced": False, "profiles": [], "history": []}
        profiles = raw.get("profiles") if isinstance(raw.get("profiles"), list) else []
        history = raw.get("history") if isinstance(raw.get("history"), list) else []
        synced_at = safe_float(raw.get("synced_at"), 0.0)
        total_raw = raw.get("total_credits")
        total_credits = safe_float(total_raw, -1.0) if total_raw is not None else -1.0
        return {
            "present": True,
            "synced": synced_at > 0 and total_credits >= 0,
            "synced_at": synced_at,
            "total_credits": total_credits if total_credits >= 0 else None,
            "default_profile": str(raw.get("default_profile") or ""),
            "profiles": [p for p in profiles if isinstance(p, dict)],
            "last_error": str(raw.get("last_error") or ""),
            "last_error_at": safe_float(raw.get("last_error_at"), 0.0),
            "history": [h for h in history if isinstance(h, dict)][-CREDITS_HISTORY_MAX:],
        }
    except Exception:
        return {"present": False, "synced": False, "profiles": [], "history": []}


def save_credits_cache(cache_path: Path, payload: dict[str, Any]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_suffix(f".tmp.{os.getpid()}.{int(time.time() * 1000)}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, cache_path)


def _open_sqlite_ro(db_path: Path) -> sqlite3.Connection:
    uri = db_path.resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=2.0)


def read_gflow_db(gflow_dir: Path, now: float) -> dict[str, Any]:
    db_path = gflow_dir / "gflow.db"
    result: dict[str, Any] = {
        "present": db_path.is_file(),
        "path": str(db_path),
        "profiles": [],
        "operations": [],
        "error": "",
    }
    if not db_path.is_file():
        return result
    conn = None
    try:
        conn = _open_sqlite_ro(db_path)
        conn.row_factory = sqlite3.Row
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "profiles" in tables:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(profiles)").fetchall()}
            sel = [c for c in ("name", "profile_dir", "first_seen_at", "last_used_at") if c in cols]
            if sel:
                for r in conn.execute(f"SELECT {', '.join(sel)} FROM profiles").fetchall():
                    d = dict(r)
                    pdir = Path(str(d.get("profile_dir") or "")) if d.get("profile_dir") else (gflow_dir / f"profile_{d.get('name', '')}")
                    email = ""
                    acct_file = pdir / ".gflow_account"
                    if acct_file.is_file():
                        try:
                            email = acct_file.read_text(encoding="utf-8", errors="ignore").strip()
                        except Exception:
                            email = ""
                    cookies_ok = (pdir / "Default" / "Network" / "Cookies").is_file() or (pdir / "Default" / "Cookies").is_file()
                    result["profiles"].append({
                        "profile": str(d.get("name") or ""),
                        "email": email,
                        "cookies_present": cookies_ok,
                        "last_used_at": parse_iso_ts(d.get("last_used_at")),
                        "profile_dir_exists": pdir.is_dir(),
                    })
        if "operations" in tables:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(operations)").fetchall()}
            want = [
                "id", "profile_name", "command", "mode", "model",
                "aspect_ratio", "status", "started_at", "completed_at",
                "error_type", "error_detail", "metadata_json",
            ]
            sel = [c for c in want if c in cols]
            if sel:
                cutoff_iso = datetime.fromtimestamp(now - MONTH_SEC - DAY_SEC, tz=timezone.utc).isoformat()
                order_col = "started_at" if "started_at" in cols else "rowid"
                where = f"WHERE started_at >= ?" if "started_at" in cols else ""
                params = (cutoff_iso,) if where else ()
                sql = f"SELECT {', '.join(sel)} FROM operations {where} ORDER BY {order_col} DESC LIMIT 2000"
                for r in conn.execute(sql, params).fetchall():
                    d = dict(r)
                    ts = parse_iso_ts(d.get("started_at")) or parse_iso_ts(d.get("completed_at"))
                    if ts <= 0 or ts < now - MONTH_SEC:
                        continue
                    meta_credits = 0.0
                    meta_raw = d.get("metadata_json")
                    if isinstance(meta_raw, str) and meta_raw.strip().startswith("{"):
                        try:
                            mj = json.loads(meta_raw)
                            for k in ("credits_used", "credits_spent", "cost_credits", "credits"):
                                if mj.get(k) is not None:
                                    meta_credits = max(0.0, safe_float(mj.get(k), 0.0))
                                    break
                        except Exception:
                            pass
                    result["operations"].append({
                        "ts": ts,
                        "profile": str(d.get("profile_name") or ""),
                        "command": str(d.get("command") or ""),
                        "mode": str(d.get("mode") or ""),
                        "model": canonical_model_id(d.get("model")),
                        "aspect_ratio": str(d.get("aspect_ratio") or ""),
                        "status": str(d.get("status") or "").lower(),
                        "error_type": str(d.get("error_type") or ""),
                        "error_detail": str(d.get("error_detail") or ""),
                        "credits": meta_credits,
                    })
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return result


def scan_workspace_flow_runs(workspace: str, max_depth: int = 5) -> list[dict[str, Any]]:
    if not workspace:
        return []
    root = Path(workspace)
    if not root.is_dir():
        return []
    found: list[dict[str, Any]] = []
    root_depth = len(root.parts)
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            cur = Path(dirpath)
            depth = len(cur.parts) - root_depth
            dirnames[:] = [
                d for d in dirnames
                if d not in WORKSPACE_SKIP_DIRS and not d.startswith(".")
            ]
            if depth >= max_depth:
                dirnames[:] = []
            if "flow_run_state.json" not in filenames:
                continue
            state_file = cur / "flow_run_state.json"
            try:
                raw = json.loads(state_file.read_text(encoding="utf-8", errors="replace"))
                segs = raw.get("segments") if isinstance(raw.get("segments"), dict) else {}
                counts = {"done": 0, "upscaled": 0, "pending": 0, "failed": 0, "uncertain": 0, "blocked": 0}
                credits_used = 0.0
                for seg in segs.values():
                    if not isinstance(seg, dict):
                        continue
                    st = str(seg.get("status") or "PENDING").upper()
                    credits_used += max(0.0, safe_float(seg.get("credits_used"), 0.0))
                    if st == "DONE":
                        counts["done"] += 1
                    elif st == "UPSCALED":
                        counts["upscaled"] += 1
                    elif st == "IN_FLIGHT_UNCERTAIN":
                        counts["uncertain"] += 1
                    elif st == "FAILED":
                        counts["failed"] += 1
                    elif st == "BLOCKED_CREDITS":
                        counts["blocked"] += 1
                    else:
                        counts["pending"] += 1
                rel = str(state_file.relative_to(root)).replace("\\", "/")
                found.append({
                    "episode": cur.name,
                    "path": rel,
                    "total": len(segs),
                    "completed": counts["done"] + counts["upscaled"],
                    "counts": counts,
                    "credits_used": round(credits_used, 1),
                    "updated_at": str(raw.get("updated_at") or ""),
                })
                if len(found) >= 12:
                    break
            except Exception:
                continue
    except Exception:
        pass
    found.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return found


# --- On-demand manual live credit sync (ONLY when explicitly requested) -------


def _is_structlog_dict(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    if "event" in obj and ("level" in obj or "timestamp" in obj or "logger" in obj):
        if not any(k in obj for k in ("profiles", "credits", "total_credits", "status")):
            return True
    return False


def parse_gflow_json_stdout(stdout: str) -> Any:
    text = str(stdout or "")
    dec = json.JSONDecoder()
    idx = 0
    found: list[Any] = []
    n = len(text)
    while idx < n:
        ch = text[idx]
        if ch not in "{[":
            idx += 1
            continue
        try:
            val, end = dec.raw_decode(text, idx)
            if isinstance(val, dict) and not _is_structlog_dict(val):
                found.append(val)
            elif isinstance(val, list):
                found.append(val)
            idx = end
        except Exception:
            idx += 1
    if not found:
        return None
    for item in reversed(found):
        if isinstance(item, dict) and any(
            k in item for k in ("profiles", "total_credits", "credits")
        ):
            return item
    return found[-1]


def _extract_profiles_from_credits_payload(payload: Any) -> tuple[list[dict[str, Any]], float | None, str]:
    if not isinstance(payload, dict):
        return [], None, ""
    profiles_out: list[dict[str, Any]] = []
    default_prof = ""

    raw_profiles = payload.get("profiles")
    if isinstance(raw_profiles, list):
        for p in raw_profiles:
            if not isinstance(p, dict):
                continue
            cred = p.get("credits")
            cred_val = safe_float(cred, -1.0) if cred is not None else -1.0
            entry = {
                "profile": str(p.get("profile") or p.get("name") or "default"),
                "email": str(p.get("email") or ""),
                "is_default": bool(p.get("is_default")),
                "authenticated": bool(p.get("authenticated", cred_val >= 0)),
                "status": str(p.get("status") or ("ok" if cred_val >= 0 else "unknown")),
                "credits": cred_val if cred_val >= 0 else None,
            }
            if entry["is_default"] and not default_prof:
                default_prof = entry["profile"]
            profiles_out.append(entry)
        tot = payload.get("total_credits")
        if tot is not None:
            tot_val = safe_float(tot, -1.0)
            if tot_val >= 0:
                return profiles_out, tot_val, default_prof
        vals = [p["credits"] for p in profiles_out if p["credits"] is not None]
        return profiles_out, (sum(vals) if vals else None), default_prof

    if payload.get("credits") is not None:
        cred_val = safe_float(payload.get("credits"), -1.0)
        prof_name = str(payload.get("profile") or "default")
        profiles_out.append({
            "profile": prof_name,
            "email": str(payload.get("email") or ""),
            "is_default": bool(payload.get("is_default", True)),
            "authenticated": bool(payload.get("authenticated", cred_val >= 0)),
            "status": str(payload.get("status") or "ok"),
            "credits": cred_val if cred_val >= 0 else None,
        })
        return profiles_out, (cred_val if cred_val >= 0 else None), prof_name

    return [], None, ""


def sync_live_credits(
    gflow_bin: str,
    gflow_dir: Path,
    cache_path: Path,
    now: float,
    force_ignore_busy: bool = False,
    timeout_sec: int = 45,
) -> dict[str, Any]:
    existing = read_credits_cache(cache_path)
    if not gflow_bin:
        return {"ok": False, "skipped_busy": False, "error": "未检测到 gflow / gflow-cli 可执行文件"}

    busy, busy_detail = inspect_profile_locks(gflow_dir, now)
    if busy and not force_ignore_busy:
        return {
            "ok": False,
            "skipped_busy": True,
            "error": f"检测到 Flow 正在运行（{busy_detail}），为避免抢占 Profile 锁已跳过同步",
        }

    def _record_error(msg: str) -> dict[str, Any]:
        merged = {
            "version": CREDITS_CACHE_VERSION,
            "synced_at": existing.get("synced_at", 0.0),
            "total_credits": existing.get("total_credits"),
            "default_profile": existing.get("default_profile", ""),
            "profiles": existing.get("profiles", []),
            "history": existing.get("history", []),
            "last_error": msg[:400],
            "last_error_at": now,
        }
        try:
            save_credits_cache(cache_path, merged)
        except Exception:
            pass
        return {"ok": False, "skipped_busy": False, "error": msg}

    cmd_attempts = [
        [gflow_bin, "credits", "list", "--json"],
        [gflow_bin, "credits", "user", "--json"],
    ]
    last_err = ""
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

    for cmd in cmd_attempts:
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_sec,
                creationflags=creationflags,
            )
        except subprocess.TimeoutExpired:
            last_err = f"同步超时（>{timeout_sec}s），代理下需等待浏览器读取 Flow 余额"
            continue
        except Exception as exc:
            last_err = f"启动 gflow 失败：{exc}"
            continue

        parsed = parse_gflow_json_stdout(proc.stdout)
        profiles, total_credits, default_prof = _extract_profiles_from_credits_payload(parsed)
        if proc.returncode == 0 and total_credits is not None:
            hist = list(existing.get("history", []))
            if not hist or abs(safe_float(hist[-1].get("total_credits"), -1) - total_credits) > 1e-6 or (now - safe_float(hist[-1].get("ts"), 0)) >= 300:
                hist.append({"ts": round(now), "total_credits": round(total_credits, 1)})
                hist = hist[-CREDITS_HISTORY_MAX:]
            payload = {
                "version": CREDITS_CACHE_VERSION,
                "synced_at": now,
                "total_credits": round(total_credits, 1),
                "default_profile": default_prof or read_default_profile_from_config(gflow_dir),
                "profiles": profiles,
                "history": hist,
                "last_error": "",
                "last_error_at": 0.0,
            }
            save_credits_cache(cache_path, payload)
            return {
                "ok": True,
                "skipped_busy": False,
                "total_credits": round(total_credits, 1),
                "profile_count": len(profiles),
                "synced_at": round(now),
                "error": "",
            }
        err_msg = (proc.stderr or proc.stdout or f"exit={proc.returncode}").strip()
        last_err = err_msg.splitlines()[-1] if err_msg else f"gflow 退出码 {proc.returncode}"

    return _record_error(last_err or "无法解析 gflow 积分输出")


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
    gflow_bin = find_gflow_bin(gflow_bin_override)
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
        gbin = find_gflow_bin(args.gflow_bin)
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


if __name__ == "__main__":
    raise SystemExit(main())
