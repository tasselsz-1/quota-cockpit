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

    def test_v2_cross_modal_waf_window_image_pools_and_unsubmitted_exclusion(self) -> None:
        # 2027-01-15T08:00:00Z (non-peak UTC 08:00)
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            gflow_dir = tdir / "gflow"
            gflow_dir.mkdir()
            iso = lambda offset: fr.datetime.fromtimestamp(now + offset, tz=fr.timezone.utc).isoformat()
            _create_mock_gflow_db(
                gflow_dir,
                operations=[
                    ("op_v1", "demo", "video i2v", "i2v", "veo_3_1_lite", "16:9", "succeeded", iso(-300), iso(-250), "", "", None),
                    ("op_i1", "demo", "image t2i", "t2i", "NARWHAL", "16:9", "succeeded", iso(-200), iso(-170), "", "", None),
                    ("op_u1", "demo", "image upscale", "upscale", "4k", "", "succeeded", iso(-100), iso(-80), "", "", None),
                ],
            )
            ledger_path = tdir / "gflow_traffic_ledger.json"
            events = [
                # 5 older video events in 1h (>10m ago) to prove hour cap disabled (max_per_hour=0)
                {"ts": now - 2400, "action": "i2v", "episode": "ep01", "segment": "seg_01", "credits": 10.0, "outcome": "OK", "exit_code": 0, "recaptcha_tokens": 1},
                {"ts": now - 2100, "action": "i2v", "episode": "ep01", "segment": "seg_02", "credits": 10.0, "outcome": "OK", "exit_code": 0, "recaptcha_tokens": 1},
                {"ts": now - 1800, "action": "i2v", "episode": "ep01", "segment": "seg_03", "credits": 10.0, "outcome": "OK", "exit_code": 0, "recaptcha_tokens": 1},
                {"ts": now - 1500, "action": "i2v", "episode": "ep01", "segment": "seg_04", "credits": 10.0, "outcome": "OK", "exit_code": 0, "recaptcha_tokens": 1},
                {"ts": now - 1200, "action": "i2v", "episode": "ep01", "segment": "seg_05", "credits": 10.0, "outcome": "OK", "exit_code": 0, "recaptcha_tokens": 1},
                # Within 10m: i2v (1 tok) + image_t2i 4x (1 tok, 4 quota_units) + image_upscale 4k (1 tok, 1 quota_unit)
                {"ts": now - 300, "submit_start_ts": now - 310, "action": "i2v", "episode": "ep01", "segment": "seg_06", "credits": 10.0, "outcome": "OK", "exit_code": 0, "recaptcha_tokens": 1},
                {"ts": now - 200, "submit_start_ts": now - 215, "action": "image_t2i", "episode": "ep01", "segment": "img_01", "model": "nano2", "quota_pool": "nano2_shared", "quota_units": 4, "credits": 0.0, "outcome": "OK", "exit_code": 0, "recaptcha_tokens": 1},
                {"ts": now - 100, "submit_start_ts": now - 105, "action": "image_upscale", "episode": "ep01", "segment": "img_01", "model": "4k", "quota_pool": "image_upscale_4k", "quota_units": 1, "credits": 0.0, "outcome": "OK", "exit_code": 0, "recaptcha_tokens": 1},
                # Character action (0 WAF tokens) + pre-submit local failure (0 WAF tokens, excluded)
                {"ts": now - 90, "action": "character", "episode": "ep01", "segment": "hero", "credits": 0.0, "outcome": "OK", "exit_code": 0, "recaptcha_tokens": 0},
                {"ts": now - 10, "action": "image_t2i", "episode": "ep01", "segment": "img_02", "model": "nano-pro", "quota_pool": "nano_pro", "quota_units": 4, "credits": 0.0, "outcome": "LOCAL_PRECHECK_FAIL", "exit_code": 2, "recaptcha_tokens": 0},
            ]
            ledger_path.write_text(json.dumps({
                "version": 2,
                "breaker": {"active": False, "requires_manual_reset": False, "until_ts": 0.0, "reason": ""},
                "model_breakers": {},
                "last_waf_ts": 0.0,
                "last_heat_rest_ts": 0.0,
                "events": events,
            }), encoding="utf-8")

            rep = fr.build_report(
                ledger_path=ledger_path,
                gflow_dir=gflow_dir,
                credits_cache_path=tdir / "missing_cache.json",
                now=now,
                gflow_bin_override="",
            )
            self.assertEqual(rep["version"], 2)
            self.assertEqual(rep["health"], "ok")
            self.assertFalse(rep["rolling"]["hour_enabled"])
            self.assertEqual(rep["rolling"]["hour_limit"], 0)
            self.assertEqual(rep["rolling"]["waf_window_tokens"], 3)
            self.assertEqual(rep["rolling"]["waf_effective_limit"], 8)
            self.assertEqual(rep["rolling"]["day_used"], 7)
            self.assertEqual(rep["rolling"]["day_character_used"], 1)
            self.assertEqual(rep["rolling"]["min_gap_wait_sec"], 0)
            pools = {p["id"]: p for p in rep["image_quota"]["pools"]}
            self.assertEqual(pools["nano2_shared"]["used"], 4)
            self.assertEqual(pools["image_upscale_4k"]["used"], 1)
            self.assertEqual(pools["nano_pro"]["used"], 0)
            self.assertEqual(rep["image_quota"]["daily_image_submits"], 1)
            self.assertFalse(rep["image_quota"]["heat_rest_due"])
            img_models = {m["model"]: m for m in rep["usage"]["by_image_model_30d"]}
            self.assertEqual(img_models["nano2"]["ok"], 1)
            self.assertEqual(img_models["nano2"]["label"], "Nano Banana 2.1")

    def test_peak_hours_waf_recovery_clamp_and_explicit_hour_cap(self) -> None:
        # 2027-01-15T15:00:00Z -> UTC 15:00 (peak window UTC 14:00-17:00)
        peak_now = 1_800_025_200.0
        non_peak_now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            ledger_path = tdir / "ledger.json"
            cache_path = tdir / "cache.json"

            # 1. Peak hours clamps 8 -> 6 and 6 tokens triggers critical (10m 满窗)
            six_events = [
                {"ts": peak_now - 60 * (i + 1), "action": "image_t2i", "model": "nano2", "quota_units": 4, "credits": 0.0, "outcome": "OK", "exit_code": 0, "recaptcha_tokens": 1}
                for i in range(6)
            ]
            ledger_path.write_text(json.dumps({"version": 2, "breaker": {}, "events": six_events}), encoding="utf-8")
            rep_peak = fr.build_report(ledger_path, tdir / "nogflow", cache_path, now=peak_now, max_recaptcha_10m=8)
            self.assertTrue(rep_peak["rolling"]["is_peak_hours"])
            self.assertEqual(rep_peak["rolling"]["waf_effective_limit"], 6)
            self.assertEqual(rep_peak["health"], "critical")
            self.assertEqual(rep_peak["status_label"], "10m 满窗")

            # 2. Non-peak + recent WAF via last_waf_ts=0 fallback (exit_code==10 within 24h) clamps 8 -> 6
            ledger_path.write_text(json.dumps({
                "version": 2,
                "breaker": {},
                "last_waf_ts": 0.0,
                "events": [
                    {"ts": non_peak_now - 3600, "action": "i2v", "credits": 0.0, "outcome": "WAF_403", "exit_code": 10, "recaptcha_tokens": 1},
                ],
            }), encoding="utf-8")
            rep_rec = fr.build_report(ledger_path, tdir / "nogflow", cache_path, now=non_peak_now, max_recaptcha_10m=8)
            self.assertFalse(rep_rec["rolling"]["is_peak_hours"])
            self.assertTrue(rep_rec["rolling"]["recent_waf_recovery"])
            self.assertEqual(rep_rec["rolling"]["waf_effective_limit"], 6)

            # 3. Explicit max_per_hour=5 enforces hourly critical
            five_video = [
                {"ts": non_peak_now - 1000 - i * 100, "action": "i2v", "credits": 10.0, "outcome": "OK", "exit_code": 0, "recaptcha_tokens": 1}
                for i in range(5)
            ]
            ledger_path.write_text(json.dumps({"version": 2, "breaker": {}, "events": five_video}), encoding="utf-8")
            rep_hr = fr.build_report(ledger_path, tdir / "nogflow", cache_path, now=non_peak_now, max_per_hour=5)
            self.assertTrue(rep_hr["rolling"]["hour_enabled"])
            self.assertEqual(rep_hr["health"], "critical")
            self.assertEqual(rep_hr["status_label"], "已达限流")

    def test_model_breakers_heat_rest_and_extended_event_tags(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            ledger_path = tdir / "ledger.json"
            cache_path = tdir / "cache.json"
            # 72 OK + 8 tag-case submitted image events = exact 80 submits across 24h (>10m ago) -> heat_rest_due True
            events = [
                {"ts": now - 900 - i * 600, "action": "image_t2i", "model": "nano2", "quota_pool": "nano2_shared", "quota_units": 4, "credits": 0.0, "outcome": "OK", "exit_code": 0, "recaptcha_tokens": 1}
                for i in range(72)
            ]
            # Extended outcome tags (use recaptcha_tokens=1 on older events >10m ago so 10m stays clear)
            tag_cases = [
                ("WAF_403", 10, "WAF"),
                ("AUTH_LOCK", 10, "WAF"),
                ("RATE_LIMIT", 11, "429"),
                ("MODEL_QUOTA_EXCEEDED", 12, "POOL"),
                ("CONTENT_POLICY", 13, "POL"),
                ("SAME_IMAGE_SUSPECT", 0, "DUP"),
                ("TRANSPORT_TIMEOUT", 14, "TMO"),
                ("HARD_BLOCK", 15, "BLK"),
            ]
            for idx, (outcome, code, _tag) in enumerate(tag_cases):
                events.append({
                    "ts": now - 700 - idx,
                    "action": "image_t2i",
                    "model": "nano-pro",
                    "quota_pool": "nano_pro",
                    "quota_units": 4,
                    "credits": 0.0,
                    "outcome": outcome,
                    "exit_code": code,
                    "recaptcha_tokens": 1,
                })
            ledger_path.write_text(json.dumps({
                "version": 2,
                "breaker": {"active": False},
                "model_breakers": {
                    "nano_pro": {"active": True, "until_ts": now + 1800, "reason": "daily pool exhausted"},
                    "nano2_shared": {"active": True, "until_ts": now - 10, "reason": "expired"},
                },
                "last_heat_rest_ts": now - 60000,
                "events": events,
            }), encoding="utf-8")

            rep = fr.build_report(ledger_path, tdir / "nogflow", cache_path, now=now)
            self.assertEqual(rep["health"], "warning")
            self.assertEqual(rep["status_label"], "单池隔离")
            self.assertEqual(len(rep["model_breakers"]), 1)
            self.assertIn("nano_pro", rep["model_breakers"])
            self.assertEqual(rep["model_breakers"]["nano_pro"]["pool"], "nano_pro")
            self.assertTrue(rep["image_quota"]["heat_rest_due"])
            self.assertGreaterEqual(rep["image_quota"]["daily_image_submits"], 80)

            tags_by_outcome = {ev["outcome"]: ev["tag"] for ev in rep["recent_events"]}
            for outcome, _code, expected_tag in tag_cases:
                self.assertEqual(tags_by_outcome.get(outcome), expected_tag)

    def test_single_flight_lock_and_workspace_project_rotation_scan(self) -> None:
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            # Single-flight lock: stale PID -> False, live parent PID -> True
            lock_path = ws / "gflow_single_flight.lock"
            lock_path.write_text(json.dumps({"pid": 99999999, "cmd": "image"}), encoding="utf-8")
            sf0 = fr.inspect_single_flight_lock(lock_path, now)
            self.assertFalse(sf0["active"])
            ppid = os.getppid()
            if ppid > 0 and ppid != os.getpid() and fr.is_pid_alive(ppid):
                lock_path.write_text(json.dumps({"pid": ppid, "cmd": "image_t2i"}), encoding="utf-8")
                sf1 = fr.inspect_single_flight_lock(lock_path, now)
                self.assertTrue(sf1["active"])
                self.assertIn(str(ppid), sf1["detail"])

            # Video run + flow_project_index.json (50-seg rotation)
            vid_dir = ws / "work" / "ep02"
            vid_dir.mkdir(parents=True)
            (vid_dir / "flow_run_state.json").write_text(json.dumps({
                "segments": {"s01": {"status": "DONE", "credits_used": 10}},
                "updated_at": "2026-11-27T10:00:00+08:00",
            }), encoding="utf-8")
            (vid_dir / "flow_project_index.json").write_text(json.dumps({
                "active_project_id": "proj-v1",
                "projects": {"proj-v1": {"submitted_count": 32}},
            }), encoding="utf-8")

            # Image run + flow_image_state.json (100-tile rotation)
            img_dir = ws / "work" / "ep02_img"
            img_dir.mkdir(parents=True)
            (img_dir / "flow_image_state.json").write_text(json.dumps({
                "jobs": {
                    "img_01": {"status": "DONE", "project_id": "proj-i1", "count": 4},
                    "img_02": {"status": "DONE", "upscaled_scale": "4k", "project_id": "proj-i1", "count": 4},
                    "img_03": {"status": "FAILED", "project_id": "proj-i1", "count": 4},
                },
                "flow_project_id": "proj-i1",
                "flow_project_tiles": 76,
                "flow_project_history": [{"id": "proj-i0", "tiles": 100}],
                "updated_at": "2026-11-27T11:00:00+08:00",
            }), encoding="utf-8")

            runs = fr.scan_workspace_flow_runs(str(ws))
            by_kind = {r["kind"]: r for r in runs}
            self.assertIn("video", by_kind)
            self.assertIn("image", by_kind)
            self.assertEqual(by_kind["video"]["project_rotation"]["used"], 32)
            self.assertEqual(by_kind["video"]["project_rotation"]["limit"], 50)
            self.assertEqual(by_kind["image"]["completed"], 2)
            self.assertEqual(by_kind["image"]["project_rotation"]["used"], 76)
            self.assertEqual(by_kind["image"]["project_rotation"]["limit"], 100)
            self.assertEqual(by_kind["image"]["project_rotation"]["project_count"], 2)


if __name__ == "__main__":
    unittest.main()
