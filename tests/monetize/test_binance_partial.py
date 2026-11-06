"""partial and chains_partial are visible. window_state_24h and action_hint stay as they were."""
from __future__ import annotations

import unittest

from scrapling.monetize import binance_flows as BF
from scrapling.monetize import upd43


def _chain(covered_24h: bool) -> dict:
    return {
        "head": {"block": 1},
        "1h": {"window_fully_covered": True, "by_asset": {"USDT": {}}, "token_net_outflow_usd": 0, "net_outflow_usd_incl_native": 0},
        "24h": {
            "window_fully_covered": covered_24h,
            "by_asset": {"USDT": {}} if covered_24h else {},
            "token_net_outflow_usd": 0 if covered_24h else None,
            "net_outflow_usd_incl_native": 0 if covered_24h else None,
            "note": None,
        },
    }


def _run(inner: dict) -> dict:
    return upd43._wrap_snapshot(lambda: inner)()


class BinancePartialTests(unittest.TestCase):
    def test_partial_is_explicit_and_window_state_unchanged(self) -> None:
        snap = _run({
            "as_of": "2026-11-06T18:03:47Z",
            "action_hint": "pause",
            "chains": {"ethereum": _chain(True), "bsc": _chain(False)},
            "all_chains": {"action_hint": "pause", "provider_lag_chains": ["tron"], "net_outflow_usd_24h": 1},
        })
        ws = snap["all_chains"]["window_state_24h"]
        self.assertEqual(ws["state"], "partial")
        self.assertEqual(ws["chains_partial"], ["bsc"])
        self.assertIn("reason", ws)
        self.assertTrue(snap["partial"])
        self.assertEqual(snap["chains_partial"], ["bsc"])
        self.assertTrue(snap["all_chains"]["partial"])
        self.assertEqual(snap["all_chains"]["chains_partial"], ["bsc"])
        # Lag advisory is preserved, not invented by the partial fields.
        self.assertEqual(snap["action_hint"], "pause")
        self.assertEqual(snap["all_chains"]["action_hint"], "pause")
        self.assertEqual(snap["all_chains"]["provider_lag_chains"], ["tron"])
        self.assertEqual(snap["chains"]["bsc"]["24h"]["window_state"]["state"], "partial")
        self.assertEqual(snap["chains"]["ethereum"]["24h"]["window_state"]["state"], "complete")

    def test_complete_window_is_explicitly_false_and_omits_action_hint(self) -> None:
        snap = _run({
            "as_of": "2026-11-06T18:03:47Z",
            "chains": {"ethereum": _chain(True)},
            "all_chains": {"provider_lag_chains": [], "net_outflow_usd_24h": 0},
        })
        self.assertEqual(snap["all_chains"]["window_state_24h"]["state"], "complete")
        self.assertEqual(snap["all_chains"]["window_state_24h"]["chains_partial"], [])
        self.assertFalse(snap["partial"])
        self.assertEqual(snap["chains_partial"], [])
        self.assertFalse(snap["all_chains"]["partial"])
        self.assertNotIn("action_hint", snap)
        self.assertNotIn("action_hint", snap["all_chains"])

    def test_free_view_copies_partial_without_adding_action_hint(self) -> None:
        shown = BF.visible_partial({
            "partial": True,
            "chains_partial": ["bsc"],
            "all_chains": {"window_state_24h": {"state": "partial", "chains_partial": ["bsc"]}},
        })
        self.assertEqual(shown, {"partial": True, "chains_partial": ["bsc"]})
        self.assertNotIn("action_hint", shown)
        legacy = BF.visible_partial({
            "all_chains": {"window_state_24h": {"state": "partial", "chains_partial": ["bsc"]}},
        })
        self.assertEqual(legacy["partial"], True)
        self.assertEqual(legacy["chains_partial"], ["bsc"])
        quiet = BF.visible_partial({
            "partial": False,
            "chains_partial": [],
        })
        self.assertFalse(quiet["partial"])
        self.assertEqual(quiet["chains_partial"], [])
        self.assertNotIn("action_hint", quiet)


if __name__ == "__main__":
    unittest.main()
