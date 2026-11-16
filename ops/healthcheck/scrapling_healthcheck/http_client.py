"""Bounded HTTP GET/POST. No cookies, no credential headers on redirects."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Dict, Optional
from urllib.parse import urljoin, urlparse

REDIRECTS = (301, 302, 303, 307, 308)
MAX_REDIRECTS = 3
_ALWAYS_DROP = {"authorization", "cookie", "x-admin-token", "proxy-authorization"}
_SECRET_PARTS = ("token", "secret", "password", "signature", "api-key", "apikey")


@dataclass
class HttpResponse:
    status: int
    headers: Dict[str, str]
    body: bytes
    error: str
    final_url: str
    truncated: bool


def _port(parts) -> Optional[int]:
    if parts.port:
        return parts.port
    if parts.scheme == "https":
        return 443
    if parts.scheme == "http":
        return 80
    return None


def same_site(base: str, final: str) -> bool:
    """True only for the same https host and port, or a dotted subdomain of it.

    `evilscraplingdata.xyz` is not a host of `scraplingdata.xyz`.
    """
    a, b = urlparse(base), urlparse(final)
    host_a = (a.hostname or "").lower().rstrip(".")
    host_b = (b.hostname or "").lower().rstrip(".")
    if a.scheme != "https" or b.scheme != "https" or not host_a or not host_b:
        return False
    if a.username or a.password or b.username or b.password:
        return False
    if _port(a) != _port(b):
        return False
    if host_b == host_a:
        return True
    return host_b.endswith("." + host_a)


def secret_header(name: str) -> bool:
    lowered = name.lower()
    if lowered in _ALWAYS_DROP:
        return True
    return any(part in lowered for part in _SECRET_PARTS)


def public_headers(user_agent: str) -> Dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Accept": "application/json, text/html, text/plain, application/xml, */*",
        "X-Internal-Monitor": "1",
    }


def join(base: str, path: str) -> str:
    return urljoin(base.rstrip("/") + "/", path.lstrip("/"))


def loads(body: bytes):
    return json.loads(body.decode("utf-8"))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


class HttpClient:
    def __init__(self, timeout: float, max_bytes: int, user_agent: str, opener=None) -> None:
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.user_agent = user_agent
        self.opener = opener or urllib.request.build_opener(_NoRedirect()).open

    def request(self, method: str, url: str, body: Optional[bytes] = None,
                headers: Optional[Dict[str, str]] = None) -> HttpResponse:
        origin = url
        hop_headers = public_headers(self.user_agent)
        if headers:
            for key, value in headers.items():
                if key.lower() in _ALWAYS_DROP:
                    continue
                hop_headers[key] = value
        current = url
        hop_method = method
        hop_body = body
        for hop in range(MAX_REDIRECTS + 1):
            req = urllib.request.Request(current, data=hop_body, headers=hop_headers, method=hop_method)
            try:
                with self.opener(req, timeout=self.timeout) as resp:
                    raw = resp.read(self.max_bytes + 1)
                    hdrs = {k.lower(): v for k, v in resp.headers.items()}
                    final = resp.geturl()
                    if not same_site(origin, final):
                        return HttpResponse(0, hdrs, b"", "left configured host", final, False)
                    return HttpResponse(
                        resp.status, hdrs, raw[: self.max_bytes], "", final, len(raw) > self.max_bytes,
                    )
            except urllib.error.HTTPError as exc:
                if exc.code in REDIRECTS and hop < MAX_REDIRECTS:
                    location = ""
                    if exc.headers:
                        location = exc.headers.get("Location") or exc.headers.get("location") or ""
                    nxt = urljoin(current, location) if location else ""
                    if exc.fp:
                        exc.fp.close()
                    if not nxt or not same_site(origin, nxt):
                        return HttpResponse(0, {}, b"", "redirect rejected", current, False)
                    hop_headers = public_headers(self.user_agent)
                    if exc.code in (301, 302, 303):
                        hop_method = "GET"
                        hop_body = None
                    current = nxt
                    continue
                raw = exc.read(self.max_bytes + 1) if exc.fp else b""
                hdrs = {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
                final = exc.geturl() or current
                return HttpResponse(exc.code, hdrs, raw[: self.max_bytes], "", final, len(raw) > self.max_bytes)
            except Exception as exc:
                return HttpResponse(0, {}, b"", type(exc).__name__, current, False)
        return HttpResponse(0, {}, b"", "redirect limit", current, False)
