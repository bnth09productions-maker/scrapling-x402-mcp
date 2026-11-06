"""last_confirmed is a source-published time, or null. Never a refresh clock."""
from __future__ import annotations

import time
import unittest

from scrapling.monetize import labels as L


def _reset() -> None:
    L._ix["idx"] = {"placeholder": []}
    L._ix["built"] = time.time()
    L._ix["as_of"] = "2020-01-01T00:00:00Z"
    L._ix["counts"] = {}


def _put(addr: str, *, as_of: str, confirmed_at=None, entity: str = "Example") -> None:
    L._add(
        L._ix["idx"], L._ix["counts"], addr, ["ethereum"], "Example label", entity,
        "exchange_hot_wallet", "unit source", "https://example.invalid", "high", as_of, confirmed_at,
    )


class LastConfirmedTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset()

    def test_source_parser_does_not_invent_a_clock_or_zone(self) -> None:
        self.assertIsNone(L._source_confirmed(None))
        self.assertIsNone(L._source_confirmed(""))
        self.assertIsNone(L._source_confirmed("now"))
        self.assertIsNone(L._source_confirmed("2024-05-01T12:00:00"))  # no zone
        self.assertEqual(L._source_confirmed("2024-05-01"), "2024-05-01")
        self.assertEqual(L._source_confirmed("2024-05-01T12:00:00Z"), "2024-05-01T12:00:00Z")
        self.assertEqual(L._source_confirmed("2024-05-01T12:00:00+02:00"), "2024-05-01T10:00:00Z")
        self.assertIsNone(L._source_confirmed("not-a-date"))

    def test_single_null_when_only_refresh_time_is_known(self) -> None:
        addr = "0x" + "ab" * 20
        _put(addr, as_of="2026-11-06T17:45:53Z", confirmed_at=None)
        row = L.lookup(addr)
        self.assertTrue(row["match"])
        self.assertIsNone(row["last_confirmed"])
        self.assertEqual(row["as_of"], "2020-01-01T00:00:00Z")  # corpus rebuild, not confirmation
        self.assertEqual(row["label_as_of"], "2026-11-06T17:45:53Z")
        self.assertIsNone(row["labels"][0]["last_confirmed"])

    def test_single_uses_source_confirmation(self) -> None:
        addr = "0x" + "cd" * 20
        _put(addr, as_of="2026-11-06T17:45:53Z", confirmed_at="2024-05-01")
        row = L.lookup(addr)
        self.assertEqual(row["last_confirmed"], "2024-05-01")
        self.assertNotEqual(row["last_confirmed"], row["as_of"])
        self.assertNotEqual(row["last_confirmed"], row["label_as_of"])

    def test_latest_of_several_source_times(self) -> None:
        addr = "0x" + "ef" * 20
        _put(addr, as_of="file-mtime-ignored", confirmed_at="2023-01-01", entity="Older")
        _put(addr, as_of="file-mtime-ignored", confirmed_at="2024-06-01T00:00:00Z", entity="Newer")
        row = L.lookup(addr)
        self.assertEqual(row["last_confirmed"], "2024-06-01T00:00:00Z")

    def test_special_and_unknown_are_null(self) -> None:
        zero = L.lookup("0x" + "0" * 40)
        self.assertTrue(zero["match"])
        self.assertEqual(zero["category"], "special")
        self.assertIsNone(zero["last_confirmed"])
        unknown = L.lookup("0x" + "11" * 20)
        self.assertFalse(unknown["match"])
        self.assertIsNone(unknown["last_confirmed"])

    def test_bulk_and_free_variants(self) -> None:
        confirmed = "0x" + "22" * 20
        refresh_only = "0x" + "33" * 20
        _put(confirmed, as_of="2026-11-06T17:45:53Z", confirmed_at="2024-08-15T00:00:00Z")
        _put(refresh_only, as_of="2026-11-06T17:45:53Z", confirmed_at=None)
        rows = L.lookup_many([
            confirmed,
            refresh_only,
            "0x" + "0" * 40,
            "not-an-address",
        ])
        self.assertEqual(rows[0]["last_confirmed"], "2024-08-15T00:00:00Z")
        self.assertIsNone(rows[1]["last_confirmed"])
        self.assertIsNone(rows[2]["last_confirmed"])
        self.assertIsNone(rows[3]["last_confirmed"])
        self.assertIn("error", rows[3])
        free_confirmed = L.free_label_view(rows[0], "https://scraplingdata.xyz")
        free_refresh = L.free_label_view(rows[1], "https://scraplingdata.xyz")
        free_zero = L.free_label_view(rows[2], "https://scraplingdata.xyz")
        self.assertEqual(free_confirmed["last_confirmed"], "2024-08-15T00:00:00Z")
        self.assertIsNone(free_refresh["last_confirmed"])
        self.assertIsNone(free_zero["last_confirmed"])
        self.assertNotEqual(free_refresh["last_confirmed"], free_refresh["as_of"])
        self.assertTrue(free_confirmed["free"])

    def test_row_helper_ignores_file_generated_at(self) -> None:
        self.assertIsNone(L._row_confirmed({"generated_at": "2026-11-06T17:45:53Z", "as_of": "2026-11-06T17:45:53Z"}))
        self.assertEqual(L._row_confirmed({"date": "2021-03-04", "generated_at": "2026-11-06T00:00:00Z"}), "2021-03-04")


if __name__ == "__main__":
    unittest.main()
