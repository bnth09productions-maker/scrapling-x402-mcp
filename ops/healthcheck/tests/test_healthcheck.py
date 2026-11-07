"""Offline health-checker tests. No network and no paid calls."""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from urllib.parse import urlparse

NOW = datetime(2026, 11, 7, 23, 0, 30, tzinfo=timezone.utc).timestamp()

from scrapling_healthcheck.alerts import post_webhook, write_outbox
from scrapling_healthcheck.checks import Runner
from scrapling_healthcheck.engine import execute, load_config
from scrapling_healthcheck.http_client import HttpResponse
from scrapling_healthcheck.state_store import RunLock


def _b64(obj) -> str:
    raw = json.dumps(obj, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _mpp(path: str) -> str:
    scope = _b64({"_mppx_scope": {"resource": path, "http_method": "GET"}})
    charge = f'Payment intent="charge", request="{scope}"'
    session = f'Payment intent="session", request="{scope}"'
    return charge + ", " + session


class FakeClient:
    def __init__(self, routes):
        self.routes = routes

    def request(self, method, url, body=None, headers=None):
        path = urlparse(url).path
        if urlparse(url).query:
            path = path + "?" + urlparse(url).query
        key = (method, path)
        if key not in self.routes:
            return HttpResponse(0, {}, b"", "missing", url, False)
        status, hdrs, payload = self.routes[key]
        if isinstance(payload, dict) and body:
            try:
                sent = json.loads(body)
                payload = dict(payload)
                if "id" in payload and isinstance(sent, dict):
                    payload["id"] = sent.get("id")
            except Exception:
                pass
        if isinstance(payload, (dict, list)):
            raw = json.dumps(payload).encode()
        elif isinstance(payload, str):
            raw = payload.encode()
        else:
            raw = payload
        return HttpResponse(status, {k.lower(): v for k, v in hdrs.items()}, raw, "", url, False)


def healthy(now: float):
    path = "/v1/bundle/market-pulse"
    paid = {"priceUsd": 0.001, "accepts": [{"scheme": "exact"}], "error": "Payment Required"}
    free_time = "2026-11-07T23:00:00Z"
    return {
        ("GET", "/"): (200, {"content-type": "text/html"}, "<html>" + ("scrapling " * 80) + "</html>"),
        ("GET", "/v1/free/binance-flows"): (200, {"content-type": "application/json"},
                                            {"free": True, "as_of": free_time, "partial": False, "chains_partial": []}),
        ("GET", "/v1/free/stock-perps.json"): (200, {"content-type": "application/json"},
                                               {"free": True, "as_of": free_time, "count": 5, "markets": [{"symbol": "TSLA"}]}),
        ("GET", "/v1/free/wallet-label"): (200, {"content-type": "application/json"},
                                           {"free": True, "as_of": free_time, "match": True, "category": "special",
                                            "last_confirmed": None}),
        ("GET", "/v1/free/feeds"): (200, {"content-type": "application/json"}, {"free": True, "feeds": [{"id": "a"}]}),
        ("GET", "/v1/free/crypto/top10"): (200, {"content-type": "application/json"}, {"free": True}),
        ("GET", "/v1/free/defi/top-yields"): (200, {"content-type": "application/json"}, {"free": True}),
        ("GET", path): (402, {"content-type": "application/json", "www-authenticate": _mpp(path)}, paid),
        ("GET", "/docs"): (200, {"content-type": "text/html"}, f"see {path} ($0.001) in the catalog"),
        ("GET", "/llms.txt"): (200, {"content-type": "text/plain"}, f"GET https://scraplingdata.xyz{path} | $0.001 |"),
        ("GET", "/openapi.json"): (200, {"content-type": "application/json"}, {
            "openapi": "3.1.0",
            "info": {"x-guidance": f"{path} $0.001"},
            "paths": {path: {"get": {"x-payment-info": {"price": {"amount": "0.001"}}}}},
        }),
        ("GET", "/.well-known/x402"): (200, {"content-type": "application/json"}, {
            "x402Version": 2, "resources": [{"url": "https://scraplingdata.xyz" + path, "priceUsd": 0.001}],
        }),
        ("POST", "/mcp"): (200, {"content-type": "application/json"}, {
            "jsonrpc": "2.0", "id": 1,
            "result": {"protocolVersion": "2025-03-26", "serverInfo": {"name": "scrapling-x402-store"},
                       "tools": [{"name": "list_datasets"}]},
        }),
        ("GET", "/robots.txt"): (200, {"content-type": "text/plain"}, "User-agent: *\nSitemap: https://scraplingdata.xyz/sitemap.xml\n"),
        ("GET", "/sitemap.xml"): (200, {"content-type": "application/xml"}, "<urlset><loc>https://scraplingdata.xyz/</loc></urlset>"),
        ("GET", "/hacks/data"): (200, {"content-type": "application/json"},
                                 {"generated_at": "2026-11-07T23:00:00Z", "tracker_status": 200}),
    }, now


def config_for(root: str):
    with open(os.path.join(os.path.dirname(__file__), "..", "config", "healthcheck.json"), encoding="utf-8") as handle:
        cfg = json.load(handle)
    cfg["state_dir"] = os.path.join(root, "state")
    cfg["report_dir"] = os.path.join(root, "reports")
    cfg["log_dir"] = os.path.join(root, "logs")
    cfg["outbox_dir"] = os.path.join(root, "outbox")
    return cfg


class HealthTests(unittest.TestCase):
    def test_healthy_run_and_dedupe_and_recovery(self):
        root = tempfile.mkdtemp()
        routes, now = healthy(NOW)
        # wallet path includes the query string in the default config
        routes[("GET", "/v1/free/wallet-label?address=0x0000000000000000000000000000000000000000")] = routes.pop(
            ("GET", "/v1/free/wallet-label")
        )
        for extra, usd in (
            ("/v1/bundle/defi-intel", 0.001),
            ("/v1/lookup/address-poisoning", 0.0009),
            ("/v1/check/counterparty", 0.004),
            ("/v1/perps/stocks", 0.003),
            ("/v1/lookup/binance-flows", 0.02),
            ("/v1/lookup/binance-flows/report", 0.25),
            ("/v1/pass/day", 2.0),
        ):
            routes[("GET", extra)] = (402, {"content-type": "application/json", "www-authenticate": _mpp(extra)},
                                      {"priceUsd": usd, "accepts": [{"scheme": "exact"}]})
        cfg = config_for(root)
        code = execute(cfg, root, client=FakeClient(routes), now=now)
        self.assertEqual(code, 0)
        outbox = os.path.join(root, "outbox", "events.jsonl")
        self.assertFalse(os.path.exists(outbox))
        code = execute(cfg, root, client=FakeClient(routes), now=now + 10)
        self.assertEqual(code, 0)
        routes[("GET", "/docs")] = (200, {"content-type": "text/html"}, "catalog without the pulse price")
        code = execute(cfg, root, client=FakeClient(routes), now=now + 20)
        self.assertEqual(code, 1)
        with open(outbox, encoding="utf-8") as handle:
            first = handle.read().strip().splitlines()
        self.assertEqual(len(first), 1)
        self.assertIn("failure", first[0])
        code = execute(cfg, root, client=FakeClient(routes), now=now + 30)
        self.assertEqual(code, 1)
        with open(outbox, encoding="utf-8") as handle:
            second = handle.read().strip().splitlines()
        self.assertEqual(len(second), 1)
        routes[("GET", "/docs")] = (200, {"content-type": "text/html"}, "see /v1/bundle/market-pulse ($0.001) in the catalog")
        code = execute(cfg, root, client=FakeClient(routes), now=now + 40)
        self.assertEqual(code, 0)
        with open(outbox, encoding="utf-8") as handle:
            third = handle.read().strip().splitlines()
        self.assertEqual(len(third), 2)
        self.assertIn("recovery", third[1])

    def test_wrong_body_wrong_price_400_and_429(self):
        routes, now = healthy(NOW)
        routes[("GET", "/v1/free/feeds")] = (200, {"content-type": "application/json"}, {"free": True, "feeds": []})
        routes[("GET", "/v1/bundle/market-pulse")] = (
            402, {"www-authenticate": _mpp("/v1/bundle/market-pulse")},
            {"priceUsd": 0.005, "accepts": [{"scheme": "exact"}]},
        )
        routes[("GET", "/v1/pass/day")] = (400, {}, {"detail": "bad"})
        routes[("GET", "/v1/free/crypto/top10")] = (429, {}, {"detail": "slow"})
        cfg = {"base_url": "https://scraplingdata.xyz", "free": [
            {"id": "feeds", "path": "/v1/free/feeds", "require_list": "feeds"},
            {"id": "top", "path": "/v1/free/crypto/top10"},
        ], "paid_402": [
            {"id": "market-pulse", "path": "/v1/bundle/market-pulse", "usd": 0.001},
            {"id": "pass-day", "path": "/v1/pass/day", "usd": 2.0},
        ], "bad_signature": {"id": "market-pulse", "path": "/v1/bundle/market-pulse", "usd": 0.001},
               "mpp": {"path": "/v1/bundle/market-pulse", "intents": ["charge", "session"]},
               "price_consistency": {"path": "/v1/bundle/market-pulse", "usd": 0.001},
               "hacks_max_age_seconds": 10**9, "local": {}}
        # price consistency and other default routes are absent: those checks fail closed
        results = {item.check_id: item for item in Runner(cfg, FakeClient(routes), now).run()}
        self.assertEqual(results["free:feeds"].status, "fail")
        self.assertEqual(results["paid:market-pulse"].status, "fail")
        self.assertEqual(results["paid:market-pulse"].details["code"], "price")
        self.assertEqual(results["paid:pass-day"].details["code"], "status-400")
        self.assertEqual(results["free:top"].status, "fail")
        self.assertNotEqual(results["paid:pass-day"].status, "pass")

    def test_local_evidence_missing_stale_and_known_stall(self):
        now = 1_800_000_000.0
        root = tempfile.mkdtemp()
        missing = Runner({"base_url": "https://scraplingdata.xyz", "local": {
            "sync_evidence": {"path": os.path.join(root, "nope.json"), "fresh_seconds": 10, "known_gap_seconds": 100},
            "demand_radar": {"path": os.path.join(root, "nope.db")},
        }}, FakeClient({}), now).run()
        watched_missing = {item.check_id: item.status for item in missing}
        self.assertEqual(watched_missing["local:sync"], "missing")
        self.assertEqual(watched_missing["local:demand_radar"], "missing")
        sync = os.path.join(root, "sync.json")
        with open(sync, "w", encoding="utf-8") as handle:
            json.dump({"last_ok": "2026-11-07T00:00:00Z"}, handle)
        db = os.path.join(root, "radar.db")
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE demand (id INTEGER PRIMARY KEY, ts REAL)")
        con.execute("INSERT INTO demand (ts) VALUES (?)", (now - 10_000,))
        con.commit()
        con.close()
        watched = {item.check_id: item for item in Runner({"base_url": "https://scraplingdata.xyz", "local": {
            "sync_evidence": {"path": sync, "fresh_seconds": 60, "known_gap_seconds": 10**9},
            "demand_radar": {"path": db, "fresh_seconds": 1000, "known_stall_max_age_seconds": 20000},
        }}, FakeClient({}), now).run()}
        self.assertEqual(watched["local:sync"].status, "watch")
        self.assertEqual(watched["local:demand_radar"].status, "watch")
        worsened = {item.check_id: item for item in Runner({"base_url": "https://scraplingdata.xyz", "local": {
            "demand_radar": {"path": db, "fresh_seconds": 1000, "known_stall_max_age_seconds": 5000},
        }}, FakeClient({}), now).run()}
        self.assertEqual(worsened["local:demand_radar"].status, "fail")
        self.assertEqual(worsened["local:demand_radar"].details["code"], "worsened")

    def test_request_log_ignores_wallet_columns_and_flags_5xx(self):
        now = 1_800_000_000.0
        db = os.path.join(tempfile.mkdtemp(), "usage.db")
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE request_log (id INTEGER PRIMARY KEY, ts REAL, status INTEGER, payer_wallet TEXT, ip_masked TEXT)")
        con.execute("INSERT INTO request_log (ts, status, payer_wallet, ip_masked) VALUES (?,?,?,?)",
                    (now - 10, 200, "0x" + "ab" * 20, "203.0.113.8"))
        con.execute("INSERT INTO request_log (ts, status, payer_wallet, ip_masked) VALUES (?,?,?,?)",
                    (now - 5, 500, "0x" + "cd" * 20, "203.0.113.9"))
        con.commit()
        con.close()
        result = Runner({"base_url": "https://scraplingdata.xyz", "local": {
            "request_log": {"path": db, "max_age_seconds": 100, "max_5xx_24h": 0},
        }}, FakeClient({}), now).run()
        row = next(item for item in result if item.check_id == "local:request_log")
        self.assertEqual(row.status, "fail")
        blob = json.dumps(row.details)
        self.assertNotIn("0xabab", blob)
        self.assertNotIn("203.0.113", blob)

    def test_mcp_schema_and_corrupt_state_and_lock(self):
        routes, now = healthy(NOW)
        routes[("POST", "/mcp")] = (200, {}, {"jsonrpc": "2.0", "id": 1, "result": {}})
        cfg = {"base_url": "https://scraplingdata.xyz", "local": {}, "hacks_max_age_seconds": 10**9,
               "free": [], "paid_402": [], "price_consistency": {}, "mpp": {"intents": []}}
        # empty mpp intents still compares; use the real mcp failure only
        results = {item.check_id: item for item in Runner({
            "base_url": "https://scraplingdata.xyz", "local": {},
        }, FakeClient(routes), now).run()}
        self.assertEqual(results["mcp:initialize"].status, "fail")
        self.assertEqual(results["mcp:tools/list"].status, "fail")
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "state"))
        with open(os.path.join(root, "state", "state.json"), "w", encoding="utf-8") as handle:
            handle.write("{")
        full, stamp = healthy(now)
        full[("GET", "/v1/free/wallet-label?address=0x0000000000000000000000000000000000000000")] = full.pop(
            ("GET", "/v1/free/wallet-label")
        )
        for extra, usd in (
            ("/v1/bundle/defi-intel", 0.001), ("/v1/lookup/address-poisoning", 0.0009),
            ("/v1/check/counterparty", 0.004), ("/v1/perps/stocks", 0.003),
            ("/v1/lookup/binance-flows", 0.02), ("/v1/lookup/binance-flows/report", 0.25),
            ("/v1/pass/day", 2.0),
        ):
            full[("GET", extra)] = (402, {"www-authenticate": _mpp(extra)}, {"priceUsd": usd, "accepts": [{}]})
        cfg = config_for(root)
        code = execute(cfg, root, client=FakeClient(full), now=stamp)
        self.assertEqual(code, 0)
        with open(os.path.join(root, "outbox", "events.jsonl"), encoding="utf-8") as handle:
            self.assertIn("corrupt", handle.read())
        lock = RunLock(os.path.join(root, "state", "held.lock"), stale_seconds=100)
        self.assertTrue(lock.acquire())
        other = RunLock(os.path.join(root, "state", "held.lock"), stale_seconds=100)
        self.assertFalse(other.acquire())
        lock.release()

    def test_banned_config_and_webhook_stays_local(self):
        with self.assertRaises(ValueError):
            load_config_obj = {"base_url": "https://scraplingdata.xyz", "free": [{"path": "/v1/coingecko"}]}
            from scrapling_healthcheck.checks import reject_banned_config
            reject_banned_config(load_config_obj)
        os.environ.pop("SCRAPLING_HEALTH_WEBHOOK_URL", None)
        path = os.path.join(tempfile.mkdtemp(), "events.jsonl")
        write_outbox(path, [{"id": "a", "kind": "failure", "check_id": "x", "summary": "down", "at": "t"}])
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("not_configured", text)
        self.assertIn("no external chat", text)
        self.assertEqual(post_webhook("http://example.com/hook", {"id": "a", "kind": "failure", "check_id": "x", "summary": "s", "at": "t"}), "skipped")

    def test_example_config_loads(self):
        path = os.path.join(os.path.dirname(__file__), "..", "config", "healthcheck.json")
        cfg = load_config(path)
        self.assertEqual(cfg["paid_402"][0]["usd"], 0.001)
        self.assertIsNone(cfg["local"]["demand_radar"]["path"])


if __name__ == "__main__":
    unittest.main()
