"""On-demand manual live credit sync helpers for Google Flow."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from . import constants
from .constants import CREDITS_CACHE_VERSION, CREDITS_HISTORY_MAX, safe_float
from .readers import (
    inspect_profile_locks,
    read_credits_cache,
    read_default_profile_from_config,
    save_credits_cache,
)


def _resolve_gflow_bin(explicit: str = "") -> str:
    facade = sys.modules.get("flow_report")
    fn = getattr(facade, "find_gflow_bin", None) if facade is not None else None
    if callable(fn):
        return fn(explicit)
    return constants.find_gflow_bin(explicit)


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
