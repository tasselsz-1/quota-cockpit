#!/usr/bin/env python3
"""Unit tests for Google Flow video quota & rate-limit reporter (flow_report.py)."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sidecars" / "cockpit"))

import flow_report as fr  # noqa: E402


def _create_mock_gflow_db(gflow_dir: Path, operations: list[tuple], profiles: list[tuple] | None = None) -> Path:
    db_path = gflow_dir / "gflow.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE profiles (
            name TEXT,
            profile_dir TEXT,
            first_seen_at TEXT,
            last_used_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE operations (
            id TEXT,
            profile_name TEXT,
            command TEXT,
            mode TEXT,
            model TEXT,
            aspect_ratio TEXT,
            status TEXT,
            started_at TEXT,
            completed_at TEXT,
            error_type TEXT,
            error_detail TEXT,
            metadata_json TEXT
        )
        """
    )
    for row in profiles or []:
        conn.execute("INSERT INTO profiles VALUES (?, ?, ?, ?)", row)
    for row in operations:
        conn.execute("INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row)
    conn.commit()
    conn.close()
    return db_path


class TestFlowReport(unittest.TestCase):
    def test_ledger_db_credits_post_sync_deduction_and_models(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            gflow_dir = tdir / "gflow"
            gflow_dir.mkdir()
            prof_dir = gflow_dir / "profile_demo"
            (prof_dir / "Default" / "Network").mkdir(parents=True)
            (prof_dir / ".gflow_account").write_text("demo@example.com\n", encoding="utf-8")
            (prof_dir / "Default" / "Network" / "Cookies").write_bytes(b"mock")
            (gflow_dir / "config.toml").write_text('default_profile = "demo"\n', encoding="utf-8")

            iso = lambda offset: fr.datetime.fromtimestamp(now + offset, tz=fr.timezone.utc).isoformat()
            _create_mock_gflow_db(
                gflow_dir,
                profiles=[("demo", str(prof_dir), iso(-86400), iso(-60))],
                operations=[
                    ("op1", "demo", "video i2v", "i2v", "veo_3_1_lite", "16:9", "succeeded", iso(-1800), iso(-1750), "", "", None),
                    ("op2", "demo", "video i2v", "i2v", "omni_1_2_flash", "16:9", "succeeded", iso(-600), iso(-540), "", "", None),
                    # Extra direct/MCP video op not in ledger -> untracked_db_24h == 1
                    ("op3", "demo", "video i2v", "i2v", "veo_3_1_lite", "16:9", "succeeded", iso(-300), iso(-250), "", "", None),
                    ("op4", "demo", "video i2v", "i2v", "veo_3_1_lite", "16:9", "failed", iso(-120), iso(-110), "waf-rejection", "PUBLIC_ERROR_UNUSUAL_ACTIVITY", None),
                    ("op5", "demo", "character create", "character", "", "", "succeeded", iso(-7200), iso(-7180), "", "", None),
                ],
            )

            ledger_path = tdir / "gflow_traffic_ledger.json"
            ledger_data = {
                "version": 1,
                "breaker": {"active": False, "requires_manual_reset": False, "until_ts": 0.0, "reason": ""},
                "events": [
                    {"ts": now - 1800, "action": "i2v", "episode": "ep01", "segment": "seg_01", "credits": 10.0, "outcome": "OK", "exit_code": 0},
                    {"ts": now - 600, "action": "i2v", "episode": "ep01", "segment": "seg_02", "credits": 20.0, "outcome": "OK", "exit_code": 0},
                    {"ts": now - 120, "action": "i2v", "episode": "ep01", "segment": "seg_03", "credits": 0.0, "outcome": "AUTH_OR_CAPTCHA", "exit_code": 10},
                ],
                "updated_at": iso(-120),
            }
            ledger_path.write_text(json.dumps(ledger_data), encoding="utf-8")

            cache_path = tdir / "flow-credits-cache.json"
            # Synced at now - 1000 (after seg_01, before seg_02 which spent 20)
            cache_path.write_text(json.dumps({
                "version": 1,
                "synced_at": now - 1000,
                "total_credits": 1000.0,
                "default_profile": "demo",
                "profiles": [{"profile": "demo", "email": "demo@example.com", "is_default": True, "authenticated": True, "credits": 1000.0}],
                "history": [{"ts": round(now - 1000), "total_credits": 1000.0}],
            }), encoding="utf-8")

            rep = fr.build_report(
                ledger_path=ledger_path,
                gflow_dir=gflow_dir,
                credits_cache_path=cache_path,
                now=now,
                gflow_bin_override="",
            )
            self.assertTrue(rep["available"])
            self.assertEqual(rep["data_sources"]["rate_limit_source"], "ledger")
            self.assertEqual(rep["health"], "ok")
            # Post-sync spend = 20 -> estimated = 980, spendable = 980 - 100 = 880
            self.assertEqual(rep["credits"]["synced_total"], 1000.0)
            self.assertEqual(rep["credits"]["spent_since_sync"], 20.0)
            self.assertEqual(rep["credits"]["estimated_total"], 980.0)
            self.assertEqual(rep["credits"]["spendable"], 880.0)
            # Rolling stats
            self.assertEqual(rep["rolling"]["hour_used"], 3)
            self.assertEqual(rep["rolling"]["day_used"], 3)
            self.assertEqual(rep["rolling"]["day_credits_used"], 30.0)
            self.assertEqual(rep["rolling"]["db_video_24h"], 4)
            self.assertEqual(rep["rolling"]["untracked_db_24h"], 1)
            # Model estimates: veo-lite (10) -> 88 segments; omni-1.2 (20) -> 44 segments
            by_id = {m["id"]: m for m in rep["model_estimates"]}
            self.assertEqual(by_id["veo_3_1_lite"]["est_segments"], 88)
            self.assertEqual(by_id["omni_1_2_flash"]["est_segments"], 44)
            self.assertEqual(len(rep["retiring_models"]), 3)
            self.assertEqual(rep["usage"]["window_30d"]["waf"], 1)

    def test_manual_and_timed_circuit_breaker(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            ledger_path = tdir / "ledger.json"
            cache_path = tdir / "cache.json"

            # 1. Manual breaker
            ledger_path.write_text(json.dumps({
                "version": 1,
                "breaker": {"active": True, "requires_manual_reset": True, "until_ts": 0, "reason": "WAF exit=10"},
                "events": [],
            }), encoding="utf-8")
            rep1 = fr.build_report(ledger_path, tdir / "nogflow", cache_path, now=now)
            self.assertEqual(rep1["health"], "locked")
            self.assertTrue(rep1["breaker"]["open"])
            self.assertTrue(rep1["breaker"]["requires_manual_reset"])

            # 2. Active timed breaker
            ledger_path.write_text(json.dumps({
                "version": 1,
                "breaker": {"active": True, "requires_manual_reset": False, "until_ts": now + 1200, "reason": "429"},
                "events": [],
            }), encoding="utf-8")
            rep2 = fr.build_report(ledger_path, tdir / "nogflow", cache_path, now=now)
            self.assertEqual(rep2["health"], "locked")
            self.assertEqual(rep2["breaker"]["remaining_sec"], 1200)

            # 3. Expired timed breaker -> ok
            ledger_path.write_text(json.dumps({
                "version": 1,
                "breaker": {"active": True, "requires_manual_reset": False, "until_ts": now - 10, "reason": "429"},
                "events": [],
            }), encoding="utf-8")
            rep3 = fr.build_report(ledger_path, tdir / "nogflow", cache_path, now=now)
            self.assertFalse(rep3["breaker"]["open"])
            self.assertEqual(rep3["health"], "ok")

    def test_gflow_db_fallback_without_ledger(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            gflow_dir = tdir / "gflow"
            gflow_dir.mkdir()
            iso = lambda offset: fr.datetime.fromtimestamp(now + offset, tz=fr.timezone.utc).isoformat()
            _create_mock_gflow_db(
                gflow_dir,
                operations=[
                    ("a1", "p", "video i2v", "i2v", "veo_3_1_lite", "16:9", "succeeded", iso(-600), iso(-550), "", "", None),
                    ("a2", "p", "video r2v", "r2v", "omni_1_2_flash", "16:9", "succeeded", iso(-300), iso(-240), "", "", None),
                ],
            )
            rep = fr.build_report(
                ledger_path=tdir / "missing_ledger.json",
                gflow_dir=gflow_dir,
                credits_cache_path=tdir / "missing_cache.json",
                now=now,
            )
            self.assertTrue(rep["available"])
            self.assertEqual(rep["data_sources"]["rate_limit_source"], "gflow_db")
            self.assertEqual(rep["rolling"]["hour_used"], 2)
            self.assertEqual(rep["rolling"]["day_credits_used"], 30.0)
            self.assertEqual(len(rep["recent_events"]), 2)

    def test_unavailable_graceful_degradation(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            orig_find = fr.find_gflow_bin
            fr.find_gflow_bin = lambda explicit="": ""
            try:
                rep = fr.build_report(
                    ledger_path=tdir / "missing.json",
                    gflow_dir=tdir / "missing_gflow",
                    credits_cache_path=tdir / "missing_cache.json",
                    now=now,
                )
            finally:
                fr.find_gflow_bin = orig_find
            self.assertFalse(rep["available"])
            self.assertEqual(rep["health"], "unavailable")

    def test_live_profile_lock_blocks_sync(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            gflow_dir = tdir / "gflow"
            locks_dir = gflow_dir / "locks"
            locks_dir.mkdir(parents=True)
            # Dead/nonexistent PID should NOT block
            (locks_dir / "profile_stale.lock").write_text(json.dumps({"pid": 99999999, "profile": "demo"}), encoding="utf-8")
            busy0, _ = fr.inspect_profile_locks(gflow_dir, now)
            self.assertFalse(busy0)

            # Live PID (use parent PID so pid != os.getpid() and is guaranteed alive)
            ppid = os.getppid()
            if ppid > 0 and ppid != os.getpid() and fr.is_pid_alive(ppid):
                (locks_dir / "profile_live.lock").write_text(
                    json.dumps({"pid": ppid, "profile": "demo", "command": "video i2v"}),
                    encoding="utf-8",
                )
                busy1, detail = fr.inspect_profile_locks(gflow_dir, now)
                self.assertTrue(busy1)
                self.assertIn(str(ppid), detail)
                res = fr.sync_live_credits("mock-gflow", gflow_dir, tdir / "cache.json", now)
                self.assertFalse(res["ok"])
                self.assertTrue(res["skipped_busy"])

    def test_parse_gflow_json_stdout_skips_structlog(self) -> None:
        raw = (
            '{"event": "browser_launch", "level": "info", "timestamp": "2026-11-26T00:00:00Z"}\n'
            'some log line\n'
            '{"status": "ok", "profiles": [{"profile": "alpha", "email": "a@example.com", "is_default": true, "authenticated": true, "credits": 4200}], "total_credits": 4200, "count": 1}\n'
        )
        parsed = fr.parse_gflow_json_stdout(raw)
        profiles, total, default_prof = fr._extract_profiles_from_credits_payload(parsed)
        self.assertEqual(total, 4200.0)
        self.assertEqual(default_prof, "alpha")
        self.assertEqual(len(profiles), 1)

    def test_workspace_flow_run_state_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            ep_dir = ws / "work" / "ep01"
            ep_dir.mkdir(parents=True)
            (ep_dir / "flow_run_state.json").write_text(json.dumps({
                "version": 1,
                "segments": {
                    "segment_01": {"status": "DONE", "credits_used": 10},
                    "segment_02": {"status": "UPSCALED", "credits_used": 20},
                    "segment_03": {"status": "FAILED", "credits_used": 0},
                    "segment_04": {"status": "IN_FLIGHT_UNCERTAIN", "credits_used": 0},
                    "segment_05": {"status": "PENDING", "credits_used": 0},
                },
                "updated_at": "2026-11-26T12:00:00+08:00",
            }), encoding="utf-8")
            runs = fr.scan_workspace_flow_runs(str(ws))
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["episode"], "ep01")
            self.assertEqual(runs[0]["completed"], 2)
            self.assertEqual(runs[0]["total"], 5)
            self.assertEqual(runs[0]["counts"]["uncertain"], 1)
            self.assertEqual(runs[0]["credits_used"], 30.0)


if __name__ == "__main__":
    unittest.main()
