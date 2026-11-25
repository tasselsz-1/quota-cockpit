#!/usr/bin/env python3
"""Unit tests for pool_usage_report.py (local 7-day per-pool usage scanner)."""

import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

SIDECAR_DIR = Path(__file__).resolve().parent.parent / "sidecars" / "cockpit"
if str(SIDECAR_DIR) not in sys.path:
    sys.path.insert(0, str(SIDECAR_DIR))

import pool_usage_report  # noqa: E402


def encode_varint(val: int) -> bytes:
    out = bytearray()
    v = int(val)
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def pb_varint(field_num: int, val: int) -> bytes:
    return encode_varint((field_num << 3) | 0) + encode_varint(val)


def pb_bytes(field_num: int, payload: bytes) -> bytes:
    return encode_varint((field_num << 3) | 2) + encode_varint(len(payload)) + payload


def make_planner_md(ts: int, provider: int, uncached_in: int, cache_read: int, out_tok: int, model_enum: int = 1535) -> bytes:
    ts_msg = pb_varint(1, int(ts))
    mu = (
        pb_varint(1, model_enum)
        + pb_varint(2, uncached_in)
        + pb_varint(3, out_tok)
        + pb_varint(5, cache_read)
        + pb_varint(6, provider)
    )
    return pb_bytes(1, ts_msg) + pb_bytes(9, mu)


def make_gen_meta(model_slug: str) -> bytes:
    planner = pb_bytes(28, model_slug.encode("utf-8"))
    return pb_bytes(3, planner)


def create_usage_db(db_path: Path, step_rows: list[tuple[int, int, bytes]], model_slug: str, mtime: float) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE steps (idx INTEGER, step_type INTEGER, metadata BLOB)")
        conn.execute("CREATE TABLE gen_metadata (idx INTEGER, data BLOB)")
        conn.executemany("INSERT INTO steps (idx, step_type, metadata) VALUES (?, ?, ?)", step_rows)
        if model_slug:
            conn.execute("INSERT INTO gen_metadata (idx, data) VALUES (0, ?)", (make_gen_meta(model_slug),))
        conn.commit()
    finally:
        conn.close()
    os.utime(db_path, (mtime, mtime))


class PoolUsageReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conv_dir = self.root / "conversations"
        self.conv_dir.mkdir()
        self.now = 1_760_000_000.0

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_scan_pools_tier_weighting_window_filter_and_cache(self) -> None:
        # DB 1: Gemini Neon (heavy tier = 1.0x) with 1 expired turn (>7d) + 2 recent turns
        t_expired = int(self.now - 7.5 * 86400)
        t_day3 = int(self.now - 3 * 86400)
        t_recent = int(self.now - 1800)
        create_usage_db(
            self.conv_dir / "conv-neon.db",
            [
                (1, 15, make_planner_md(t_expired, 24, 50_000, 100_000, 2_000)),
                (2, 15, make_planner_md(t_day3, 24, 10_000, 50_000, 1_000)),
                (3, 15, make_planner_md(t_recent, 24, 10_000, 50_000, 1_000)),
                # Non-planner step ignored
                (4, 14, make_planner_md(t_recent, 24, 999_999, 0, 0)),
            ],
            "gemini-4-neon-xhigh",
            self.now - 10,
        )

        # DB 2: Gemini Flash (light tier = 0.25x) with identical tokens to verify tier multiplier
        create_usage_db(
            self.conv_dir / "conv-flash.db",
            [
                (1, 15, make_planner_md(t_recent + 60, 24, 10_000, 50_000, 1_000)),
            ],
            "gemini-3.8-flash-medium",
            self.now - 10,
        )

        # DB 3: Claude Opus (Pool B, provider 26)
        t_claude = int(self.now - 2 * 86400)
        create_usage_db(
            self.conv_dir / "conv-claude.db",
            [
                (1, 15, make_planner_md(t_claude, 26, 20_000, 80_000, 2_000, model_enum=1404)),
            ],
            "claude-opus-5-5-high",
            self.now - 10,
        )

        cache_file = self.root / "pool-usage-cache.json"
        r1 = pool_usage_report.build_pool_usage_report(self.conv_dir, now=self.now, cache_path=cache_file)
        self.assertEqual(r1["scan_meta"]["scanned_dbs"], 3)
        self.assertEqual(r1["scan_meta"]["cache_hits"], 0)

        pa = r1["pools"]["pool_a"]
        pb = r1["pools"]["pool_b"]
        # Expired turn excluded; 2 Neon + 1 Flash = 3 turns in 7d, 2 turns in 5h
        self.assertEqual(pa["turns_7d"], 3)
        self.assertEqual(pa["turns_5h"], 2)
        self.assertEqual(pa["uncached_in_7d"], 30_000)
        self.assertEqual(pa["cache_read_7d"], 150_000)
        self.assertEqual(pa["out_7d"], 3_000)
        # Base turn weight = 10_000 + 50_000*0.1 + 1_000*5 = 20_000; 2x Neon (1.0) + 1x Flash (0.25) = 45_000
        self.assertAlmostEqual(pa["weighted_7d"], 45_000.0, delta=1.0)

        self.assertEqual(pb["turns_7d"], 1)
        self.assertEqual(pb["turns_5h"], 0)
        # 20_000 + 80_000*0.1 + 2_000*5 = 38_000 * 1.0
        self.assertAlmostEqual(pb["weighted_7d"], 38_000.0, delta=1.0)

        # Warm second call hits cache for all 3 DBs
        r2 = pool_usage_report.build_pool_usage_report(self.conv_dir, now=self.now, cache_path=cache_file)
        self.assertEqual(r2["scan_meta"]["scanned_dbs"], 0)
        self.assertEqual(r2["scan_meta"]["cache_hits"], 3)
        self.assertEqual(r2["pools"]["pool_a"]["turns_7d"], 3)

    def test_db_gc_preserves_7d_archived_pool_usage_and_prunes_expired(self) -> None:
        t_day2 = int(self.now - 2 * 86400)
        db_old = self.conv_dir / "conv-old-gc.db"
        db_empty = self.conv_dir / "conv-empty-gc.db"
        create_usage_db(
            db_old,
            [(1, 15, make_planner_md(t_day2, 24, 15_000, 60_000, 1_500))],
            "gemini-4-neon-xhigh",
            t_day2 + 60,
        )
        create_usage_db(db_empty, [], "", t_day2 + 60)

        cache_file = self.root / "pool-usage-cache.json"
        r1 = pool_usage_report.build_pool_usage_report(self.conv_dir, now=self.now, cache_path=cache_file)
        self.assertEqual(r1["pools"]["pool_a"]["turns_7d"], 1)
        self.assertEqual(r1["scan_meta"]["archived_dbs"], 0)

        # Simulate Antigravity GC deleting both DBs
        db_old.unlink()
        db_empty.unlink()

        r2 = pool_usage_report.build_pool_usage_report(self.conv_dir, now=self.now + 600, cache_path=cache_file)
        self.assertEqual(r2["scan_meta"]["active_dbs"], 0)
        self.assertEqual(r2["scan_meta"]["archived_dbs"], 1)  # empty DB pruned, non-empty archived
        self.assertEqual(r2["pools"]["pool_a"]["turns_7d"], 1)

        # Once 7 days pass, archived buckets expire and get pruned cleanly
        r3 = pool_usage_report.build_pool_usage_report(self.conv_dir, now=self.now + 6 * 86400, cache_path=cache_file)
        self.assertEqual(r3["scan_meta"]["archived_dbs"], 0)
        self.assertEqual(r3["pools"]["pool_a"]["turns_7d"], 0)


if __name__ == "__main__":
    unittest.main()
