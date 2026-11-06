"""Sample checks: match, mismatch, skip, error, stale, empty rate, and no banned fetches."""
from __future__ import annotations

import json
import os
import tempfile
import unittest

os.environ["SCRAPLING_ACCURACY_DIR"] = tempfile.mkdtemp(prefix="acc-")

from scrapling.monetize import accuracy_checks as ac
from scrapling.monetize import quality


class AccuracyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="acc-")
        os.environ["SCRAPLING_ACCURACY_DIR"] = self.dir
        self.calls = []

    def fetch(self, url, allow_homepage=False, method="GET", json_body=None):
        self.calls.append(url)
        self.assertNotIn("coingecko", url)
        self.assertNotIn("blockscout", url)
        if "aave.com" in url:
            return {"ok": True, "status": 200, "body": "<html>Aave protocol</html>", "final_url": url}
        if "uniswap.org" in url:
            return {"ok": False, "status": 500, "body": "", "error": "http 500", "final_url": url}
        if "lido.fi" in url:
            return {"ok": True, "status": 200, "body": "unrelated page", "final_url": url}
        if "etherscan.io" in url:
            return {"ok": True, "status": 200, "body": "Binance: Hot Wallet", "final_url": url}
        if "hyperliquid" in url:
            body = json.dumps([
                {"universe": [{"name": "xyz:TSLA"}, {"name": "xyz:NVDA"}, {"name": "xyz:AAPL"}]},
                [{"markPx": "100"}, {"markPx": "200"}, {"markPx": "10"}],
            ])
            return {"ok": True, "status": 200, "body": body, "final_url": url}
        return {"ok": False, "status": 0, "body": "", "error": "unexpected url", "final_url": url}

    def test_refuses_banned_and_private_targets_before_fetch(self) -> None:
        def boom(url, **kwargs):
            raise AssertionError("fetched " + url)

        banned = ac.fetch_public("https://api.coingecko.com/api/v3/ping", opener=boom, resolve=lambda host: ["1.1.1.1"])
        self.assertFalse(banned["ok"])
        self.assertIn("banned", banned["error"])
        private = ac.validate_url("https://etherscan.io/address/0xabc", resolve=lambda host: ["127.0.0.1"])
        self.assertIn("loopback", private)
        meta = ac.validate_url("https://etherscan.io/", resolve=lambda host: ["169.254.169.254"])
        self.assertIn("metadata", meta)
        self.assertIsNotNone(ac.validate_url("http://aave.com/", allow_homepage=True))
        self.assertIsNotNone(ac.validate_url("https://10.1.1.1/", allow_homepage=True))

    def test_match_mismatch_skip_error_and_rate_bounds(self) -> None:
        now = 1_700_000_000.0
        state = ac.run_once(
            now=now,
            fetch=self.fetch,
            company_ours=lambda q: {
                "aave": {"name": "Aave", "website": "https://aave.com", "as_of": "2026-01-01T00:00:00Z"},
                "uniswap": {"name": "Uniswap", "website": "https://uniswap.org", "as_of": "2026-01-01T00:00:00Z"},
                "lido": {"name": "Lido", "website": "https://lido.fi", "as_of": "2026-01-01T00:00:00Z"},
            }[q],
            label_ours=lambda address: {"entity": "Binance", "as_of": "2026-01-01T00:00:00Z"},
            perps_ours=lambda: {
                "fetched_at": now - 30,
                "as_of": "2026-01-01T00:00:00Z",
                "markets": [
                    {"symbol": "TSLA", "mark_px": 100},
                    {"symbol": "NVDA", "mark_px": 200},
                    {"symbol": "AAPL", "mark_px": 50},
                ],
            },
            flows_ours=lambda: {},
        )
        company = state["products"]["company"]
        self.assertEqual(company["matched"], 1)
        self.assertEqual(company["mismatched"], 1)
        self.assertEqual(company["errors"], 1)
        self.assertEqual(company["sample_count"], 3)
        self.assertEqual(company["checked"], 2)
        self.assertAlmostEqual(company["match_rate"], round(1 / 3, 4))
        self.assertGreaterEqual(company["match_rate"], 0)
        self.assertLessEqual(company["match_rate"], 1)
        wallet = state["products"]["wallet-label"]
        self.assertEqual(wallet["matched"], 1)
        self.assertEqual(wallet["sample_count"], 1)
        self.assertEqual(wallet["match_rate"], 1.0)
        self.assertTrue(any(r["result"] == "skipped" and "ownership" in r["reason"] for r in wallet["rows"]))
        flows = state["products"]["binance-flows"]
        self.assertIsNone(flows["match_rate"])
        self.assertEqual(flows["status"], "ungraded")
        self.assertEqual(flows["unknown"], 1)
        self.assertTrue(any(r["result"] == "skipped" and "announcement" in r["reason"].lower() for r in flows["rows"]))
        perps = state["products"]["stock-perps"]
        self.assertIsNone(perps["match_rate"])
        self.assertEqual(perps["status"], "ungraded")
        self.assertEqual(perps["self_consistency"]["matched"], 2)
        self.assertEqual(perps["self_consistency"]["mismatched"], 1)
        self.assertIn("same upstream", perps["self_consistency"]["note"].lower())
        for block in state["products"].values():
            rate = block["match_rate"]
            if rate is not None:
                self.assertGreaterEqual(rate, 0)
                self.assertLessEqual(rate, 1)
            self.assertIn("sample_count", block)
            self.assertIn("rows", block)

    def test_stale_snapshot_is_not_a_mismatch_and_empty_rate_is_null(self) -> None:
        now = 1_700_000_000.0
        state = ac.run_once(
            now=now,
            fetch=self.fetch,
            company_ours=lambda q: None,
            label_ours=lambda address: None,
            perps_ours=lambda: {"fetched_at": now - 10_000, "markets": [{"symbol": "TSLA", "mark_px": 1}]},
            flows_ours=lambda: None,
        )
        company = state["products"]["company"]
        self.assertEqual(company["checked"], 0)
        self.assertIsNone(company["match_rate"])
        self.assertNotEqual(company["match_rate"], 1)
        self.assertEqual(company["status"], "ungraded")
        perps = state["products"]["stock-perps"]
        tsla = next(r for r in perps["rows"] if r["sample_id"] == "TSLA")
        self.assertEqual(tsla["result"], "skipped")
        self.assertNotEqual(tsla["result"], "mismatch")
        self.assertIsNone(perps["self_consistency"]["match_rate"])
        self.assertNotIn("https://api.hyperliquid.xyz/info", self.calls)
        loaded = ac.current(now + ac.STALE_AFTER_SEC + 5)
        self.assertTrue(loaded["stale"])
        self.assertIsNone(loaded["products"]["company"]["match_rate"])

    def test_scheduler_persists_and_skips_early_rerun(self) -> None:
        now = 1_800_000_000.0
        first = ac.maybe_run(now=now, fetch=self.fetch, company_ours=lambda q: None, label_ours=lambda a: None,
                              perps_ours=lambda: None, flows_ours=lambda: None)
        calls = len(self.calls)
        second = ac.maybe_run(now=now + 3600, fetch=self.fetch, company_ours=lambda q: None, label_ours=lambda a: None,
                               perps_ours=lambda: None, flows_ours=lambda: None)
        self.assertEqual(len(self.calls), calls)
        self.assertEqual(first["started_at_unix"], second["started_at_unix"])

    def test_html_escapes_and_does_not_replace_grades(self) -> None:
        page = quality._html_page({
            "as_of": "2026-11-06T00:00:00Z",
            "summary": {"grade_counts": {"A": 1}},
            "products": {"coin": {"product": "coin", "title": "Coin", "grade": "A", "score": 100,
                                  "sources": ["Chainlink"], "coverage": "coins=1", "last_checked": "2026-11-06T00:00:00Z"}},
            "sample_checks": {
                "started_at": "2026-11-06T00:00:00Z",
                "stale": False,
                "products": {"company": {
                    "status": "ungraded", "match_rate": None, "sample_count": 0, "matched": 0,
                    "mismatched": 0, "skipped": 0, "errors": 0, "unknown": 0,
                    "limitation": "<script>alert(1)</script>",
                }},
            },
        })
        self.assertIn("Sample checks, not guaranteed correctness", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn(">A<", page)
        empty = ac._tally([], independent=True)
        self.assertEqual(empty["sample_count"], 0)
        self.assertIsNone(empty["match_rate"])
        self.assertNotEqual(empty["match_rate"], 1)


if __name__ == "__main__":
    unittest.main()
