"""Constants, model catalog, and basic utility helpers for flow_quota."""

from __future__ import annotations

import ctypes
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import platform
import shutil
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
