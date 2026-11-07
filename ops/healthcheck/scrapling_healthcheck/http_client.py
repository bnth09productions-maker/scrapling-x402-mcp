"""Bounded HTTP GET/POST. No cookies, no credential headers."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Dict, Optional
from urllib.parse import urljoin, urlparse


@dataclass
class HttpResponse:
    status: int
    headers: Dict[str, str]
    body: bytes
    error: str
    final_url: str
    truncated: bool


class HttpClient:
    def __init__(self, timeout: float, max_bytes: int, user_agent: str) -> None:
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.user_agent = user_agent

    def request(self, method: str, url: str, body: Optional[bytes] = None,
                headers: Optional[Dict[str, str]] = None) -> HttpResponse:
        extra = {
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/html, text/plain, application/xml, */*",
            "X-Internal-Monitor": "1",
        }
        if headers:
            # Never forward authorization or payment material from config.
            for key, value in headers.items():
                if key.lower() in ("authorization", "cookie", "x-admin-token"):
                    continue
                extra[key] = value
        req = urllib.request.Request(url, data=body, headers=extra, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read(self.max_bytes + 1)
                hdrs = {k.lower(): v for k, v in resp.headers.items()}
                return HttpResponse(resp.status, hdrs, raw[: self.max_bytes], "", resp.geturl(), len(raw) > self.max_bytes)
        except urllib.error.HTTPError as exc:
            raw = exc.read(self.max_bytes + 1) if exc.fp else b""
            hdrs = {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
            return HttpResponse(exc.code, hdrs, raw[: self.max_bytes], "", exc.geturl(), len(raw) > self.max_bytes)
        except Exception as exc:
            return HttpResponse(0, {}, b"", type(exc).__name__, url, False)


def same_site(base: str, final: str) -> bool:
    a, b = urlparse(base), urlparse(final)
    return a.scheme == "https" and b.scheme == "https" and (b.hostname or "").lower().endswith(
        (a.hostname or "").lower()
    )


def join(base: str, path: str) -> str:
    return urljoin(base.rstrip("/") + "/", path.lstrip("/"))


def loads(body: bytes):
    return json.loads(body.decode("utf-8"))
