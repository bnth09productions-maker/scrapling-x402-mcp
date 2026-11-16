"""Offline tests. No network, no payment, no gateway process."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "files"))

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from link_compat import location_for, redirect_target
from mcp_method import method_decision, negotiated_version, protocol_error


def _client():
    sys.path.insert(0, str(ROOT / "files"))
    from link_compat import register_link_compat
    app = FastAPI()
    hits = {"n": 0}

    @app.get("/v1/bundle/market-pulse")
    @app.post("/v1/bundle/market-pulse")
    def pulse():
        hits["n"] += 1
        return JSONResponse({"priceUsd": 0.001}, status_code=402)

    @app.get("/v1/lookup/yields")
    def yields():
        hits["n"] += 1
        return JSONResponse({"priceUsd": 0.002}, status_code=402)

    register_link_compat(app)
    return TestClient(app), hits


class LinkTests(unittest.TestCase):
    def test_aliases_redirect_and_do_not_unlock_payment(self):
        client, hits = _client()
        for path, target in (
            ("/v1/market-pulse", "/v1/bundle/market-pulse"),
            ("/v1/defi-intel", "/v1/bundle/defi-intel"),
        ):
            resp = client.get(path + "?q=1", follow_redirects=False)
            self.assertEqual(resp.status_code, 307)
            self.assertEqual(resp.headers["location"], target + "?q=1")
            self.assertNotIn("evil", resp.headers["location"])
            posted = client.post(path, json={"x": 1}, follow_redirects=False)
            self.assertEqual(posted.status_code, 307)
            self.assertEqual(posted.headers["location"], target)
        self.assertEqual(hits["n"], 0)
        paid = client.get("/v1/bundle/market-pulse", follow_redirects=False)
        self.assertEqual(paid.status_code, 402)
        self.assertEqual(hits["n"], 1)

    def test_trailing_dot_allowlist_and_dotted_identifiers(self):
        self.assertEqual(redirect_target("/v1/lookup/yields."), "/v1/lookup/yields")
        self.assertIsNone(redirect_target("/v1/free/stock-perps.json"))
        self.assertIsNone(redirect_target("/v1/free/stock-perps.json."))
        self.assertIsNone(redirect_target("/v1/lookup/coin."))
        self.assertIsNone(redirect_target("/v1/../secret"))
        self.assertIsNone(redirect_target("/v1/./market-pulse"))
        self.assertIsNone(redirect_target("//evil.example/v1/market-pulse"))
        self.assertIsNone(redirect_target("/v1/market-pulse/%2e%2e/admin"))
        self.assertIsNone(location_for("/v1/lookup/yields.", b"a=1\r\nLocation:https://evil"))
        self.assertEqual(location_for("/v1/market-pulse", b"chain=ethereum&address=0xabc"),
                         "/v1/bundle/market-pulse?chain=ethereum&address=0xabc")
        client, hits = _client()
        resp = client.post("/v1/lookup/yields.?x=1", follow_redirects=False)
        self.assertEqual(resp.status_code, 307)
        self.assertEqual(resp.headers["location"], "/v1/lookup/yields?x=1")
        self.assertEqual(hits["n"], 0)
        self.assertEqual(client.get("/v1/free/stock-perps.json", follow_redirects=False).status_code, 404)

    def test_mcp_methods_protocol_and_oauth_shape(self):
        self.assertEqual(method_decision("OPTIONS"), (204, {"Allow": "POST, OPTIONS"}))
        self.assertEqual(method_decision("GET")[0], 405)
        self.assertEqual(method_decision("HEAD")[0], 405)
        self.assertEqual(method_decision("DELETE")[0], 405)
        self.assertIsNone(method_decision("POST"))
        self.assertNotEqual(method_decision("GET")[0], 200)
        self.assertIsNone(protocol_error(None))
        self.assertIsNone(protocol_error(""))
        self.assertIsNone(protocol_error("2025-03-26"))
        bad = protocol_error("1999-01-01")
        self.assertEqual(bad["jsonrpc"], "2.0")
        self.assertEqual(bad["error"]["code"], -32600)
        self.assertNotIn("token_endpoint", str(bad))
        self.assertEqual(negotiated_version("2025-06-18"), "2025-06-18")
        self.assertEqual(negotiated_version("nope"), "2025-11-25")
        oauth = (
            '{"error": "not_found", "oauth": False, '
            '"message": "This server does not use OAuth or API keys.", '
            '"x402_manifest": "https://scraplingdata.xyz/.well-known/x402"}'
        )
        self.assertIn('"oauth": False', oauth)
        self.assertNotIn("token_endpoint", oauth)
        self.assertNotIn("authorization_endpoint", oauth)
        self.assertNotIn("issuer", oauth)

    def test_apply_preflight_is_atomic(self):
        sys.path.insert(0, str(ROOT))
        from apply_link_compat import CutoverError, apply_tree
        root = Path(tempfile.mkdtemp())
        gw = root / "scrapling/monetize/gateway.py"
        mcp = root / "scrapling/monetize/mcp_server.py"
        gw.parent.mkdir(parents=True)
        gw.write_bytes(
            b"from .mcp_server import register_mcp_routes\n"
            b"    register_mcp_routes(app, _public_base_url)\n"
            b"price stays $0.004\n"
        )
        mcp.write_bytes(
            b"from scrapling.core._compat import json_dumps_bytes, json_loads\n"
            b"    async def mcp_endpoint(request: Request) -> Response:\n        try:\n"
            b"    async def mcp_no_stream() -> Response:\n"
            b"        # Stateless server: no server-initiated SSE stream and no sessions to delete.\n"
            b"        return Response(status_code=405, headers={\"Allow\": \"POST\"})\n"
        )
        before = {p: p.read_bytes() for p in (gw, mcp)}
        mcp.write_bytes(mcp.read_bytes().replace(b"mcp_no_stream", b"renamed", 1))
        before = {p: p.read_bytes() for p in (gw, mcp)}
        with self.assertRaises(CutoverError):
            apply_tree(root)
        self.assertEqual({p: p.read_bytes() for p in (gw, mcp)}, before)
        self.assertFalse((root / "scrapling/monetize/link_compat.py").exists())
        mcp.write_bytes(before[mcp].replace(b"renamed", b"mcp_no_stream"))
        # restore the broken file to the valid anchor text
        mcp.write_bytes(
            b"from scrapling.core._compat import json_dumps_bytes, json_loads\n"
            b"    async def mcp_endpoint(request: Request) -> Response:\n        try:\n"
            b"    async def mcp_no_stream() -> Response:\n"
            b"        # Stateless server: no server-initiated SSE stream and no sessions to delete.\n"
            b"        return Response(status_code=405, headers={\"Allow\": \"POST\"})\n"
        )
        notes = apply_tree(root)
        self.assertTrue(any(line.startswith("patched ") for line in notes))
        text = gw.read_text(encoding="utf-8")
        self.assertIn("register_link_compat(app)", text)
        self.assertIn("$0.004", text)
        self.assertNotIn("$0.002", text)
        mcp_text = mcp.read_text(encoding="utf-8")
        self.assertIn("protocol_error", mcp_text)
        self.assertIn('method_decision("OPTIONS")', mcp_text)
        self.assertIn('method_decision("GET")', mcp_text)
        self.assertNotIn('headers={"Allow": "POST"}', mcp_text)
        again = apply_tree(root)
        self.assertTrue(all(line.startswith("already ") for line in again))
        script = (ROOT / "apply_link_compat.py").read_text(encoding="utf-8")
        self.assertNotIn("0.002", script)


if __name__ == "__main__":
    unittest.main()
