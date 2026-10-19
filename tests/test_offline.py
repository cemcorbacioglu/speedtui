"""Offline tests for the pure logic (no network). Run: python -m unittest"""

from __future__ import annotations

import random
import tempfile
import unittest
from pathlib import Path

import httpx

from speedtui.comparisons import (
    describe,
    format_duration,
    latency_notes,
    load_reference,
    pick_comparisons,
)
from speedtui.measure import (
    LatencyResult,
    ServerInfo,
    SpeedTestResult,
    ThroughputResult,
    jitter,
    server_time_ms,
)
from speedtui.storage import History
from speedtui.ui.bigdigits import HEIGHT, format_speed, render_big


class MeasureTests(unittest.TestCase):
    def test_jitter_is_mean_consecutive_difference(self) -> None:
        self.assertAlmostEqual(jitter([10, 12, 11, 15]), (2 + 1 + 4) / 3)
        self.assertEqual(jitter([10]), 0.0)

    def test_server_time_sums_cloudflare_metrics(self) -> None:
        response = httpx.Response(
            200,
            headers=[
                ("server-timing", "cfSpeedEdge;dur=2, cfSpeedWorker;dur=266"),
                ("server-timing", 'cfL4;desc="?proto=TCP&rtt=13967"'),
            ],
        )
        self.assertEqual(server_time_ms(response), 268)

    def test_server_time_missing_header(self) -> None:
        self.assertEqual(server_time_ms(httpx.Response(200)), 0)


class ComparisonTests(unittest.TestCase):
    def test_every_reference_item_has_a_source(self) -> None:
        for item in load_reference()["item"]:
            self.assertTrue(item["source"], item["name"])
            self.assertTrue(item["short"], item["name"])
            self.assertIn(item["direction"], ("download", "upload"))

    def test_size_comparison(self) -> None:
        item = {"name": "x", "short": "x", "direction": "download", "kind": "size", "gb": 1, "source": "s"}
        # 1 GB = 8000 megabits; at 80 Mbps that is 100 s.
        result = describe(item, 80)
        self.assertIn("2 minutes", result.text)
        self.assertEqual(result.value, "2 min")

    def test_pick_has_both_directions(self) -> None:
        for seed in range(20):
            picks = pick_comparisons(100, 20, rng=random.Random(seed))
            self.assertIn(len(picks), (3, 4))
            texts = " ".join(p.text for p in picks)
            self.assertTrue("Upload" in texts or "video-call" in texts)

    def test_durations(self) -> None:
        self.assertEqual(format_duration(0.5), "under a second")
        self.assertEqual(format_duration(30), "30 seconds")
        self.assertEqual(format_duration(3 * 86400), "3.0 days")

    def test_latency_notes(self) -> None:
        notes = [n.text for n in latency_notes(20, 1)]
        self.assertIn("4,000 km", notes[0])
        self.assertIn("great", notes[1])
        self.assertIn("fine", notes[2])
        self.assertIn("jitter", latency_notes(50, 45)[2].text)


class BigDigitTests(unittest.TestCase):
    def test_render_is_rectangular(self) -> None:
        rows = render_big("123.4")
        self.assertEqual(len(rows), HEIGHT)
        self.assertEqual(len({len(r) for r in rows}), 1)

    def test_format_speed(self) -> None:
        self.assertEqual(format_speed(92.44), "92.4")
        self.assertEqual(format_speed(940.2), "940")
        self.assertEqual(format_speed(None), "--")


class StorageTests(unittest.TestCase):
    def test_save_and_read_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            history = History(Path(tmp) / "sub" / "history.db")
            result = SpeedTestResult(
                ServerInfo("IST", "TR", "192.0.2.1"),
                LatencyResult([20.0, 22.0], 21.0, 2.0),
                ThroughputResult("download", 90.0, 10**8, 9.7, 6),
                ThroughputResult("upload", 22.0, 10**7, 9.7, 6),
            )
            history.save(result, 10)
            history.save(result, 10)
            entries = history.recent()
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0].colo, "IST")
            self.assertEqual(entries[0].download_mbps, 90.0)


if __name__ == "__main__":
    unittest.main()
