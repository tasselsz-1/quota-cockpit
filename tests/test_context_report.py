import sys
from pathlib import Path
import unittest

COCKPIT_DIR = Path(__file__).resolve().parents[1] / "sidecars" / "cockpit"
if str(COCKPIT_DIR) not in sys.path:
    sys.path.insert(0, str(COCKPIT_DIR))

import context_report as cr  # noqa: E402


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _tag(fn: int, wt: int) -> bytes:
    return _varint((fn << 3) | wt)


def _pb_int(fn: int, val: int) -> bytes:
    return _tag(fn, 0) + _varint(val)


def _pb_bytes(fn: int, payload: bytes) -> bytes:
    return _tag(fn, 2) + _varint(len(payload)) + payload


def _make_ts(epoch_sec: int) -> bytes:
    return _pb_int(1, epoch_sec)


def _make_usage(uncached: int, output: int, cache_read: int, thinking: int = 0, resp: int = 0) -> bytes:
    buf = b""
    if uncached:
        buf += _pb_int(2, uncached)
    if output:
        buf += _pb_int(3, output)
    if cache_read:
        buf += _pb_int(5, cache_read)
    if thinking:
        buf += _pb_int(9, thinking)
    if resp:
        buf += _pb_int(10, resp)
    return buf


def _make_planner_step(pos: int, uncached: int, cache_read: int, output: int = 200, ts: int = 1_795_500_000) -> cr.Step:
    md = _pb_bytes(1, _make_ts(ts)) + _pb_bytes(7, _make_ts(ts + 5)) + _pb_bytes(9, _make_usage(uncached, output, cache_read))
    payload = _pb_int(1, 15) + _pb_int(4, cr.STATUS_DONE) + _pb_bytes(5, md)
    return cr.Step(pos, payload, 15, cr.STATUS_DONE, md)


def _make_checkpoint_step(pos: int, ck_idx: int = 0, intent: str = "summary") -> cr.Step:
    ck = _pb_int(1, ck_idx) + _pb_bytes(4, intent.encode("utf-8"))
    payload = _pb_int(1, 23) + _pb_int(4, cr.STATUS_DONE) + _pb_bytes(cr.STEP_CHECKPOINT, ck)
    return cr.Step(pos, payload, 23, cr.STATUS_DONE, b"")


def _make_gen_with_usage(uncached: int, cache_read: int, output: int = 120) -> cr.Msg:
    chat = _pb_bytes(4, _make_usage(uncached, output, cache_read))
    return cr.Msg(_pb_bytes(1, chat))


def _make_simple_step(pos: int, step_type: int) -> cr.Step:
    payload = _pb_int(1, step_type) + _pb_int(4, cr.STATUS_DONE)
    return cr.Step(pos, payload, step_type, cr.STATUS_DONE, b"")


class ContextCacheStatsTests(unittest.TestCase):
    def test_empty_steps_no_gen(self):
        c = cr.collect_cache_stats([], None)
        self.assertFalse(c["has_data"])
        self.assertEqual(c["turns"], 0)
        self.assertIsNone(c["latest"])
        self.assertEqual(c["recent"], [])

    def test_cold_high_large_new_file_and_true_miss(self):
        t0 = 1_795_500_000
        steps = [
            # Turn 1: cold start (0 cache read)
            _make_planner_step(0, uncached=20_000, cache_read=0, ts=t0),
            # Turn 2: high hit (90.9%, prefix retention 100%)
            _make_planner_step(1, uncached=2_000, cache_read=20_000, ts=t0 + 20),
            # Turn 3: huge new file read (60k new) while all 22k prior prefix stayed cached -> low, NOT miss!
            _make_planner_step(2, uncached=60_000, cache_read=22_000, ts=t0 + 40),
            # Turn 4: high hit again (97.6%)
            _make_planner_step(3, uncached=2_000, cache_read=82_000, ts=t0 + 60),
            # Turn 5: true cache expiration/miss (only 4k prefix kept out of 84k -> 4.8% hit, <50% retention)
            _make_planner_step(4, uncached=80_000, cache_read=4_000, ts=t0 + 600),
            # Turn 6: rebuilt immediately (97.7%)
            _make_planner_step(5, uncached=2_000, cache_read=84_000, ts=t0 + 620),
        ]

        c = cr.collect_cache_stats(steps, None)
        self.assertTrue(c["has_data"])
        self.assertFalse(c["has_compaction"])
        self.assertFalse(c["compacted_pending"])
        self.assertEqual(c["turns"], 6)
        self.assertEqual([r["status"] for r in c["recent"]], ["cold", "high", "low", "high", "miss", "high"])
        self.assertFalse(c["recent"][2]["is_miss"])
        self.assertEqual(c["recent"][2]["prefix_retention_pct"], 100.0)
        self.assertTrue(c["recent"][4]["is_miss"])
        self.assertEqual(c["session"]["misses"], 1)
        self.assertEqual(c["latest"]["hit_rate_pct"], 97.7)
        self.assertEqual(c["latest"]["ts"], t0 + 625)

    def test_compaction_splits_segment_and_cold_start_not_counted_as_miss(self):
        t0 = 1_795_500_000
        steps = [
            _make_planner_step(0, uncached=20_000, cache_read=0, ts=t0),
            _make_planner_step(1, uncached=2_000, cache_read=20_000, ts=t0 + 20),
            _make_planner_step(2, uncached=22_000, cache_read=0, ts=t0 + 400),  # 1 miss in old segment
            _make_checkpoint_step(3, ck_idx=0, intent="compact"),
            # First turn after compact -> cold start, NOT miss
            _make_planner_step(4, uncached=15_000, cache_read=0, ts=t0 + 500),
            _make_planner_step(5, uncached=1_000, cache_read=15_000, ts=t0 + 520),
        ]

        c = cr.collect_cache_stats(steps, None)
        self.assertTrue(c["has_compaction"])
        self.assertFalse(c["compacted_pending"])
        self.assertEqual(c["session"]["turns"], 5)
        self.assertEqual(c["session"]["misses"], 1)
        self.assertEqual(c["segment"]["turns"], 2)
        self.assertEqual(c["segment"]["misses"], 0)
        self.assertEqual(c["recent"][3]["status"], "cold")
        self.assertTrue(c["recent"][3]["after_compact"])
        self.assertFalse(c["recent"][3]["is_miss"])
        self.assertEqual(c["latest"]["hit_rate_pct"], 93.8)

    def test_compacted_pending_when_checkpoint_is_last_step(self):
        t0 = 1_795_500_000
        steps = [
            _make_planner_step(0, uncached=20_000, cache_read=0, ts=t0),
            _make_planner_step(1, uncached=2_000, cache_read=20_000, ts=t0 + 15),
            _make_checkpoint_step(2, ck_idx=0, intent="just compacted"),
        ]
        c = cr.collect_cache_stats(steps, None)
        self.assertTrue(c["has_data"])
        self.assertTrue(c["has_compaction"])
        self.assertTrue(c["compacted_pending"])
        self.assertEqual(c["segment"]["turns"], 0)
        self.assertEqual(c["session"]["turns"], 2)
        self.assertEqual(c["latest"]["hit_rate_pct"], 90.9)

    def test_fallback_to_gen_metadata_and_render_stats_line(self):
        gen = _make_gen_with_usage(uncached=3_000, cache_read=97_000, output=400)
        st = cr.collect_stats([], gen, 250_000)
        c = st["cache"]
        self.assertTrue(c["has_data"])
        self.assertEqual(c["turns"], 1)
        self.assertEqual(c["latest"]["hit_rate_pct"], 97.0)
        self.assertEqual(c["latest"]["status"], "high")
        rendered = cr.render_stats(st, "Test Model")
        self.assertIn("Prompt cache: latest 97.0%", rendered)
        self.assertIn("97.0k hit / 3.0k new", rendered)


class ContextStepStatsTests(unittest.TestCase):
    def test_empty_steps(self):
        s = cr.collect_step_stats([])
        self.assertFalse(s["has_data"])
        self.assertEqual(s["total"], 0)
        self.assertFalse(s["has_compaction"])
        self.assertEqual(s["compactions"], 0)
        self.assertEqual(s["segment"]["total"], 0)
        self.assertEqual(s["session"]["total"], 0)

    def test_uncompacted_step_breakdown_and_render_order(self):
        t0 = 1_795_500_000
        steps = [
            _make_simple_step(0, 101),  # SYSTEM
            _make_simple_step(1, 14),   # USER
            _make_planner_step(2, uncached=20_000, cache_read=0, ts=t0),  # AGENT
            _make_simple_step(3, 8),    # TOOL (VIEW_FILE)
            _make_simple_step(4, 132),  # TOOL (GENERIC)
            _make_planner_step(5, uncached=1_000, cache_read=20_000, ts=t0 + 15),  # AGENT
        ]
        st = cr.collect_stats(steps, None, 250_000)
        s = st["steps"]
        self.assertTrue(s["has_data"])
        self.assertEqual(s["total"], 6)
        self.assertFalse(s["has_compaction"])
        self.assertEqual(s["compactions"], 0)
        self.assertEqual(s["session"], {"total": 6, "user": 1, "agent": 2, "tool": 2, "system": 1, "checkpoint": 0})
        self.assertEqual(s["segment"], s["session"])

        rendered = cr.render_stats(st, "Test Model")
        cache_idx = rendered.find("Prompt cache:")
        steps_idx = rendered.find("▸ Steps: 6 (model 2 · tool 2 · user 1)")
        self.assertGreaterEqual(cache_idx, 0)
        self.assertGreater(steps_idx, cache_idx)

    def test_compaction_splits_segment_steps_and_just_compacted_zero(self):
        t0 = 1_795_500_000
        steps = [
            _make_simple_step(0, 14),
            _make_planner_step(1, uncached=20_000, cache_read=0, ts=t0),
            _make_simple_step(2, 8),
            _make_checkpoint_step(3, ck_idx=0, intent="compact 1"),
            _make_simple_step(4, 14),
            _make_planner_step(5, uncached=15_000, cache_read=0, ts=t0 + 60),
            _make_simple_step(6, 8),
            _make_simple_step(7, 132),
        ]
        st = cr.collect_stats(steps, None, 250_000)
        s = st["steps"]
        self.assertTrue(s["has_compaction"])
        self.assertEqual(s["compactions"], 1)
        self.assertEqual(s["total"], 8)
        self.assertEqual(s["session"], {"total": 8, "user": 2, "agent": 2, "tool": 3, "system": 0, "checkpoint": 1})
        self.assertEqual(s["segment"], {"total": 4, "user": 1, "agent": 1, "tool": 2, "system": 0, "checkpoint": 0})

        rendered = cr.render_stats(st, "Test Model")
        self.assertIn("▸ Steps: segment 4 (model 1 / tool 2) · session 8 (1 compactions)", rendered)

        just_compacted = steps + [_make_checkpoint_step(8, ck_idx=1, intent="compact 2")]
        s2 = cr.collect_step_stats(just_compacted)
        self.assertEqual(s2["compactions"], 2)
        self.assertEqual(s2["total"], 9)
        self.assertEqual(s2["segment"]["total"], 0)


if __name__ == "__main__":
    unittest.main()
