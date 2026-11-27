"""Local read-only data readers for Google Flow quota & rate-limit reporting."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any

from .constants import (
    CREDITS_HISTORY_MAX,
    DAY_SEC,
    DEFAULT_PROJECT_ROTATE_SEGMENTS,
    DEFAULT_PROJECT_ROTATE_TILES,
    MONTH_SEC,
    WORKSPACE_SKIP_DIRS,
    canonical_model_id,
    is_pid_alive,
    parse_iso_ts,
    safe_float,
    safe_int,
)

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


def inspect_single_flight_lock(lock_path: Path, now: float) -> dict[str, Any]:
    """Read-only inspection of the shared ~/.gemini/gflow_single_flight.lock file."""
    res: dict[str, Any] = {
        "present": False,
        "active": False,
        "pid": 0,
        "age_sec": 0,
        "detail": "",
        "path": str(lock_path),
    }
    if not lock_path.is_file():
        return res
    res["present"] = True
    try:
        raw = lock_path.read_text(encoding="utf-8", errors="ignore").strip()
        pid = 0
        lock_ts = 0.0
        if raw.startswith("{"):
            data = json.loads(raw)
            pid = safe_int(data.get("pid"), 0)
            lock_ts = safe_float(data.get("ts"), 0.0) or parse_iso_ts(data.get("created_at"))
        else:
            m = re.search(r"\b(\d{2,7})\b", raw)
            if m:
                pid = safe_int(m.group(1), 0)
        if lock_ts <= 0:
            lock_ts = lock_path.stat().st_mtime
        age_sec = max(0, int(round(now - lock_ts)))
        res["pid"] = pid
        res["age_sec"] = age_sec
        if pid > 0 and pid != os.getpid() and is_pid_alive(pid):
            res["active"] = True
            res["detail"] = f"全局单飞锁占用 (PID {pid})"
        elif pid == 0 and age_sec < 30:
            res["active"] = True
            res["detail"] = "全局单飞锁活跃"
    except Exception:
        pass
    return res


def read_ledger(ledger_path: Path) -> dict[str, Any]:
    empty = {
        "present": False,
        "version": 1,
        "breaker": {},
        "model_breakers": {},
        "last_waf_ts": 0.0,
        "last_heat_rest_ts": 0.0,
        "events": [],
        "updated_at": "",
    }
    if not ledger_path.is_file():
        return empty
    try:
        raw = json.loads(ledger_path.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(raw, dict):
            return {**empty, "present": True, "parse_error": "invalid_root"}
        events = raw.get("events") if isinstance(raw.get("events"), list) else []
        breaker = raw.get("breaker") if isinstance(raw.get("breaker"), dict) else {}
        model_breakers = raw.get("model_breakers") if isinstance(raw.get("model_breakers"), dict) else {}
        return {
            "present": True,
            "version": safe_int(raw.get("version"), 1),
            "breaker": breaker,
            "model_breakers": model_breakers,
            "last_waf_ts": safe_float(raw.get("last_waf_ts"), 0.0),
            "last_heat_rest_ts": safe_float(raw.get("last_heat_rest_ts"), 0.0),
            "events": [e for e in events if isinstance(e, dict)],
            "updated_at": str(raw.get("updated_at") or ""),
        }
    except Exception as exc:
        return {**empty, "present": True, "parse_error": str(exc)}


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


def _read_video_project_rotation(ep_dir: Path) -> dict[str, Any] | None:
    idx_file = ep_dir / "flow_project_index.json"
    if not idx_file.is_file():
        return None
    try:
        raw = json.loads(idx_file.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(raw, dict):
            return None
        projects = raw.get("projects") if isinstance(raw.get("projects"), dict) else {}
        active_pid = str(raw.get("active_project_id") or "").strip()
        rotate_limit = max(1, safe_int(raw.get("rotate_every_segments"), DEFAULT_PROJECT_ROTATE_SEGMENTS))
        active_entry = projects.get(active_pid) if active_pid else None
        if not isinstance(active_entry, dict) and projects:
            active_entry = next(
                (v for v in projects.values() if isinstance(v, dict) and v.get("status") == "active"),
                None,
            )
        used = max(0, safe_int((active_entry or {}).get("submitted_count"), 0))
        return {
            "used": used,
            "limit": rotate_limit,
            "unit": "段",
            "project_count": len(projects),
        }
    except Exception:
        return None


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
            if "flow_run_state.json" in filenames:
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
                    entry: dict[str, Any] = {
                        "kind": "video",
                        "episode": cur.name,
                        "path": rel,
                        "total": len(segs),
                        "completed": counts["done"] + counts["upscaled"],
                        "counts": counts,
                        "credits_used": round(credits_used, 1),
                        "updated_at": str(raw.get("updated_at") or ""),
                    }
                    proj_rot = _read_video_project_rotation(cur)
                    if proj_rot is not None:
                        entry["project_rotation"] = proj_rot
                    found.append(entry)
                except Exception:
                    pass
            if "flow_image_state.json" in filenames:
                img_state_file = cur / "flow_image_state.json"
                try:
                    raw = json.loads(img_state_file.read_text(encoding="utf-8", errors="replace"))
                    jobs = raw.get("jobs") if isinstance(raw.get("jobs"), dict) else {}
                    counts = {"done": 0, "upscaled": 0, "pending": 0, "failed": 0, "uncertain": 0, "blocked": 0}
                    latest_upd = str(raw.get("updated_at") or "")
                    cur_pid = str(raw.get("flow_project_id") or "").strip()
                    inferred_tiles = 0
                    for job in jobs.values():
                        if not isinstance(job, dict):
                            continue
                        st = str(job.get("status") or "PENDING").upper()
                        j_upd = str(job.get("updated_at") or "")
                        if j_upd and j_upd > latest_upd:
                            latest_upd = j_upd
                        if st == "DONE":
                            if str(job.get("upscaled_scale") or "").strip():
                                counts["upscaled"] += 1
                            else:
                                counts["done"] += 1
                            if cur_pid and str(job.get("project_id") or "") == cur_pid:
                                inferred_tiles += max(1, safe_int(job.get("count"), 1))
                        elif st in ("QUOTA_DEFERRED", "BLOCKED"):
                            counts["blocked"] += 1
                        elif st in ("FAILED", "CONTENT_POLICY", "SAME_IMAGE_BLOCKED"):
                            counts["failed"] += 1
                        else:
                            counts["pending"] += 1
                    raw_tiles = raw.get("flow_project_tiles")
                    if raw_tiles is not None:
                        used_tiles = max(0, safe_int(raw_tiles, 0))
                    elif raw.get("flow_project_count") is not None:
                        used_tiles = max(0, safe_int(raw.get("flow_project_count"), 0))
                    else:
                        used_tiles = inferred_tiles
                    hist = raw.get("flow_project_history") if isinstance(raw.get("flow_project_history"), list) else []
                    proj_cnt = len(hist) + (1 if cur_pid else 0)
                    rel = str(img_state_file.relative_to(root)).replace("\\", "/")
                    found.append({
                        "kind": "image",
                        "episode": cur.name,
                        "path": rel,
                        "total": len(jobs),
                        "completed": counts["done"] + counts["upscaled"],
                        "counts": counts,
                        "credits_used": 0.0,
                        "project_rotation": {
                            "used": used_tiles,
                            "limit": DEFAULT_PROJECT_ROTATE_TILES,
                            "unit": "tiles",
                            "project_count": proj_cnt,
                        },
                        "updated_at": latest_upd,
                    })
                except Exception:
                    pass
            if len(found) >= 16:
                break
    except Exception:
        pass
    found.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return found[:12]
