"""OpenAPI for GET /v1/batch-scrape, matched to the handler that always returns 402.

The paid scrape is POST with a JSON body. GET takes no urls and does not scrape.
This module only describes that. It does not change prices or the route body.
"""
from __future__ import annotations

from typing import Any, Dict

GET_SUMMARY = "Batch scrape probe: GET always returns 402; scrape with POST"
GET_DESCRIPTION = (
    "GET /v1/batch-scrape accepts no query parameters and no JSON body and does not scrape. "
    "The handler always returns HTTP 402 Payment Required. The challenge tells the client to "
    "POST /v1/batch-scrape with JSON {\"urls\": [\"https://...\"]} (1 to 100 absolute URLs), "
    "optional tier (auto, cached_intel, static_scrape, stealth_browser), extract_rules, and use_cache. "
    "A payment attached to this GET is not turned into scraped pages. The paid call is POST."
)


def apply_batch_scrape_openapi(paths: Dict[str, Any]) -> None:
    """Document GET the way the route behaves. Leave the POST operation untouched."""
    item = paths.setdefault("/v1/batch-scrape", {})
    post = item.get("post")
    get = dict(item.get("get") or {})
    get["summary"] = GET_SUMMARY
    get["description"] = GET_DESCRIPTION
    get["parameters"] = []
    get.pop("requestBody", None)
    get["responses"] = {
        "402": {
            "description": (
                "Always returned. Body is the x402 challenge whose usage says to POST JSON urls. "
                "This GET is not a successful scrape."
            ),
            "content": {"application/json": {"schema": {"type": "object"}}},
        }
    }
    get["x-payment-info"] = {
        "payable": False,
        "runtime_status": 402,
        "note": "GET does not scrape. Pay POST /v1/batch-scrape with a JSON body.",
        "scrape": {
            "method": "POST",
            "path": "/v1/batch-scrape",
            "body": {"urls": ["https://example.com"], "tier": "static_scrape"},
        },
    }
    item["get"] = get
    if post is not None:
        item["post"] = post
