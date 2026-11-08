"""Narrow redirects onto routes that already exist. No data and no payment decision."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse

# Missing prefix seen in traffic. Both targets already exist and stay paid.
ALIASES = {
    "/v1/market-pulse": "/v1/bundle/market-pulse",
    "/v1/defi-intel": "/v1/bundle/defi-intel",
}

# One trailing period only. Each path is a real route that our own prose ends with a period,
# or one of the two bundle routes above. Not a general "strip dots" rule.
TRAILING_DOT = (
    "/v1/bundle/market-pulse",
    "/v1/bundle/defi-intel",
    "/v1/pass/month",
    "/v1/lookup/yields",
    "/v1/perps/stocks",
    "/v1/free/ecom/stores",
    "/v1/quality",
    "/v1/intel/risk-check",
    "/v1/free/feeds",
    "/v1/free/datasets",
)


def redirect_target(path: str) -> str | None:
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
        return None
    if "\\" in path or "\x00" in path or "%" in path or "://" in path:
        return None
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts[1:]):
        return None
    if path in ALIASES:
        return ALIASES[path]
    if path.endswith(".") and not path.endswith(".."):
        bare = path[:-1]
        if bare in TRAILING_DOT and bare not in ALIASES:
            return bare
    return None


def location_for(path: str, query: bytes) -> str | None:
    target = redirect_target(path)
    if target is None:
        return None
    if b"\r" in query or b"\n" in query:
        return None
    if not query:
        return target
    return target + "?" + query.decode("latin-1")


def register_link_compat(app: FastAPI) -> None:
    async def handler(request: Request) -> RedirectResponse:
        loc = location_for(request.url.path, request.scope.get("query_string") or b"")
        if loc is None:
            raise HTTPException(404)
        return RedirectResponse(loc, status_code=307)

    paths = list(ALIASES) + [item + "." for item in TRAILING_DOT]
    for path in paths:
        app.add_api_route(path, handler, methods=["GET", "POST", "HEAD"], include_in_schema=False)
