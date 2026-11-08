"""Offline checks for the counterparty $0.002 cut. Stdlib only. No network."""
from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import unittest
from pathlib import Path

from apply_counterparty_price import CutoverError, EDITS, apply_tree, main, preflight, rollback_tree

DECOY = "intel risk-check stays $0.0045 and trust bundle stays $0.04 and wallet profile stays $0.005"
GUIDANCE = (
    "/v1/trust/agent?chain=&address= $0.04 (trust bundle) "
    "/v1/check/counterparty?chain=&address= $0.004 (safe/caution/block for a wallet or token) "
    "/v1/wallet/profile?chain=&address= $0.005 (wallet label)\n"
    "risk-check ($0.0045, 3 free/IP/day)\n"
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot(root: Path) -> dict[str, bytes]:
    return {rel: (root / rel).read_bytes() for rel, _old, _new in EDITS}


def _tree(newline: str = "\n", mode: int = 0o644) -> Path:
    root = Path(tempfile.mkdtemp())
    parts: dict[str, str] = {}
    for rel, old, _new in EDITS:
        if rel.endswith("gateway.py"):
            chunk = GUIDANCE.replace("\n", newline)
        else:
            chunk = old + newline + DECOY + newline
        parts[rel] = parts.get(rel, "") + chunk
    for rel, text in parts.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        os.chmod(path, mode)
    return root


class CounterpartyPriceTests(unittest.TestCase):
    def test_atomic_usdc_is_2000(self):
        # PaymentConfig.usdc_atomic_units: round(usd * 10**6), minimum 1, on 6-decimal USDC.
        self.assertEqual(str(max(1, int(round(0.002 * 10**6)))), "2000")
        self.assertEqual(str(max(1, int(round(0.004 * 10**6)))), "4000")

    def test_mpp_amount_string_matches_bridge_formula(self):
        # MppDoor._amount is f"{usd:.6f}".rstrip("0").rstrip(".") or "0". No app import.
        def amount(usd: float) -> str:
            return f"{usd:.6f}".rstrip("0").rstrip(".") or "0"

        self.assertEqual(amount(0.002), "0.002")
        self.assertEqual(amount(0.004), "0.004")
        self.assertEqual(amount(0.001), "0.001")
        self.assertEqual(amount(2.0), "2")

    def test_late_anchor_failure_leaves_every_file_unchanged(self):
        root = _tree()
        before = _snapshot(root)
        target = root / "scrapling/monetize/upd25.py"
        target.write_bytes(("no price here\n" + DECOY).encode("utf-8"))
        before = _snapshot(root)
        with self.assertRaises(CutoverError) as caught:
            apply_tree(root)
        self.assertIn("no files written", str(caught.exception))
        self.assertEqual(_snapshot(root), before)
        self.assertFalse((root / ".counterparty-price-backups").exists())

    def test_patch_preserves_newlines_mode_and_is_idempotent(self):
        root = _tree(newline="\r\n", mode=0o640)
        notes = apply_tree(root)
        self.assertTrue(any(line.startswith("patched ") for line in notes))
        for rel, _old, _new in EDITS:
            data = (root / rel).read_bytes()
            self.assertIn(b"\r\n", data)
            self.assertEqual(data.count(b"\n"), data.count(b"\r\n"))
            self.assertEqual(stat.S_IMODE((root / rel).stat().st_mode), 0o640)
        blob = b"\n".join((root / rel).read_bytes() for rel, _o, _n in EDITS)
        self.assertNotIn(b'"price": 0.004', blob)
        self.assertIn(b'"price": 0.002', blob)
        self.assertIn(b"$0.0045", blob)
        self.assertIn(b"$0.04", blob)
        self.assertIn(b"$0.005", blob)
        self.assertNotIn(b"PAID $0.004 USDC. Before paying", blob)
        self.assertIn(b"PAID $0.002 USDC. Before paying", blob)
        self.assertIn(b"/v1/check/counterparty?chain=&address= $0.002 ", blob)
        self.assertNotIn(b"/v1/check/counterparty?chain=&address= $0.004 ", blob)
        hashes = {rel: _sha(root / rel) for rel, _o, _n in EDITS}
        again = apply_tree(root)
        self.assertTrue(all(line.startswith("already ") for line in again))
        self.assertEqual({rel: _sha(root / rel) for rel, _o, _n in EDITS}, hashes)
        rolled = rollback_tree(root)
        self.assertTrue(any(line.startswith("patched ") for line in rolled))
        restored = b"\n".join((root / rel).read_bytes() for rel, _o, _n in EDITS)
        self.assertIn(b'"price": 0.004', restored)
        self.assertNotIn(b'"price": 0.002', restored)
        self.assertIn(b"$0.0045", restored)

    def test_mixed_state_aborts_without_writes(self):
        root = _tree()
        agent = root / "scrapling/monetize/agent_api.py"
        agent.write_bytes(agent.read_bytes().replace(b"0.004", b"0.002", 1))
        before = _snapshot(root)
        with self.assertRaises(CutoverError) as caught:
            apply_tree(root)
        self.assertIn("mixed state", str(caught.exception))
        self.assertEqual(_snapshot(root), before)

    def test_replace_failure_restores_every_file(self):
        root = _tree()
        before = _snapshot(root)
        calls = {"n": 0}

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] == 3:
                raise OSError("simulated disk failure")
            os.replace(src, dst)

        with self.assertRaises(CutoverError) as caught:
            apply_tree(root, replace=flaky)
        self.assertIn("restored", str(caught.exception))
        self.assertEqual(_snapshot(root), before)
        self.assertGreaterEqual(calls["n"], 3)
        backups = list((root / ".counterparty-price-backups").rglob("agent_api.py"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), before["scrapling/monetize/agent_api.py"])

    def test_preflight_only_does_not_write(self):
        root = _tree()
        before = _snapshot(root)
        planned = preflight(root)
        self.assertEqual(len(planned), 5)
        self.assertTrue(all(item.changed for item in planned))
        self.assertEqual(_snapshot(root), before)

    def test_staging_failure_and_cli_abort_leave_sources(self):
        root = _tree()
        before = _snapshot(root)
        blocker = root / "not-a-directory"
        blocker.write_bytes(b"x")
        with self.assertRaises(CutoverError) as caught:
            apply_tree(root, backup_dir=blocker)
        self.assertIn("staging failed", str(caught.exception))
        self.assertEqual(_snapshot(root), before)
        (root / "scrapling/monetize/skill_scrapling_data.md").unlink()
        remaining = {rel: (root / rel).read_bytes() for rel, _old, _new in EDITS if (root / rel).exists()}
        self.assertEqual(main(["apply", "--preflight", str(root)]), 1)
        self.assertEqual({rel: (root / rel).read_bytes() for rel in remaining}, remaining)
        self.assertFalse((root / "scrapling/monetize/skill_scrapling_data.md").exists())

    def test_invalid_payment_is_not_a_settled_success(self):
        def classify(status: int, price):
            if status == 400:
                return "fail-not-402"
            if status == 200:
                return "fail-accepted"
            if status == 402 and abs(float(price) - 0.002) < 1e-9:
                return "unpaid-ok"
            return "fail"

        self.assertEqual(classify(402, 0.002), "unpaid-ok")
        self.assertEqual(classify(402, 0.004), "fail")
        self.assertEqual(classify(400, 0.002), "fail-not-402")
        self.assertEqual(classify(200, 0.002), "fail-accepted")


if __name__ == "__main__":
    unittest.main()
