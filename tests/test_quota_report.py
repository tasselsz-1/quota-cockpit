#!/usr/bin/env python3
"""Unit tests for quota_report.py and context_report.py in quota-cockpit."""

import datetime
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

SIDECAR_DIR = Path(__file__).resolve().parent.parent / "sidecars" / "cockpit"
if str(SIDECAR_DIR) not in sys.path:
    sys.path.insert(0, str(SIDECAR_DIR))

import context_report  # noqa: E402
import quota_report  # noqa: E402


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


def make_metadata(
    ts: int,
    tool_name: str = "generate_image",
    args: dict | None = None,
    end_ts: int | None = None,
) -> bytes:
    ts_msg = pb_varint(1, int(ts))
    args_str = json.dumps(args or {}, ensure_ascii=False).encode("utf-8")
    tc_msg = pb_bytes(2, tool_name.encode("utf-8")) + pb_bytes(3, args_str)
    raw = pb_bytes(1, ts_msg) + pb_bytes(4, tc_msg)
    if end_ts is not None:
        raw += pb_bytes(8, pb_varint(1, int(end_ts)))
    return raw


def make_error_details(err_text: str) -> bytes:
    return pb_bytes(2, err_text.encode("utf-8"))


def create_conv_db(db_path: Path, rows: list[tuple[int, int, int, bytes, bytes | None]], mtime: float) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE steps ("
            "idx INTEGER, step_type INTEGER, status INTEGER, metadata BLOB, error_details BLOB)"
        )
        conn.executemany(
            "INSERT INTO steps (idx, step_type, status, metadata, error_details) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()
    os.utime(db_path, (mtime, mtime))


def iso_utc(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class QuotaReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conv_dir = self.root / "conversations"
        self.conv_dir.mkdir()
        self.now = 1_760_000_000.0

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_active_official_429_lock(self) -> None:
        reset_ts = self.now + 7200  # 2h remaining
        w_start = reset_ts - quota_report.WINDOW_5H_SEC
        err_429 = json.dumps({
            "error": {
                "code": 429,
                "message": "You have exhausted your capacity on this model.",
                "details": [{
                    "metadata": {
                        "model": "gemini-3.1-flash-image",
                        "quotaResetTimeStamp": iso_utc(reset_ts),
                    },
                    "retryDelay": "7200s",
                }],
            }
        })
        rows = [
            # Before window -> excluded from window_5h, kept in 24h
            (1, 91, 3, make_metadata(int(w_start - 3600), args={"ImageName": "old_shot"}), None),
            # Inside active 5h lock window
            (2, 91, 3, make_metadata(int(w_start + 600), args={"ImageName": "shot_01"}), None),
            (3, 91, 4, make_metadata(int(w_start + 1200), args={"ImageName": "shot_02"}), make_error_details("unexpected EOF")),
            (4, 91, 4, make_metadata(int(self.now - 120), args={"ImageName": "shot_03"}), make_error_details(err_429)),
            # Non-image tool must be ignored
            (5, 132, 3, make_metadata(int(self.now - 60), tool_name="read_file"), None),
        ]
        create_conv_db(self.conv_dir / "conv-lock.db", rows, self.now - 30)

        rep = quota_report.build_report(self.conv_dir, now=self.now)
        w5 = rep["window_5h"]
        self.assertEqual(rep["model"], "gemini-3.1-flash-image")
        self.assertTrue(w5["cooldown_active"])
        self.assertEqual(w5["mode"], "official_429_lock")
        self.assertEqual(w5["health"], "locked")
        self.assertEqual(w5["ok"], 1)
        self.assertEqual(w5["eof"], 1)
        self.assertEqual(w5["rate_limited"], 1)
        self.assertEqual(w5["attempts"], 2)
        self.assertEqual(w5["remaining_est"], 0)
        self.assertEqual(w5["used_pct"], 100.0)
        self.assertAlmostEqual(w5["reset_remaining_sec"], 7200.0, delta=2.0)
        self.assertEqual(rep["window_24h"]["ok"], 2)

    def test_post_reset_idle_and_active_rolling(self) -> None:
        # Calls older than 5h have fully rolled out -> idle_full
        old_ts = self.now - quota_report.WINDOW_5H_SEC - 900
        past_reset = self.now - 1800
        err_429 = f'{{"quotaResetTimeStamp":"{iso_utc(past_reset)}","model":"gemini-3.1-flash-image","message":"RESOURCE_EXHAUSTED"}}'
        rows_idle = [
            (1, 91, 3, make_metadata(int(old_ts), args={"ImageName": "expired_ok"}), None),
            (2, 91, 4, make_metadata(int(past_reset - 300), args={"ImageName": "pre_429"}), make_error_details(err_429)),
        ]
        create_conv_db(self.conv_dir / "conv-past.db", rows_idle, self.now - 1800)

        rep_idle = quota_report.build_report(self.conv_dir, now=self.now)
        self.assertFalse(rep_idle["window_5h"]["cooldown_active"])
        self.assertEqual(rep_idle["window_5h"]["mode"], "idle_full")
        self.assertEqual(rep_idle["window_5h"]["attempts"], 0)
        self.assertEqual(rep_idle["window_5h"]["health"], "ok")

        # Even if a 429 reset passed 30m ago, a call made 2h ago is STILL inside the true sliding 5h window
        still_in_5h_ts = int(self.now - 7200)
        rows_mid = [
            (1, 91, 3, make_metadata(still_in_5h_ts, args={"ImageName": "still_active_pre_reset"}), None),
        ]
        create_conv_db(self.conv_dir / "conv-mid.db", rows_mid, self.now - 7000)
        rep_mid = quota_report.build_report(self.conv_dir, now=self.now)
        self.assertEqual(rep_mid["window_5h"]["mode"], "active_rolling")
        self.assertEqual(rep_mid["window_5h"]["attempts"], 1)
        self.assertAlmostEqual(rep_mid["window_5h"]["next_recover_ts"], still_in_5h_ts + quota_report.WINDOW_5H_SEC, delta=1.0)

        # Add fresh post-reset calls -> active_rolling accumulates continuously
        rows_fresh = [
            (1, 91, 3, make_metadata(int(self.now - 600), args={"ImageName": "post_1"}), None),
            (2, 91, 4, make_metadata(int(self.now - 300), args={"ImageName": "post_2"}), make_error_details("connection reset by peer")),
        ]
        create_conv_db(self.conv_dir / "conv-fresh.db", rows_fresh, self.now - 10)

        rep_roll = quota_report.build_report(self.conv_dir, soft_cap=3, hard_cap=5, count_mode="attempts", now=self.now)
        w5 = rep_roll["window_5h"]
        self.assertEqual(w5["mode"], "active_rolling")
        self.assertEqual(w5["ok"], 2)
        self.assertEqual(w5["eof"], 1)
        self.assertEqual(w5["attempts"], 3)
        self.assertEqual(w5["metric_used"], 3)
        self.assertEqual(w5["health"], "warning")

        rep_ok_only = quota_report.build_report(self.conv_dir, soft_cap=3, hard_cap=5, count_mode="ok_only", now=self.now)
        self.assertEqual(rep_ok_only["window_5h"]["metric_used"], 2)
        self.assertEqual(rep_ok_only["window_5h"]["health"], "ok")

    def test_multi_lock_calibration_and_cache_reuse(self) -> None:
        r1 = self.now - 2 * 86400
        r2 = self.now - 1 * 86400
        rows = [
            # Lock 1: 3 OK + 1 EOF = 4 attempts
            (1, 91, 3, make_metadata(int(r1 - 10000)), None),
            (2, 91, 3, make_metadata(int(r1 - 9000)), None),
            (3, 91, 3, make_metadata(int(r1 - 8000)), None),
            (4, 91, 4, make_metadata(int(r1 - 7000)), make_error_details("EOF")),
            (5, 91, 4, make_metadata(int(r1 - 100)), make_error_details(f'{{"quotaResetTimeStamp":"{iso_utc(r1)}","model":"gemini-3.1-flash-image","message":"429"}}')),
            # Lock 2: 5 OK + 1 ERR = 6 attempts
            (6, 91, 3, make_metadata(int(r2 - 9000)), None),
            (7, 91, 3, make_metadata(int(r2 - 8000)), None),
            (8, 91, 3, make_metadata(int(r2 - 7000)), None),
            (9, 91, 3, make_metadata(int(r2 - 6000)), None),
            (10, 91, 3, make_metadata(int(r2 - 5000)), None),
            (11, 91, 4, make_metadata(int(r2 - 4000)), make_error_details("invalid argument")),
            (12, 91, 4, make_metadata(int(r2 - 100)), make_error_details(f'{{"quotaResetTimeStamp":"{iso_utc(r2)}","model":"gemini-3.1-flash-image","message":"429"}}')),
        ]
        create_conv_db(self.conv_dir / "conv-cal.db", rows, self.now - 100)
        cache_file = self.root / "scan-cache.json"

        rep1 = quota_report.build_report(self.conv_dir, cache_path=cache_file, now=self.now)
        cal = rep1["calibration"]
        self.assertEqual(cal["observed_lock_count"], 2)
        self.assertEqual(cal["avg_ok_at_lock"], 4.0)
        self.assertEqual(cal["avg_attempts_at_lock"], 5.0)
        self.assertEqual(rep1["scan_meta"]["scanned_dbs"], 1)
        self.assertEqual(rep1["scan_meta"]["cache_hits"], 0)

        # Second call hits cache
        rep2 = quota_report.build_report(self.conv_dir, cache_path=cache_file, now=self.now)
        self.assertEqual(rep2["scan_meta"]["scanned_dbs"], 0)
        self.assertEqual(rep2["scan_meta"]["cache_hits"], 1)

    def test_event_kinds_and_health_critical(self) -> None:
        rows = [
            (1, 91, 3, make_metadata(int(self.now - 500), args={"Prompt": "cinematic neon alley"}), None),
            (2, 91, 1, make_metadata(int(self.now - 200), args={"ImageName": "in_progress"}), None),
            (3, 91, 4, make_metadata(int(self.now - 100), args={"ImageName": "bad_req"}), make_error_details("safety filter blocked")),
        ]
        create_conv_db(self.conv_dir / "conv-kinds.db", rows, self.now - 5)

        rep = quota_report.build_report(self.conv_dir, soft_cap=2, hard_cap=3, count_mode="attempts", now=self.now)
        w5 = rep["window_5h"]
        self.assertEqual(w5["ok"], 1)
        self.assertEqual(w5["running"], 1)
        self.assertEqual(w5["err"], 1)
        self.assertEqual(w5["attempts"], 3)
        self.assertEqual(w5["health"], "critical")
        self.assertEqual(w5["remaining_est"], 0)
        self.assertEqual(rep["recent_events"][0]["kind"], "ERR")

    def test_workspace_ledger_discovery(self) -> None:
        ws = self.root / "workspace"
        ep_dir = ws / "output" / "ep01"
        ep_dir.mkdir(parents=True)
        ledger = {
            "schema_version": "4.2",
            "global_cooldown_until": self.now + 900,
            "windows": {
                "W1": {
                    "hard_cap": 60,
                    "used_calls": 42,
                    "pending_calls": 2,
                    "cooldown_until": self.now + 300,
                    "reservations": [
                        {"state": "OK"},
                        {"state": "OK"},
                        {"state": "PENDING"},
                        {"state": "429"},
                    ],
                }
            },
        }
        (ep_dir / "api_call_ledger.json").write_text(json.dumps(ledger), encoding="utf-8")

        rep = quota_report.build_report(self.conv_dir, workspace=str(ws), now=self.now)
        self.assertEqual(len(rep["workspace_ledgers"]), 1)
        item = rep["workspace_ledgers"][0]
        self.assertEqual(item["episode_label"], "ep01")
        self.assertEqual(item["path"], "output/ep01/api_call_ledger.json")
        self.assertAlmostEqual(item["global_cooldown_remaining_sec"], 900.0, delta=1.0)
        self.assertEqual(len(item["windows"]), 1)
        win = item["windows"][0]
        self.assertEqual(win["used_calls"], 42)
        self.assertEqual(win["pending_calls"], 2)
        self.assertEqual(win["remaining_calls"], 16)
        self.assertAlmostEqual(win["cooldown_remaining_sec"], 900.0, delta=1.0)
        self.assertEqual(win["states"]["OK"], 2)
        self.assertEqual(win["states"]["PENDING"], 1)
        self.assertEqual(win["states"]["429"], 1)

    def test_token_pricing_and_per_image_avg(self) -> None:
        # 10 CJK chars (11) + 40 ASCII chars (10) = 21 prompt tokens
        prompt_mix = "电影级暗夜霓虹古风街" + ("a" * 40)
        self.assertEqual(quota_report.estimate_text_tokens(prompt_mix), 21)

        rows = [
            # 0 refs
            (1, 91, 3, make_metadata(int(self.now - 900), args={"ImageName": "r0", "Prompt": prompt_mix}, end_ts=int(self.now - 885)), None),
            # 1 ref via ImagePath
            (2, 91, 3, make_metadata(int(self.now - 800), args={"ImageName": "r1", "Prompt": prompt_mix, "ImagePath": "/tmp/a.png"}), None),
            # 3 refs via ImagePaths
            (3, 91, 3, make_metadata(int(self.now - 700), args={"ImageName": "r3", "Prompt": prompt_mix, "ImagePaths": ["/a.png", "/b.png", "/c.png"]}), None),
            # Non-billable CLIENT_ERR must carry 0 billable tokens
            (4, 91, 4, make_metadata(int(self.now - 600), args={"ImageName": "bad", "Prompt": prompt_mix, "ImagePaths": ["/a.png"]}), make_error_details("invalid arguments: missing required field")),
        ]
        create_conv_db(self.conv_dir / "conv-tok.db", rows, self.now - 10)

        rep = quota_report.build_report(self.conv_dir, now=self.now)
        w5 = rep["window_5h"]
        self.assertEqual(w5["attempts"], 3)
        self.assertEqual(w5["client_err"], 1)
        self.assertEqual(w5["ref_images"], 4)
        self.assertEqual(w5["prompt_tokens"], 63)
        self.assertEqual(w5["input_image_tokens"], 4 * 1120)
        self.assertEqual(w5["output_image_tokens"], 3 * 1120)
        self.assertEqual(w5["thinking_tokens"], 3 * 250)
        self.assertEqual(w5["tokens_raw"], 63 + 4 * 1120 + 3 * 1120)
        self.assertEqual(w5["tokens_total"], w5["tokens_raw"] + 750)
        self.assertGreater(w5["est_usd"], 0.20)

        # Verify duration_sec extracted from md.msg(8)
        ev_r0 = [e for e in rep["recent_events"] if e["image_name"] == "r0"][0]
        self.assertEqual(ev_r0["duration_sec"], 15.0)
        ev_bad = [e for e in rep["recent_events"] if e["image_name"] == "bad"][0]
        self.assertEqual(ev_bad["kind"], "CLIENT_ERR")
        self.assertEqual(ev_bad["tokens_total"], 0)

        avg = rep["per_image_avg"]
        self.assertEqual(avg["sample_count"], 3)
        self.assertGreater(avg["output_cost_share_pct"], 95.0)
        labels = [g["label"] for g in avg["by_refs"]]
        self.assertEqual(labels, ["0", "1", "3+"])
        # Verify dedicated with_refs (n_refs >= 1) aggregate excludes r0 and averages r1 (1 ref) + r3 (3 refs)
        wr = avg["with_refs"]
        self.assertFalse(wr["is_fallback"])
        self.assertEqual(wr["sample_count"], 2)
        self.assertEqual(wr["ok_count"], 2)
        self.assertAlmostEqual(wr["share_pct"], 66.7, delta=0.1)
        self.assertEqual(wr["avg_refs"], 2.0)
        self.assertEqual(wr["avg_tokens_raw"], 21 + 2 * 1120 + 1120)
        self.assertEqual(wr["avg_tokens_total"], 21 + 2 * 1120 + 1120 + 250)

    def test_rpm_client_err_and_post_429_ok_lift(self) -> None:
        # 1) RPM burst must NOT lock the 5h window — even when Google reuses "exhausted your capacity" with 1.5s retryDelay
        rpm_ts = int(self.now - 300)
        err_rpm = json.dumps({
            "error": {
                "code": 429,
                "message": "You have exhausted your capacity on this model. Your quota will reset after 1s.",
                "details": [{
                    "retryDelay": "1.5s",
                    "metadata": {
                        "model": "gemini-3.1-flash-image",
                        "quotaResetTimeStamp": iso_utc(rpm_ts + 2),
                    },
                }],
            }
        })
        # 2) Real 5h 429 with future reset_ts, followed by a newer OK -> cooldown automatically lifted!
        lock_ts = int(self.now - 200)
        future_reset = self.now + 7200
        err_5h = json.dumps({
            "error": {
                "code": 429,
                "message": "QUOTA_EXHAUSTED: You have exhausted your capacity on this model.",
                "details": [{"metadata": {"model": "gemini-3.1-flash-image", "quotaResetTimeStamp": iso_utc(future_reset)}}],
            }
        })
        rows = [
            (1, 91, 4, make_metadata(rpm_ts, end_ts=rpm_ts + 1), make_error_details(err_rpm)),
            (2, 91, 4, make_metadata(lock_ts, end_ts=lock_ts + 2), make_error_details(err_5h)),
            (3, 91, 3, make_metadata(int(self.now - 60), args={"ImageName": "recovered_ok"}), None),
        ]
        create_conv_db(self.conv_dir / "conv-rpm.db", rows, self.now - 10)

        rep = quota_report.build_report(self.conv_dir, now=self.now)
        w5 = rep["window_5h"]
        self.assertEqual(w5["rpm_limited"], 1)
        self.assertEqual(w5["rate_limited"], 1)
        self.assertFalse(w5["cooldown_active"])
        self.assertEqual(w5["mode"], "active_rolling")
        self.assertEqual(rep["calibration"]["rpm_burst_count"], 1)

    def test_rolling_schedule_30m_buckets_and_milestones(self) -> None:
        # Align now to a clean half-hour boundary so bucket bounds are deterministic
        now = 1_760_000_400.0  # divisible by 1800
        # 2 calls expiring in +10m / +12m (same 30m bucket & same 5m first_batch)
        # 1 call expiring in +45m (second 30m bucket, inside 1h milestone)
        # 1 call expiring in +95m (third bucket, inside 2h milestone)
        t1 = int(now - quota_report.WINDOW_5H_SEC + 600)
        t2 = int(now - quota_report.WINDOW_5H_SEC + 720)
        t3 = int(now - quota_report.WINDOW_5H_SEC + 2700)
        t4 = int(now - quota_report.WINDOW_5H_SEC + 5700)
        rows = [
            (1, 91, 3, make_metadata(t1, args={"ImageName": "b1"}), None),
            (2, 91, 3, make_metadata(t2, args={"ImageName": "b2"}), None),
            (3, 91, 4, make_metadata(t3, args={"ImageName": "b3"}), make_error_details("EOF")),
            (4, 91, 3, make_metadata(t4, args={"ImageName": "b4"}), None),
        ]
        create_conv_db(self.conv_dir / "conv-roll.db", rows, now - 10)

        rep = quota_report.build_report(self.conv_dir, now=now)
        rs = rep["rolling_schedule"]
        self.assertEqual(rs["total_calls"], 4)
        self.assertEqual(len(rs["buckets"]), 3)
        self.assertAlmostEqual(rs["next_recover_sec"], 600.0, delta=1.0)
        self.assertAlmostEqual(rs["full_clear_sec"], 5700.0, delta=1.0)
        self.assertEqual(rs["first_batch"]["calls"], 2)
        self.assertEqual(rs["recover_next_30m"]["calls"], 2)
        self.assertEqual(rs["recover_next_1h"]["calls"], 3)
        self.assertEqual(rs["recover_next_2h"]["calls"], 4)
        self.assertEqual(rs["buckets"][-1]["cum_calls"], 4)
        self.assertEqual(rs["buckets"][-1]["cum_pct"], 100.0)

    def test_self_learning_safe_watermark_vs_calibrated_and_manual(self) -> None:
        # 15 days ago: 12 clean OK calls in one 5h burst (proven safe peak = 12, outside 7d but inside 30d)
        t_safe = int(self.now - 15 * 86400)
        rows_safe = [
            (i + 1, 91, 3, make_metadata(t_safe + i * 60, args={"ImageName": f"s_{i}"}), None)
            for i in range(12)
        ]
        create_conv_db(self.conv_dir / "conv-safe15d.db", rows_safe, t_safe + 1000)

        # 2 days ago: noisy lock (15 OK + 5 EOF = 25% EOF -> noisy, must NOT become hard calibrated_429)
        t_noisy = int(self.now - 2 * 86400)
        rows_noisy = []
        for i in range(15):
            rows_noisy.append((i + 1, 91, 3, make_metadata(t_noisy - 3600 + i * 30), None))
        for i in range(5):
            rows_noisy.append((20 + i, 91, 4, make_metadata(t_noisy - 1000 + i * 30), make_error_details("unexpected EOF")))
        rows_noisy.append((
            30, 91, 4, make_metadata(t_noisy - 10),
            make_error_details(f'{{"quotaResetTimeStamp":"{iso_utc(t_noisy + 14400)}","message":"QUOTA_EXHAUSTED"}}'),
        ))
        create_conv_db(self.conv_dir / "conv-noisy.db", rows_noisy, t_noisy)

        # Active 5h window: 13 OK calls (exceeds the 12-call 30d safe peak!)
        # Note: current calls themselves also become the new 30d safe peak (13), so test with 12 calls + 3 EOFs
        # Wait: let's test safe_watermark with 10 active OK calls + 3 EOFs (23.1% EOF -> eof_warning!)
        rows_now = []
        for i in range(10):
            rows_now.append((i + 1, 91, 3, make_metadata(int(self.now - 1800 + i * 60), args={"ImagePaths": ["/r.png"]}), None))
        for i in range(3):
            rows_now.append((20 + i, 91, 4, make_metadata(int(self.now - 600 + i * 30)), make_error_details("EOF")))
        create_conv_db(self.conv_dir / "conv-now.db", rows_now, self.now - 5)

        rep_auto = quota_report.build_report(self.conv_dir, anchor_mode="auto", now=self.now)
        anc = rep_auto["anchor"]
        self.assertEqual(anc["source"], "safe_watermark")
        self.assertFalse(anc["is_hard_ceiling"])
        self.assertTrue(anc["exceeded_safe_peak"])  # 13 active attempts > 12 prior 30d safe peak
        self.assertEqual(anc["prior_safe_calls"], 12)
        self.assertEqual(anc["ref_calls"], 13)  # Immediately adopts latest proven-safe peak
        self.assertEqual(anc["ref_tokens_source"], "calls_x_with_refs")
        self.assertGreaterEqual(anc["ref_tokens"], anc["safe_lower_tokens"])
        self.assertTrue(anc["eof_warning"])
        self.assertEqual(anc["health"], "warning")  # Never false-alarm critical
        # Verify 7d excludes the 15d burst while 30d includes it
        self.assertEqual(rep_auto["window_7d"]["attempts"], 20 + 13)
        self.assertEqual(rep_auto["window_30d"]["attempts"], 12 + 20 + 13)

        # Manual mode with explicit token_cap -> triggers critical when tokens_total >= token_cap
        rep_manual = quota_report.build_report(
            self.conv_dir, anchor_mode="manual", soft_cap=50, hard_cap=60, token_cap=10000, now=self.now
        )
        self.assertEqual(rep_manual["anchor"]["source"], "manual")
        self.assertEqual(rep_manual["anchor"]["health"], "critical")
        self.assertEqual(rep_manual["anchor"]["est_remaining_imgs_with_refs"], 0)

        # Add a CLEAN 5h 429 lock (22 OK + 1 EOF = 4.3% EOF, >= 20 attempts) -> calibrated_429 takes over
        t_clean = int(self.now - 5 * 86400)
        rows_clean = [(i + 1, 91, 3, make_metadata(t_clean - 3000 + i * 30), None) for i in range(22)]
        rows_clean.append((23, 91, 4, make_metadata(t_clean - 500), make_error_details("EOF")))
        rows_clean.append((
            24, 91, 4, make_metadata(t_clean - 10),
            make_error_details(f'{{"quotaResetTimeStamp":"{iso_utc(t_clean + 15000)}","message":"QUOTA_EXHAUSTED"}}'),
        ))
        create_conv_db(self.conv_dir / "conv-clean.db", rows_clean, t_clean)

        rep_cal = quota_report.build_report(self.conv_dir, anchor_mode="auto", now=self.now)
        self.assertEqual(rep_cal["calibration"]["clean_lock_count"], 1)
        self.assertEqual(rep_cal["anchor"]["source"], "calibrated_429")
        self.assertTrue(rep_cal["anchor"]["is_hard_ceiling"])
        self.assertEqual(rep_cal["anchor"]["ref_calls"], 23)

    def test_with_refs_ceiling_and_remaining_estimation(self) -> None:
        # 2 days ago: 20 mixed-ref calls (10 x 0-ref + 10 x 1-ref) -> proven safe peak = 20 calls
        t_hist = int(self.now - 2 * 86400)
        rows_hist = []
        for i in range(10):
            rows_hist.append((i + 1, 91, 3, make_metadata(t_hist + i * 30, args={"Prompt": "a" * 400}), None))
        for i in range(10):
            rows_hist.append((
                20 + i, 91, 3,
                make_metadata(t_hist + 600 + i * 30, args={"Prompt": "a" * 400, "ImagePaths": ["/ref1.png"]}),
                None,
            ))
        create_conv_db(self.conv_dir / "conv-hist.db", rows_hist, t_hist + 1200)

        # Active 5h window: 10 calls with 2 refs each (heavier per-image tokens than the 0/1-ref history)
        rows_now = [
            (
                i + 1, 91, 3,
                make_metadata(int(self.now - 3600 + i * 60), args={"Prompt": "a" * 400, "ImagePaths": ["/r1.png", "/r2.png"]}),
                None,
            )
            for i in range(10)
        ]
        create_conv_db(self.conv_dir / "conv-cur.db", rows_now, self.now - 10)

        rep = quota_report.build_report(self.conv_dir, anchor_mode="auto", now=self.now)
        wr = rep["per_image_avg"]["with_refs"]
        anc = rep["anchor"]
        # 20 with-ref calls total: 10 x 1-ref (100+1120+1120+250=2590) + 10 x 2-ref (100+2240+1120+250=3710) -> avg 3150
        self.assertEqual(wr["sample_count"], 20)
        self.assertEqual(wr["avg_refs"], 1.5)
        self.assertEqual(wr["avg_tokens_total"], 3150)

        # Estimated ceiling = 20 safe calls * 3150 = 63,000 tok; active used = 10 * 3710 = 37,100 tok
        self.assertEqual(anc["ref_calls"], 20)
        self.assertEqual(anc["ref_tokens"], 63000)
        self.assertEqual(anc["ref_tokens_source"], "calls_x_with_refs")
        self.assertEqual(anc["remaining_tokens"], 63000 - 37100)
        # 25,900 / 3,150 = 8.22 -> 8 images (exact 8.2); by_refs: 1-ref -> 10, 2-ref -> 6
        self.assertEqual(anc["est_remaining_imgs_with_refs"], 8)
        self.assertEqual(anc["est_remaining_imgs_with_refs_exact"], 8.2)
        self.assertEqual(anc["est_remaining_imgs_by_refs"]["1"], 10)
        self.assertEqual(anc["est_remaining_imgs_by_refs"]["2"], 6)
        self.assertGreater(rep["rolling_schedule"]["buckets"][0]["est_imgs_with_refs"], 0.0)

        # Manual token_cap overrides ref_tokens cleanly
        rep_man = quota_report.build_report(
            self.conv_dir, anchor_mode="manual", soft_cap=15, hard_cap=20, token_cap=50000, now=self.now
        )
        # remaining_tokens = 50,000 - 37,100 = 12,900 -> 12,900 // 3,150 = 4 images
        self.assertEqual(rep_man["anchor"]["ref_tokens"], 50000)
        self.assertEqual(rep_man["anchor"]["est_remaining_imgs_with_refs"], 4)
        self.assertEqual(rep_man["anchor"]["used_pct_basis"], "tokens")
        self.assertEqual(rep["anchor"]["used_pct_basis"], "tokens")

    def test_stale_v3_and_corrupted_v4_cache_invalidation_and_tmp_cleanup(self) -> None:
        db_path = self.conv_dir / "conv-cache-heal.db"
        rows = [
            (
                1, 91, 3,
                make_metadata(
                    int(self.now - 600),
                    args={"ImageName": "s1", "Prompt": "a" * 400, "ImagePaths": ["/r1.png", "/r2.png"]},
                    end_ts=int(self.now - 588),
                ),
                None,
            ),
        ]
        create_conv_db(db_path, rows, self.now - 10)
        stamp = quota_report.db_combined_stamp(db_path)
        cache_file = self.root / "image-scan-cache.json"

        # 1) Legacy v3 cache missing tokens_total & n_refs must be rejected and rebuilt to v4
        legacy_v3 = {
            "version": 3,
            "files": {
                db_path.name: {
                    "stamp": stamp,
                    "events": [{
                        "ts": float(int(self.now - 600)),
                        "conv_id": db_path.stem,
                        "step_idx": 1,
                        "kind": "OK",
                        "raw_status": 3,
                        "image_name": "s1",
                        "prompt_preview": "aaa",
                    }],
                }
            },
        }
        cache_file.write_text(json.dumps(legacy_v3), encoding="utf-8")
        stale_tmp = self.root / "image-scan-cache.tmp.99999"
        stale_tmp.write_text("leftover", encoding="utf-8")
        old_mt = self.now - 300
        os.utime(stale_tmp, (old_mt, old_mt))

        rep1 = quota_report.build_report(self.conv_dir, cache_path=cache_file, now=self.now)
        self.assertEqual(rep1["scan_meta"]["scanned_dbs"], 1)
        self.assertEqual(rep1["scan_meta"]["cache_hits"], 0)
        self.assertEqual(rep1["window_5h"]["ref_images"], 2)
        self.assertEqual(rep1["window_5h"]["tokens_total"], 100 + 2 * 1120 + 1120 + 250)
        self.assertFalse(stale_tmp.exists())
        saved = json.loads(cache_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["version"], quota_report.CACHE_VERSION)

        # 2) Even if version matches v4, a corrupted entry with missing/zero tokens_total forces per-DB rescan
        corrupted_v4 = {
            "version": quota_report.CACHE_VERSION,
            "files": {
                db_path.name: {
                    "stamp": stamp,
                    "events": [{
                        "ts": float(int(self.now - 600)),
                        "end_ts": float(int(self.now - 588)),
                        "conv_id": db_path.stem,
                        "step_idx": 1,
                        "kind": "OK",
                        "n_refs": 0,
                        "prompt_tokens": 0,
                        "input_image_tokens": 0,
                        "output_image_tokens": 0,
                        "thinking_tokens": 0,
                        "tokens_raw": 0,
                        "tokens_total": 0,
                        "est_usd": 0.0,
                    }],
                }
            },
        }
        cache_file.write_text(json.dumps(corrupted_v4), encoding="utf-8")
        rep2 = quota_report.build_report(self.conv_dir, cache_path=cache_file, now=self.now)
        self.assertEqual(rep2["scan_meta"]["scanned_dbs"], 1)
        self.assertEqual(rep2["window_5h"]["ref_images"], 2)
        self.assertEqual(rep2["window_5h"]["tokens_total"], 100 + 2 * 1120 + 1120 + 250)

        # 3) Clean v4 cache hits cleanly on subsequent run
        rep3 = quota_report.build_report(self.conv_dir, cache_path=cache_file, now=self.now)
        self.assertEqual(rep3["scan_meta"]["scanned_dbs"], 0)
        self.assertEqual(rep3["scan_meta"]["cache_hits"], 1)

    def test_calls_vs_tokens_pct_basis_when_zero_ref_dominates(self) -> None:
        # History: 12 heavy 2-ref calls (peak = 12 calls)
        t_hist = int(self.now - 2 * 86400)
        rows_hist = [
            (
                i + 1, 91, 3,
                make_metadata(t_hist + i * 30, args={"Prompt": "a" * 400, "ImagePaths": ["/r1.png", "/r2.png"]}),
                None,
            )
            for i in range(12)
        ]
        create_conv_db(self.conv_dir / "conv-heavy.db", rows_hist, t_hist + 600)
        # Active 5h: 11 zero-ref calls -> calls_pct = 91.7%, tokens_pct = ~46% -> used_pct_basis == "calls"
        rows_now = [
            (
                i + 1, 91, 3,
                make_metadata(int(self.now - 1200 + i * 30), args={"Prompt": "a" * 400}),
                None,
            )
            for i in range(11)
        ]
        create_conv_db(self.conv_dir / "conv-light.db", rows_now, self.now - 5)

        rep = quota_report.build_report(self.conv_dir, anchor_mode="auto", now=self.now)
        anc = rep["anchor"]
        self.assertEqual(anc["used_pct_basis"], "calls")
        self.assertGreater(anc["calls_pct"], 90.0)
        self.assertLess(anc["tokens_pct"], 50.0)
        self.assertEqual(anc["used_pct"], anc["calls_pct"])
        self.assertEqual(rep["window_5h"]["used_pct_basis"], "calls")

    def test_weekly_rolling_schedule_and_weekly_429_isolation(self) -> None:
        # Spread calls across 6.5 days ago (rolls off in 12h), 5.5 days ago (rolls off in 36h), and 1h ago
        t_d1 = int(self.now - 6.5 * 86400)
        t_d2 = int(self.now - 5.5 * 86400)
        t_now = int(self.now - 3600)
        weekly_reset_ts = self.now + 36 * 3600  # 36h lockout -> must classify as 429_WEEKLY (>6h)
        err_weekly = json.dumps({
            "error": {
                "code": 429,
                "message": "QUOTA_EXHAUSTED: Weekly image capacity reached.",
                "details": [{
                    "metadata": {
                        "model": "gemini-3.1-flash-image",
                        "quotaResetTimeStamp": iso_utc(weekly_reset_ts),
                    },
                    "retryDelay": "129600s",
                }],
            }
        })
        rows = [
            (1, 91, 3, make_metadata(t_d1, args={"ImageName": "w_old_1", "ImagePaths": ["/r.png"]}), None),
            (2, 91, 3, make_metadata(t_d1 + 300, args={"ImageName": "w_old_2", "ImagePaths": ["/r.png"]}), None),
            (3, 91, 3, make_metadata(t_d2, args={"ImageName": "w_mid_1", "ImagePaths": ["/r.png"]}), None),
            (4, 91, 3, make_metadata(t_now, args={"ImageName": "w_recent_1", "ImagePaths": ["/r.png"]}), None),
            (5, 91, 4, make_metadata(int(self.now - 120), end_ts=int(self.now - 118), args={"ImageName": "w_hit"}), make_error_details(err_weekly)),
        ]
        create_conv_db(self.conv_dir / "conv-weekly.db", rows, self.now - 10)

        rep = quota_report.build_report(self.conv_dir, now=self.now)
        w5 = rep["window_5h"]
        wrs = rep["weekly_rolling_schedule"]
        cal = rep["calibration"]

        # Weekly 429 must NOT pollute 5h calibration or 5h lock state
        self.assertFalse(w5["cooldown_active"])
        self.assertTrue(w5["weekly_cooldown_active"])
        self.assertEqual(w5["rate_limited"], 0)
        self.assertEqual(w5["weekly_rate_limited"], 1)
        self.assertEqual(cal["observed_lock_count"], 0)
        self.assertEqual(cal["weekly_lock_count"], 1)
        self.assertEqual(cal["avg_attempts_at_weekly_lock"], 4.0)

        # Weekly rolling schedule groups 4 billable calls by day and honors active weekly reset
        self.assertTrue(wrs["cooldown_active"])
        self.assertEqual(wrs["total_calls"], 4)
        self.assertEqual(wrs["first_batch"]["calls"], 2)
        self.assertEqual(wrs["recover_next_24h"]["calls"], 2)
        self.assertEqual(wrs["recover_next_48h"]["calls"], 3)
        self.assertGreaterEqual(len(wrs["buckets"]), 2)
        self.assertEqual(wrs["buckets"][-1]["cum_calls"], 4)
        self.assertEqual(wrs["buckets"][-1]["cum_pct"], 100.0)
        self.assertAlmostEqual(wrs["next_recover_ts"], weekly_reset_ts, delta=2.0)

        # Newer OK after weekly 429 lifts weekly_cooldown_active
        rows_lift = [
            (1, 91, 3, make_metadata(int(self.now - 30), args={"ImageName": "w_lifted"}), None),
        ]
        create_conv_db(self.conv_dir / "conv-weekly-lift.db", rows_lift, self.now - 5)
        rep2 = quota_report.build_report(self.conv_dir, now=self.now)
        self.assertFalse(rep2["window_5h"]["weekly_cooldown_active"])
        self.assertFalse(rep2["weekly_rolling_schedule"]["cooldown_active"])
        self.assertAlmostEqual(rep2["weekly_rolling_schedule"]["next_recover_ts"], t_d1 + quota_report.WINDOW_7D_SEC, delta=2.0)

    def test_context_report_import_sanity(self) -> None:
        self.assertTrue(callable(context_report.load))
        self.assertTrue(callable(context_report.collect_stats))
        self.assertTrue(hasattr(context_report, "Msg"))


if __name__ == "__main__":
    unittest.main()
