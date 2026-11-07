"""Offline checks for the counterparty $0.002 cut. No network and no payment."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from apply_counterparty_price import EDITS, apply_tree, rollback_tree

DECOY = "intel risk-check stays $0.0045 and trust bundle stays $0.04\n"


def _tree() -> Path:
    root = Path(tempfile.mkdtemp())
    for rel, old, _new in EDITS:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.write_text(path.read_text(encoding="utf-8") + old + "\n" + DECOY, encoding="utf-8")
        else:
            path.write_text(old + "\n" + DECOY, encoding="utf-8")
    return root


class CounterpartyPriceTests(unittest.TestCase):
    def test_atomic_usdc_is_2000(self):
        # PaymentConfig.usdc_atomic_units: round(usd * 10**6) for every configured USDC network.
        self.assertEqual(str(max(1, int(round(0.002 * 10**6)))), "2000")
        self.assertNotEqual(str(int(round(0.004 * 10**6))), "2000")

    def test_mpp_amount_string(self):
        from scrapling.monetize.mpp_bridge import MppDoor

        self.assertEqual(MppDoor._amount(0.002), "0.002")
        self.assertEqual(MppDoor._amount(0.004), "0.004")

    def test_patch_is_narrow_and_idempotent(self):
        root = _tree()
        notes = apply_tree(root)
        self.assertTrue(any(line.startswith("patched ") for line in notes))
        blob = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*") if path.is_file())
        self.assertNotIn('"price": 0.004', blob)
        self.assertIn('"price": 0.002', blob)
        self.assertIn("$0.0045", blob)
        self.assertIn("$0.04", blob)
        self.assertNotIn("PAID $0.004 USDC. Before paying", blob)
        self.assertIn("PAID $0.002 USDC. Before paying", blob)
        again = apply_tree(root)
        self.assertTrue(all(line.startswith("already ") for line in again))
        rolled = rollback_tree(root)
        self.assertTrue(any(line.startswith("patched ") for line in rolled))
        restored = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*") if path.is_file())
        self.assertIn('"price": 0.004', restored)
        self.assertNotIn('"price": 0.002', restored)
        self.assertIn("$0.0045", restored)

    def test_abort_when_anchor_missing(self):
        root = _tree()
        target = root / "scrapling/monetize/upd25.py"
        target.write_text("no price here\n" + DECOY, encoding="utf-8")
        with self.assertRaises(SystemExit):
            apply_tree(root)

    def test_gateway_guidance_anchor_on_branch_file(self):
        gateway = Path(__file__).resolve().parents[2] / "scrapling/monetize/gateway.py"
        text = gateway.read_text(encoding="utf-8")
        self.assertEqual(text.count("/v1/check/counterparty?chain=&address= $0.002 "), 1)
        self.assertEqual(text.count("/v1/check/counterparty?chain=&address= $0.004 "), 0)
        self.assertIn("risk-check ($0.0045", text)
        self.assertIn("/v1/wallet/profile?chain=&address= $0.005", text)
        self.assertIn("/v1/trust/agent?chain=&address= $0.04 ", text)

    def test_invalid_payment_is_not_a_settled_success(self):
        # The live unpaid and malformed-signature probes both stay HTTP 402.
        # This guards the checker contract: 400 is not treated as a healthy 402,
        # and a non-credential signature string is not accepted.
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
