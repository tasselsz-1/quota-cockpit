"""Constants, token/pricing estimators, and timestamp/file-stamp helpers."""

import datetime
from pathlib import Path
import re
from typing import Any

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


KNOWN_EVENT_KINDS = ("OK", "EOF", "ERR", "RUNNING", "429", "429_WEEKLY", "429_RPM", "CLIENT_ERR")
