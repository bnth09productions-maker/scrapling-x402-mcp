"""Stock-perps quality uses a real rubric. A missing path is ungraded, not a fake F or A."""
from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from scrapling.monetize import quality


def _body(as_of, n=5):
    return {
        "free": True,
        "as_of": as_of,
        "source": "hyperliquid.public.info",
        "markets": [{"symbol": "TSLA"}] * n,
        "count": n,
    }


class StockPerpsQualityTests(unittest.TestCase):
    def tearDown(self) -> None:
        quality.PRODUCTS.pop("__no_path", None)

    def _recompute(self, http):
        with patch.object(quality, "_http_get", side_effect=http), patch.object(quality, "_save_cache", lambda: None):
            return quality.recompute()

    def test_rubric_has_the_paid_path_and_free_probe(self) -> None:
        meta = quality.PRODUCTS["stock-perps"]
        self.assertEqual(meta["path"], "/v1/perps/stocks")
        self.assertEqual(meta["free_probe"], "/v1/free/stock-perps.json")
        self.assertEqual(meta["refresh_promised_sec"], 900)
        self.assertIn("as_of", meta["key_fields"])

    def test_fresh_probe_is_scored_and_stale_probe_is_not_an_a(self) -> None:
        now = quality._iso(time.time())
        stale = quality._iso(time.time() - 7 * 86400)

        def http_fresh(path, timeout=25):
            if path.split("?")[0] == "/v1/free/stock-perps.json":
                return 200, _body(now)
            return 200, {"as_of": now, "feeds": []}

        fresh = self._recompute(http_fresh)["grades"]["stock-perps"]
        self.assertNotIn("error", fresh)
        self.assertEqual(fresh["path"], "/v1/perps/stocks")
        self.assertEqual(fresh["components"]["freshness"], 100.0)
        self.assertIn(fresh["grade"], ("A", "B", "C", "D", "F"))
        self.assertNotEqual(fresh.get("error"), "'path'")

        def http_stale(path, timeout=25):
            if path.split("?")[0] == "/v1/free/stock-perps.json":
                return 200, _body(stale, n=1)
            return 200, {"as_of": now, "feeds": []}

        old = self._recompute(http_stale)["grades"]["stock-perps"]
        self.assertNotIn("error", old)
        self.assertEqual(old["components"]["freshness"], 0.0)
        self.assertNotEqual(old["grade"], "A")
        self.assertGreater(old["freshness_seconds"], 900)

    def test_missing_path_is_ungraded(self) -> None:
        quality.PRODUCTS["__no_path"] = {
            "title": "Incomplete rubric",
            "family": "perps",
            "sources": ["none"],
            "free_probe": "/v1/free/stock-perps.json",
        }

        def http(path, timeout=25):
            return 200, _body(quality._iso(time.time()))

        row = self._recompute(http)["grades"]["__no_path"]
        self.assertEqual(row["grade"], "ungraded")
        self.assertIsNone(row["score"])
        self.assertIn("missing path", row["error"])
        self.assertNotEqual(row["grade"], "A")
        self.assertNotEqual(row["grade"], "F")


if __name__ == "__main__":
    unittest.main()
