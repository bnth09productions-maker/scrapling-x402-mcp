"""Registry metadata, tool documentation, and one free forwarded call."""

from __future__ import annotations

import json
import re
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import jsonschema

from scrapling_mcp import __version__
from scrapling_mcp.catalog import baseline_tools, documented_tools
from scrapling_mcp.server import serve

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_URL = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
PRICE = re.compile(r"\$\d+(?:\.\d+)?")


def _prices(text: str) -> list[str]:
    return PRICE.findall(text)


class DocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.baseline = {tool["name"]: tool for tool in baseline_tools()}
        cls.tools = documented_tools()
        cls.by_name = {tool["name"]: tool for tool in cls.tools}

    def test_version_matches_package_and_server_json(self) -> None:
        server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertEqual(server["version"], "0.6.2")
        self.assertEqual(__version__, "0.6.2")
        self.assertIn('version = "0.6.2"', pyproject)

    def test_server_json_matches_official_schema(self) -> None:
        server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
        with urllib.request.urlopen(SCHEMA_URL, timeout=30) as response:
            schema = json.load(response)
        jsonschema.validate(server, schema)
        icon = server["icons"][0]
        self.assertEqual(icon["src"], "https://scraplingdata.xyz/logo.png")
        self.assertEqual(icon["mimeType"], "image/png")
        self.assertEqual(icon["sizes"], ["400x400"])
        self.assertLessEqual(len(icon["src"]), 255)
        self.assertTrue(icon["src"].startswith("https://"))
        self.assertEqual(server["remotes"][0]["url"], "https://scraplingdata.xyz/mcp")
        self.assertLessEqual(len(server["description"]), 100)

    def test_every_tool_and_argument_is_documented(self) -> None:
        self.assertEqual(len(self.tools), 120)
        self.assertEqual(set(self.by_name), set(self.baseline))
        arg_count = 0
        for name, tool in self.by_name.items():
            original = self.baseline[name]
            self.assertGreaterEqual(len(tool["description"]), 40)
            self.assertIn("Returns ", tool["description"])
            for price in _prices(original["description"]):
                self.assertIn(price, tool["description"], name)
            for price in _prices(original["title"]):
                self.assertIn(price, tool["description"], name)
            schema = tool["inputSchema"]
            self.assertEqual(schema.get("required", []), original["required"])
            self.assertEqual(set(schema["properties"]), set(original["properties"]))
            for arg, spec in schema["properties"].items():
                arg_count += 1
                desc = spec.get("description") or ""
                self.assertGreaterEqual(len(desc), 40, f"{name}.{arg}")
                self.assertIn("Example:", desc, f"{name}.{arg}")
                original_spec = original["properties"][arg]
                for key in ("type", "enum", "default", "minimum", "maximum"):
                    self.assertEqual(spec.get(key), original_spec.get(key), f"{name}.{arg}.{key}")
        self.assertEqual(arg_count, 267)

    def test_logo_is_the_documented_png(self) -> None:
        request = urllib.request.Request(
            "https://scraplingdata.xyz/logo.png",
            headers={"user-agent": "scrapling-x402-mcp-test"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read(32)
            mime = response.headers.get("Content-Type", "")
        self.assertIn("image/png", mime)
        self.assertTrue(body.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(int.from_bytes(body[16:20], "big"), 400)
        self.assertEqual(int.from_bytes(body[20:24], "big"), 400)


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.port = 8765
        cls.thread = threading.Thread(
            target=serve,
            args=("127.0.0.1", cls.port),
            daemon=True,
        )
        cls.thread.start()
        for _ in range(50):
            try:
                cls._post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                }})
                break
            except Exception:
                time.sleep(0.05)
        else:
            raise RuntimeError("server did not start")

    @classmethod
    def _post(cls, payload: dict) -> dict:
        request = urllib.request.Request(
            f"http://127.0.0.1:{cls.port}/mcp",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "accept": "application/json, text/event-stream",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)

    def test_tools_list_has_new_descriptions(self) -> None:
        listed = self._post({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools = listed["result"]["tools"]
        self.assertEqual(len(tools), 120)
        init = self._post({"jsonrpc": "2.0", "id": 3, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        }})
        self.assertEqual(init["result"]["serverInfo"]["version"], "0.6.2")
        by_name = {tool["name"]: tool for tool in tools}
        self.assertIn("Returns a JSON catalog", by_name["free_feeds"]["description"])
        tier = by_name["scrape_url"]["inputSchema"]["properties"]["tier"]
        self.assertEqual(tier["enum"], ["auto", "cached_intel", "static_scrape", "stealth_browser"])
        self.assertIn("Allowed values:", tier["description"])
        self.assertIn("Default: auto", tier["description"])
        missing = [
            f"{tool['name']}.{arg}"
            for tool in tools
            for arg, spec in tool["inputSchema"]["properties"].items()
            if not spec.get("description")
        ]
        self.assertEqual(missing, [])

    def test_free_tool_call_still_works(self) -> None:
        result = self._post({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "free_country_btc_holdings", "arguments": {}},
        })
        self.assertNotIn("error", result)
        payload = result["result"]
        self.assertIs(payload.get("isError"), False)
        text = payload["content"][0]["text"]
        self.assertNotIn("sk_scrapling_", text)
        self.assertNotIn("sdp_", text)
        data = json.loads(text)
        self.assertTrue(data.get("free"))
        self.assertGreaterEqual(len(data.get("rows") or []), 1)
        # Keep the assertion local. Do not print the body.

    def test_get_is_not_the_mcp_method(self) -> None:
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}/mcp", method="GET")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(caught.exception.code, 405)
        self.assertIn("POST", caught.exception.headers.get("Allow", ""))


if __name__ == "__main__":
    unittest.main()
