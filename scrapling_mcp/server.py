"""Streamable HTTP MCP server.

tools/list is the documented catalog. tools/call is forwarded to the existing
Scrapling endpoint, so prices, payment, and responses stay on that server.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from scrapling_mcp import __version__
from scrapling_mcp.catalog import documented_tools

UPSTREAM = os.environ.get("SCRAPLING_MCP_UPSTREAM", "https://scraplingdata.xyz/mcp")
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
SERVER_INSTRUCTIONS = (
    "Scrapling x402 Data Store. Free tools need no payment. Paid tools settle "
    "in USDC with x402: an unpaid call returns PaymentRequired, and the retry "
    "puts the signed payment in params._meta['x402/payment']. A prepaid "
    "sk_scrapling_... key or an sdp_... pass token can be passed as api_key. "
    "Failed lookups are not charged. Prices are unchanged in this build."
)


def _tools() -> list[dict[str, Any]]:
    return documented_tools()


def _protocol(requested: str | None) -> str:
    if requested in PROTOCOL_VERSIONS:
        return requested
    return "2025-06-18"


def _rpc_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _read_upstream(body: bytes) -> dict[str, Any]:
    request = Request(
        UPSTREAM,
        data=body,
        headers={
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
            "user-agent": "scrapling-x402-mcp/0.6.2",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"upstream HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"upstream unreachable: {exc.reason}") from exc
    text = raw.strip()
    if text.startswith("data:") or text.startswith("event:"):
        data_lines = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
        if not data_lines:
            raise RuntimeError("upstream event stream had no data")
        text = data_lines[-1]
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise RuntimeError("upstream response was not a JSON object")
    return parsed


def _forward_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    parsed = _read_upstream(json.dumps(payload).encode("utf-8"))
    if "error" in parsed:
        message = parsed["error"].get("message", "upstream error") if isinstance(parsed["error"], dict) else "upstream error"
        raise RuntimeError(message)
    result = parsed.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("upstream tools/call result was missing")
    return result


def handle_message(message: dict[str, Any]) -> dict[str, Any] | None:
    if message.get("jsonrpc") != "2.0" or "method" not in message:
        return _rpc_error(message.get("id"), -32600, "Invalid Request")
    method = message["method"]
    req_id = message.get("id")
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return _rpc_error(req_id, -32602, "Invalid params")
    if method.startswith("notifications/"):
        return None
    if method == "initialize":
        client = params.get("protocolVersion")
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": _protocol(client if isinstance(client, str) else None),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": "scrapling-x402-store",
                    "title": "Scrapling x402 Data Store",
                    "version": __version__,
                },
                "instructions": SERVER_INSTRUCTIONS,
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": _tools()}}
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        known = {tool["name"] for tool in _tools()}
        if not isinstance(name, str) or name not in known:
            return _rpc_error(req_id, -32602, "Unknown tool")
        if not isinstance(arguments, dict):
            return _rpc_error(req_id, -32602, "arguments must be an object")
        try:
            result = _forward_call(name, arguments)
        except RuntimeError as exc:
            return _rpc_error(req_id, -32000, str(exc)[:300])
        return {"jsonrpc": "2.0", "id": req_id, "result": result}
    return _rpc_error(req_id, -32601, "Method not found")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        # Request lines only. Bodies can carry api_key or payment payloads.
        super().log_message("%s", args[0] if args else fmt)

    def _send(self, status: int, body: bytes | None, content_type: str | None, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Allow", "POST, OPTIONS")
        if extra:
            for key, value in extra.items():
                self.send_header(key, value)
        if content_type and body is not None:
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
        elif body is not None:
            self.send_header("Content-Length", str(len(body)))
        else:
            self.send_header("Content-Length", "0")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204, None, None)

    def do_GET(self) -> None:  # noqa: N802
        self._send(405, b'{"error":"use POST"}', "application/json")

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path not in ("/mcp", "/"):
            self._send(404, b'{"error":"not found"}', "application/json")
            return
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length) if length else b""
        try:
            message = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = json.dumps(_rpc_error(None, -32700, "Parse error")).encode("utf-8")
            self._send(200, body, "application/json")
            return
        if isinstance(message, list):
            replies = [reply for item in message if isinstance(item, dict) and (reply := handle_message(item)) is not None]
            if not replies:
                self._send(202, None, None)
                return
            body = json.dumps(replies).encode("utf-8")
            self._send(200, body, "application/json")
            return
        if not isinstance(message, dict):
            body = json.dumps(_rpc_error(None, -32600, "Invalid Request")).encode("utf-8")
            self._send(200, body, "application/json")
            return
        reply = handle_message(message)
        if reply is None:
            self._send(202, None, None)
            return
        body = json.dumps(reply).encode("utf-8")
        self._send(200, body, "application/json")


def serve(host: str, port: int) -> None:
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"scrapling-x402-mcp {__version__} listening on http://{host}:{port}/mcp", flush=True)
    httpd.serve_forever()
