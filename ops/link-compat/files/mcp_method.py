"""Streamable HTTP method and protocol decisions for the stateless MCP endpoint.

GET is not an SSE stream. DELETE does not invent a session. OPTIONS advertises POST.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
ALLOW = "POST, OPTIONS"


def method_decision(method: str) -> Optional[tuple]:
    name = (method or "").upper()
    if name == "OPTIONS":
        return 204, {"Allow": ALLOW}
    if name in ("GET", "HEAD", "DELETE"):
        return 405, {"Allow": ALLOW}
    return None


def negotiated_version(requested: Any) -> str:
    if requested in SUPPORTED_PROTOCOL_VERSIONS:
        return str(requested)
    return SUPPORTED_PROTOCOL_VERSIONS[0]


def protocol_error(header: Optional[str]) -> Optional[Dict[str, Any]]:
    """Missing header is allowed. An unknown header value is a JSON-RPC error, not a session."""
    if header is None or str(header).strip() == "":
        return None
    if str(header).strip() in SUPPORTED_PROTOCOL_VERSIONS:
        return None
    return {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32600, "message": "Unsupported MCP-Protocol-Version"},
    }
