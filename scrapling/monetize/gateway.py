"""
Plug-and-Play x402 AI Agent & Human Data Buyer Gateway (FastAPI / Starlette ASGI).

Exposes:
- `GET /`: Interactive Web3 + Enterprise Data Storefront & O: Drive Autopilot Control Center
- `GET /hacks`: Hack Money Tracker (human page; totals, alerts, and cleared/retired rows)
- `GET /hacks/data`: Cached JSON for that page. Built in-process from the hack-tracker and tombstone functions, not via the free HTTP routes, so it does not use the 20/day free cap. About 60 requests a minute per IP.
- `GET /.well-known/x402.json`: Machine-readable x402 V2 payment discovery manifest
- `GET /.well-known/ai-plugin.json`: Autonomous AI Agent plugin manifest
- `GET /llms.txt`: LLM-optimized service & pricing directory
- `POST /v1/scrape` & `GET /v1/scrape`: x402-paywalled SmartScraper endpoint
- `POST /v1/batch-scrape`: Multi-URL batch extraction endpoint
- `GET /v1/datasets`: Curated dataset marketplace catalog with free sample previews
- `POST /v1/datasets/{dataset_id}/buy`: x402 & API-key gated full dataset download (JSON/JSONL/CSV/MD)
- `POST /v1/datasets/publish`: Publish new scraped datasets to the marketplace
- `POST /v1/harvest`: Run the Proactive Harvester + O: Drive file scanner
- `GET /v1/autopilot/status`: Inspect O: Drive Vault files and 24/7 Autopilot targets
- `POST /v1/autopilot/add-target`: Add a new website to the 24/7 Proactive O: Drive Scraper
- `POST /v1/autopilot/scan-odrive`: Scan `O:\\\\Scrapling_Collected_Data\\\\Drop_Files_To_Sell_Here` and list files for sale
- `POST /v1/credits/create-key`: Mint prepaid `sk_scrapling_...` API keys
- `POST /v1/credits/deposit`: Top up API key balance via on-chain transfer to `0x72C80C01fD89Bac74f47cf604b61ACd119c9BCdb`
- `GET /v1/credits/balance`: Check API key balance & usage stats
- `GET /v1/revenue`: Owner cash-flow & cache-margin telemetry
- `GET /admin/usage`: Owner daily request totals (header X-Admin-Token; 404 when ADMIN_TOKEN is unset; not in OpenAPI)
"""

import asyncio
import hmac
import inspect
import json
from os import environ
from pathlib import Path
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from scrapling.core._compat import json_dumps_bytes, json_loads

from base64 import b64decode

from .config import (
    DEFAULT_RECEIVING_ADDRESS,
    SUPPORTED_EVM_NETWORKS,
    PaymentConfig,
)
from .facilitator import EXACT_NETWORK, CdpFacilitator, FacilitatorError, is_exact_evm_payload
from .facilitator import SOLANA_FEE_PAYER, SOLANA_MAINNET, SOLANA_PAY_TO, SOLANA_USDC_MINT

def _paid_lookups_with_quality(base, lookups, cfg):
    try:
        from .quality import quality_block as _qb
    except Exception:
        _qb = lambda _k: None  # noqa: E731
    out = []
    for k, v in lookups.items():
        row = {
            "url": f"{base}{v['path']}",
            "example": f"{base}{v['example_path']}",
            "priceUsd": v["price"],
            "usdcAtomicUnits": cfg.usdc_atomic_units(v["price"]),
            "description": v["desc"],
            "networks": ["eip155:8453", "eip155:137", "eip155:42161", SOLANA_MAINNET],
        }
        q = _qb(k)
        if q:
            row["quality"] = q
        out.append(row)
    return out

from .agent_api import FREE_ENDPOINTS, LOOKUPS, build_llms_txt, openapi_mark, paid_lookup_urls, register_agent_api
from .harvester import AutonomousHarvester
from .dataset_listings import LISTINGS as DATASET_LISTINGS, listing_path, openapi_mark_datasets, paid_dataset_urls, register_dataset_listings
from .ledger import RevenueLedger
from .marketplace import DatasetMarketplace
from .mcp_server import register_mcp_routes
from .sales_engine import SalesOutreachEngine
from .smart_scraper import SmartScraper
from .x402 import X402PaymentVerifier
from .mpp_bridge import MppDoor, TEMPO_USDC
from . import trial_codes as _trial
from . import passes as _passes  # upd39 catalog passes


class ScrapeRequestBody(BaseModel):
    url: str = Field(..., description="Target URL to scrape and structure.")
    tier: str = Field(
        default="auto",
        description="Scraping tier: 'auto', 'cached_intel', 'static_scrape', or 'stealth_browser'.",
    )
    css_selector: Optional[str] = Field(default=None, description="Optional CSS selector to scope extraction.")
    xpath_selector: Optional[str] = Field(default=None, description="Optional XPath selector to scope extraction.")
    extract_rules: Optional[Dict[str, str]] = Field(
        default=None,
        description="Optional field map of `{field_name: css_or_xpath_selector}` for structured JSON output.",
    )
    use_cache: bool = Field(default=True, description="Serve from intelligence cache when available.")
    adaptive: bool = Field(default=False, description="Enable Scrapling adaptive element relocation.")
    html_override: Optional[str] = Field(
        default=None, description="Optional raw HTML to parse/structure directly (useful for testing/offline pipelines)."
    )


class BatchScrapeRequestBody(BaseModel):
    urls: List[str] = Field(..., description="List of URLs to scrape.")
    tier: str = Field(default="auto", description="Scraping tier.")
    extract_rules: Optional[Dict[str, str]] = Field(default=None, description="Structured field extraction map.")
    use_cache: bool = Field(default=True, description="Use high-margin intelligence cache.")
    publish_as_dataset: Optional[str] = Field(
        default=None, description="Optional dataset title to automatically list results in the marketplace."
    )
    dataset_price_usd: float = Field(default=4.99, description="Price if published to the marketplace.")


class CreateKeyRequestBody(BaseModel):
    owner_id: str = Field(default="0xAutonomousAgent", description="Wallet address, agent ID, or buyer email.")
    buyer_type: str = Field(default="ai_agent", description="'ai_agent' or 'human_buyer'.")
    tx_hash: Optional[str] = Field(
        default=None,
        description="On-chain transaction hash paying `0x72C80C01fD89Bac74f47cf604b61ACd119c9BCdb`.",
    )
    deposit_usd: float = Field(default=0.0, description="Deposited USD amount (or 0 to claim trial credit if enabled).")
    network: str = Field(default="eip155:8453", description="CAIP-2 network ID (e.g. eip155:8453 for Base).")


class TopupKeyRequestBody(BaseModel):
    api_key: str = Field(..., description="The `sk_scrapling_...` API key to credit.")
    amount_usd: float = Field(..., gt=0, description="Amount in USD transferred to owner wallet.")
    tx_hash: str = Field(..., description="On-chain transaction hash.")
    network: str = Field(default="eip155:8453", description="CAIP-2 network ID.")
    payer_address: str = Field(default="0xBuyerWallet", description="Sender wallet address.")


class PublishDatasetRequestBody(BaseModel):
    dataset_id: str = Field(..., description="Unique dataset slug (e.g. 'ds_crypto_prices').")
    title: str = Field(..., description="Human-readable dataset title.")
    category: str = Field(default="Web Intelligence", description="Dataset category.")
    description: str = Field(..., description="Detailed description of dataset contents.")
    price_usd: float = Field(..., gt=0, description="Price in USD to unlock full dataset.")
    records: List[Dict[str, Any]] = Field(..., description="List of structured records.")
    quality_score: int = Field(default=95, description="Quality score (0-100).")


class AddAutopilotTargetBody(BaseModel):
    url: str = Field(..., description="Website URL to proactively scrape on autopilot.")
    title: str = Field(..., description="Name of the dataset to create and sell.")
    category: str = Field(default="Crypto & E-Commerce Custom Feed", description="Dataset category.")
    price_usd: float = Field(default=4.99, gt=0, description="Price in USD for buyers to unlock.")
    css_selector: Optional[str] = Field(default=None, description="Optional CSS selector.")


class IngestBrowserPageBody(BaseModel):
    url: str = Field(..., description="URL of the webpage visited in the browser.")
    html_content: str = Field(..., description="Live rendered HTML captured from the browser tab.")
    title: Optional[str] = Field(default=None, description="Page title.")
    price_usd: float = Field(default=4.99, gt=0, description="Price in USD to list the dataset for in inventory.")
    auto_group_by_domain: bool = Field(
        default=True, description="Group & accumulate all pages visited on the same domain into one growing dataset."
    )



def _cdp_desc(text, limit=500):
    """CDP facilitator rejects a paymentPayload whose resource.description is over 500 chars."""
    s = str(text or "")
    if len(s) <= limit:
        return s
    cut = s[:limit]
    i = cut.rfind(". ")
    if i >= limit // 2:
        return cut[:i + 1]
    j = cut.rfind(" ")
    return cut[:j if j > 0 else limit].rstrip(" ,;:")

def require_admin(request: Request) -> None:
    """Owner-only guard. Fails closed when `ADMIN_TOKEN` is unset.

    Accepts `Authorization: Bearer <token>` or `X-Admin-Token: <token>`.
    """
    expected = environ.get("ADMIN_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=503, detail="Owner endpoints disabled: set the ADMIN_TOKEN environment variable.")
    supplied = request.headers.get("x-admin-token", "")
    if not supplied:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
    if not supplied or not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Owner admin token required.")


MAX_BATCH_URLS = 100
_TIER_DESC = {
    "cached_intel": "Recently cached copy of a page as Markdown, JSON-LD and tables; also used for single data lookups. Under 100 ms typical.",
    "static_scrape": "Live HTTP fetch of a page, returned as Markdown, JSON-LD, tables and links. About 1-3 s typical.",
    "stealth_browser": "Headless-browser fetch for JavaScript-heavy or bot-protected pages, returned as Markdown, JSON-LD and tables. About 5-15 s typical.",
}
SERVICE_NAME = "Scrapling x402 Data Store"
LOGO_PATH = Path(__file__).with_name("scrapling-logo.png")
_PAYMENT_HEADERS = ("payment-signature", "x-payment", "x-tx-hash", "x-api-key")


def _public_base_url(request: Request) -> str:
    """Absolute public origin, e.g. https://store.example.com (env SCRAPLING_PUBLIC_URL wins)."""
    env_url = environ.get("SCRAPLING_PUBLIC_URL", "").strip().rstrip("/")
    if env_url:
        return env_url
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme).split(",")[0].strip()
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc).split(",")[0].strip()
    return f"{proto}://{host}"


def _has_payment_attempt(request: Request) -> bool:
    if any(request.headers.get(h) for h in _PAYMENT_HEADERS):
        return True
    return request.headers.get("authorization", "").lower().startswith("bearer ")


_BAZAAR_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"type": {"type": "string"}, "example": {"type": "object"}},
    "required": ["type"],
}


_FREE_PREVIEW_BY_PREFIX = [
    ("/v1/lookup/hack-tracker", "/v1/free/hack-tracker"),
    ("/v1/lookup/coin", "/v1/free/crypto/top10"),
    ("/v1/alerts/price-moves", "/v1/free/crypto/top10"),
    ("/v1/datasets/coingecko-markets", "/v1/free/crypto/top10"),
    ("/v1/datasets/market-heatmap", "/v1/free/crypto/top10"),
    ("/v1/datasets/volume-leaders", "/v1/free/crypto/top10"),
    ("/v1/lookup/yields", "/v1/free/defi/top-yields"),
    ("/v1/lookup/pool", "/v1/free/defi/top-yields"),
    ("/v1/lookup/protocol", "/v1/free/pulse.json"),
    ("/v1/bundle/", "/v1/free/pulse.json"),
    ("/v1/whales/", "/v1/free/pulse.json"),
    ("/v1/signals/", "/v1/free/pulse.json"),
    ("/v1/regime/", "/v1/free/regime"),
    ("/v1/yields/", "/v1/free/staking-yields"),
    ("/v1/perps/", "/v1/free/funding"),
    ("/v1/meme/", "/v1/free/meme-trending"),
    ("/v1/token/", "/v1/free/meme-trending"),
    ("/v1/lookup/token-risk", "/v1/free/meme-trending"),
    ("/v1/stocks/", "/v1/free/stocks"),
    ("/v1/ecom/", "/v1/free/ecom"),
    ("/v1/datasets/live_", "/v1/datasets/{id}/preview"),
    ("/v1/datasets/", "/v1/free/datasets"),
]


def _free_preview_for(path: str) -> Optional[str]:
    for prefix, free in _FREE_PREVIEW_BY_PREFIX:
        if path.startswith(prefix):
            if free == "/v1/datasets/{id}/preview":
                parts = path.split("/")
                if len(parts) >= 4:
                    return f"/v1/datasets/{parts[3]}/preview"
            return free
    return None


def _buyer_guide(path: str, base_url: str, bazaar: Optional[Dict[str, Any]], price_usd: float) -> Dict[str, Any]:
    """Short 'how to actually buy this' block added to every 402 body."""
    from urllib.parse import urlencode
    g: Dict[str, Any] = {"price_usd": round(price_usd, 6), "charged_only_on_success": True}
    try:
        inp = ((bazaar or {}).get("info") or {}).get("input") or {}
        if inp.get("method") in ("POST", "PUT", "PATCH"):
            g["example_request"] = {"method": inp["method"], "url": base_url + path, "json": inp.get("body") or {}}
        else:
            q = inp.get("queryParams") or {}
            g["example_request"] = {"method": "GET", "url": base_url + path + (("?" + urlencode({k: str(v) for k, v in q.items()})) if q else "")}
        req = (((bazaar or {}).get("schema") or {}).get("properties", {}).get("input", {}).get("properties", {})
               .get("queryParams", {}) or {}).get("required")
        if req:
            g["required_params"] = req
    except Exception:
        pass
    fp = _free_preview_for(path)
    if fp:
        g["free_preview"] = base_url + fp
    g["steps"] = [
        "1. Read `accepts`, pick a network you hold USDC on (Base is cheapest).",
        "2. Sign the EIP-3009 authorization for `amount` to `payTo` (any x402 client: x402-fetch, x402-axios, x402 Python `x402.clients`).",
        "3. Retry the SAME request with header PAYMENT-SIGNATURE (v2) or X-PAYMENT (v1). Missing/invalid params return 400 with no charge.",
    ]
    g["catalog"] = base_url + "/.well-known/x402"
    # Always surface catalog passes so agents can convert after free tries / unpaid 402s.
    g["pay_options"] = {
        "per_call": {"price_usd": round(price_usd, 6), "path": path},
        "day_pass": {"endpoint": base_url + "/v1/pass/day", "price_usd": 2.0,
                     "covers": "paid lookups up to $1/call for 24h incl. intel risk-check/batch; excludes live scrapes, dataset/bundle downloads over $1 (stolen-funds $12), passes, alert subs"},
        "month_pass": {"endpoint": base_url + "/v1/pass/month", "price_usd": 25.0,
                       "covers": "same as day pass for 30 days (≤$1/call only; stolen-funds $12 not covered)"},
        "prepaid_credits": {"endpoint": base_url + "/v1/credits/deposit",
                            "header": "Authorization: Bearer sk_scrapling_…",
                            "covers": "deposit USDC/ETH once; metered zero-latency calls against balance"},
    }
    return g


def _payment_required_body_extras(body_402: Dict[str, Any], request: "Request", bazaar, price_usd: float) -> None:
    try:
        base = _public_base_url(request)
        body_402["buyer_guide"] = _buyer_guide(request.url.path, base, bazaar, price_usd)
        from .x402 import session_offers, session_offers_enabled
        if session_offers_enabled():
            ext = dict(body_402.get("extensions") or {})
            ext["sessionOffers"] = session_offers(base)
            body_402["extensions"] = ext
    except Exception:
        pass


def _coerce_query_example(example: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
    """Cast example query values to the JSON types their schema declares ("5" -> 5 for a number param).
    Coinbase Bazaar validates `info` against `schema`; a string where the schema says number/integer
    fails validation and the route never gets listed."""
    props = (schema or {}).get("properties") or {}
    out: Dict[str, Any] = {}
    for k, v in (example or {}).items():
        t = (props.get(k) or {}).get("type")
        try:
            if t == "integer" and isinstance(v, str):
                v = int(float(v))
            elif t == "number" and isinstance(v, str):
                f = float(v)
                v = int(f) if f.is_integer() else f
            elif t == "boolean" and isinstance(v, str):
                v = v.strip().lower() in ("1", "true", "yes")
        except ValueError:
            pass
        out[k] = v
    return out


def _bazaar_extension(
    method: str,
    input_example: Dict[str, Any],
    input_schema: Dict[str, Any],
    output_example: Dict[str, Any],
) -> Dict[str, Any]:
    """Build an x402 v2 `extensions.bazaar` block (info + JSON Schema 2020-12 validating info)."""
    if method in ("POST", "PUT", "PATCH"):
        info_input = {"type": "http", "method": method, "bodyType": "json", "body": input_example}
        input_props = {
            "type": {"type": "string", "const": "http"},
            "method": {"type": "string", "enum": ["POST", "PUT", "PATCH"]},
            "bodyType": {"type": "string", "enum": ["json", "form-data", "text"]},
            "body": input_schema,
            "queryParams": {"type": "object", "additionalProperties": {"type": "string"}},
            "headers": {"type": "object", "additionalProperties": {"type": "string"}},
        }
        required = ["type", "method", "bodyType", "body"]
    else:
        input_example = _coerce_query_example(input_example, input_schema)
        info_input = {"type": "http", "method": method, "queryParams": input_example}
        input_props = {
            "type": {"type": "string", "const": "http"},
            "method": {"type": "string", "enum": ["GET", "HEAD", "DELETE"]},
            "queryParams": input_schema,
            "headers": {"type": "object", "additionalProperties": {"type": "string"}},
        }
        required = ["type", "method"] + (["queryParams"] if input_schema.get("required") else [])
    return {
        "info": {"input": info_input, "output": {"type": "json", "example": output_example}},
        "schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "input": {
                    "type": "object",
                    "properties": input_props,
                    "required": required,
                    "additionalProperties": False,
                },
                "output": _BAZAAR_OUTPUT_SCHEMA,
            },
            "required": ["input"],
        },
    }


_SCRAPE_TIERS = ["auto", "cached_intel", "static_scrape", "stealth_browser"]
_SCRAPE_OUTPUT_EXAMPLE = {
    "status": 200,
    "url": "https://example.com",
    "tier_used": "static_scrape",
    "quality_score": 90,
    "data": {"title": "Example Domain", "markdown": "# Example Domain ...", "json_ld": [], "tables": []},
    "provenance": {"sha256": "<sha256 of fetched page>"},
}
_SCRAPE_BODY_SCHEMA = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "description": "Target URL to scrape."},
        "tier": {"type": "string", "enum": _SCRAPE_TIERS},
        "css_selector": {"type": "string"},
        "xpath_selector": {"type": "string"},
        "extract_rules": {"type": "object", "additionalProperties": {"type": "string"}},
        "use_cache": {"type": "boolean"},
    },
    "required": ["url"],
}
_SCRAPE_QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "url": {"type": "string"},
        "tier": {"type": "string", "enum": _SCRAPE_TIERS},
        "css_selector": {"type": "string"},
    },
    "required": ["url"],
}
_BATCH_BODY_SCHEMA = {
    "type": "object",
    "properties": {
        "urls": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": MAX_BATCH_URLS},
        "tier": {"type": "string", "enum": _SCRAPE_TIERS},
        "extract_rules": {"type": "object", "additionalProperties": {"type": "string"}},
        "use_cache": {"type": "boolean"},
    },
    "required": ["urls"],
}
_BUY_QUERY_SCHEMA = {
    "type": "object",
    "properties": {"format": {"type": "string", "enum": ["json", "jsonl", "csv", "markdown"]}},
}


def _build_storefront_html(config: PaymentConfig) -> str:
    """Public human landing page (clean, mobile-friendly). Machine routes untouched."""
    wallet = config.receiving_address
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta name="description" content="Live crypto and web data for AI agents. Pay per call with x402 USDC — no API key, no signup." />
  <title>Scrapling x402 Data Store</title>
  <link rel="icon" type="image/png" href="/logo.png">
  <link rel="alternate" type="application/atom+xml" title="Scrapling Data pulse" href="/feed.xml">
  <link rel="alternate" type="application/json" title="Scrapling Data pulse (JSON)" href="/v1/free/pulse.json">
  <link rel="sitemap" type="application/xml" href="/sitemap.xml">
  <style>
    :root {{
      --bg: #0b0f19;
      --panel: #121a2b;
      --border: #243048;
      --text: #eef2ff;
      --muted: #94a3b8;
      --accent: #34d399;
      --accent2: #38bdf8;
      --warn: #fbbf24;
      --radius: 14px;
      --max: 880px;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
      background: radial-gradient(1200px 600px at 10% -10%, rgba(52,211,153,0.12), transparent 55%),
                  radial-gradient(900px 500px at 100% 0%, rgba(56,189,248,0.10), transparent 50%),
                  var(--bg);
      color: var(--text);
      line-height: 1.6;
      min-height: 100vh;
    }}
    a {{ color: var(--accent); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .wrap {{ max-width: var(--max); margin: 0 auto; padding: 28px 20px 64px; }}
    header {{
      display: flex; justify-content: space-between; align-items: center;
      gap: 16px; flex-wrap: wrap; margin-bottom: 36px;
    }}
    .brand {{ display: flex; align-items: center; gap: 12px; font-weight: 700; font-size: 17px; }}
    .brand img {{ width: 32px; height: 32px; border-radius: 8px; }}
    .badge {{
      font-size: 11px; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase;
      color: var(--accent); background: rgba(52,211,153,0.12); border: 1px solid rgba(52,211,153,0.35);
      padding: 4px 10px; border-radius: 999px;
    }}
    nav {{ display: flex; gap: 14px; flex-wrap: wrap; font-size: 14px; }}
    nav a {{ color: var(--muted); }}
    nav a:hover {{ color: var(--text); }}
    .hero h1 {{
      font-size: clamp(28px, 5vw, 40px); line-height: 1.15; letter-spacing: -0.03em;
      font-weight: 800; margin-bottom: 14px; max-width: 16ch;
    }}
    .hero p.lead {{
      color: var(--muted); font-size: 17px; max-width: 40rem; margin-bottom: 22px;
    }}
    .cta-row {{ display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 10px; }}
    .btn {{
      display: inline-flex; align-items: center; justify-content: center; gap: 8px;
      padding: 11px 16px; border-radius: 10px; font-weight: 700; font-size: 14px;
      border: 1px solid transparent; text-decoration: none !important;
    }}
    .btn-primary {{ background: var(--accent); color: #042f2e; }}
    .btn-secondary {{ background: transparent; color: var(--text); border-color: var(--border); }}
    .btn-ghost {{ background: #0f172a; color: #99f6e4; border-color: var(--border); }}
    .pills {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 18px 0 40px; }}
    .pill {{
      font-size: 12.5px; color: var(--muted); background: rgba(255,255,255,0.03);
      border: 1px solid var(--border); padding: 6px 10px; border-radius: 999px;
    }}
    section {{ margin-bottom: 36px; }}
    section h2 {{
      font-size: 13px; letter-spacing: 0.08em; text-transform: uppercase;
      color: var(--accent2); margin-bottom: 14px; font-weight: 700;
    }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }}
    .card {{
      background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius);
      padding: 18px 16px;
    }}
    .card h3 {{ font-size: 15px; margin-bottom: 6px; }}
    .card p {{ color: var(--muted); font-size: 14px; }}
    .price {{
      font-size: 22px; font-weight: 800; color: var(--accent); margin: 4px 0 6px;
    }}
    .steps {{ display: grid; gap: 10px; }}
    .step {{
      display: grid; grid-template-columns: 36px 1fr; gap: 12px; align-items: start;
      background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius);
      padding: 14px 16px;
    }}
    .num {{
      width: 36px; height: 36px; border-radius: 10px; display: grid; place-items: center;
      background: rgba(52,211,153,0.12); color: var(--accent); font-weight: 800;
    }}
    .step strong {{ display: block; margin-bottom: 2px; }}
    .step span {{ color: var(--muted); font-size: 14px; }}
    .mono {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12.5px; color: #7dd3fc; word-break: break-all;
    }}
    footer {{
      margin-top: 48px; padding-top: 20px; border-top: 1px solid var(--border);
      color: var(--muted); font-size: 13.5px; display: flex; flex-wrap: wrap; gap: 10px 16px;
    }}
    @media (max-width: 560px) {{
      .hero h1 {{ max-width: none; }}
      .btn {{ width: 100%; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <div class="brand">
        <img src="/logo.png" alt="" width="32" height="32" />
        <span>Scrapling x402</span>
        <span class="badge">Live</span>
      </div>
      <nav>
        <a href="/hacks">Hack Tracker</a>
        <a href="/quality">Quality</a>
        <a href="/bundles">Bundles</a>
        <a href="/docs">Docs</a>
        <a href="/llms.txt">llms.txt</a>
        <a href="/openapi.json">OpenAPI</a>
        <a href="/.well-known/x402">x402</a>
        <a href="/test-buy">Try pay</a>
      </nav>
    </header>

    <div class="hero">
      <h1>Live crypto &amp; web data for AI agents</h1>
      <p class="lead">
        Pay per call in USDC via <strong style="color:var(--text)">x402</strong> — no API key, no account, no charge on failure.
        Free samples to try; full catalog from <strong style="color:var(--text)">$0.001</strong>.
      </p>
      <div class="cta-row">
        <a class="btn btn-primary" href="/v1/free/market-overview">Try a free sample</a>
        <a class="btn btn-ghost" href="/docs">API docs</a>
        <a class="btn btn-secondary" href="/llms.txt">Full route list</a>
      </div>
      <div class="pills">
        <span class="pill">No API key</span>
        <span class="pill">USDC on Base &amp; more</span>
        <span class="pill">HTTP 402 pay-per-call</span>
        <span class="pill">Agents + humans</span>
      </div>
    </div>

    <section>
      <h2>What you get</h2>
      <div class="cards">
        <div class="card"><h3>Crypto &amp; on-chain</h3><p>Prices, candles, yields, whales, token risk, ENS, balances, market pulse.</p></div>
        <div class="card"><h3>Research &amp; web</h3><p>News, web search, PDF text, YouTube transcripts, FX, company-from-domain.</p></div>
        <div class="card"><h3>Agent-ready</h3><p>OpenAPI, x402 manifest, A2A agent card, MCP, llms.txt — plug in and pay.</p></div>
      </div>
    </section>

    <section>
      <h2>Free samples</h2>
      <div class="cards">
        <div class="card"><h3>Market overview</h3><p><a href="/v1/free/market-overview">/v1/free/market-overview</a> — Fear &amp; Greed + BTC/ETH</p></div>
        <div class="card"><h3>Candles</h3><p><a href="/v1/free/candles">/v1/free/candles</a> — last 5 OHLCV bars</p></div>
        <div class="card"><h3>News</h3><p><a href="/v1/free/news">/v1/free/news</a> — top headlines</p></div>
        <div class="card"><h3>ENS</h3><p><a href="/v1/free/ens">/v1/free/ens</a> — vitalik.eth sample</p></div>
      </div>
      <p style="margin-top:12px;color:var(--muted);font-size:14px;">More free teasers in <a href="/llms.txt">llms.txt</a> and <a href="/docs">/docs</a>.</p>
    </section>

    <section>
      <h2>Bundles</h2>
      <div class="cards">
        <div class="card"><h3>Wallet labels</h3><div class="price">$15</div><p>~55k business/public address tags. <a href="/v1/bundles/wallet-labels-full/download">x402</a></p></div>
        <div class="card"><h3>Hack tracker</h3><div class="price">$10</div><p>Incidents + attacker wallets + flows. <a href="/v1/bundles/hack-tracker-export/download">x402</a></p></div>
        <div class="card"><h3>Tokenized / RE / treasuries</h3><div class="price">$10</div><p>Stocks, RealT, corporate treasuries snapshot. <a href="/v1/bundles/tokenized-assets-snapshot/download">x402</a></p></div>
        <div class="card"><h3>DeFi yields + pools</h3><div class="price">$5</div><p>Top yields + staking + flows. <a href="/v1/bundles/defi-yields-pools-snapshot/download">x402</a></p></div>
        <div class="card"><h3>All-in crypto data</h3><div class="price">$25</div><p>Everything above + regime/feeds. <a href="/v1/bundles/all-in-crypto-data/download">x402</a></p></div>
      </div>
      <p style="margin-top:12px;color:var(--muted);font-size:14px;">Catalog <a href="/v1/bundles">/v1/bundles</a> · human page <a href="/bundles">/bundles</a> · same zips on <a href="https://scrapling.gumroad.com" rel="noopener" target="_blank">Gumroad</a>.</p>
    </section>

    <section>
      <h2>Pricing</h2>
      <div class="cards">
        <div class="card">
          <h3>Pay per call</h3>
          <div class="price">from $0.001</div>
          <p>Most lookups $0.001–$0.005. Bundles and reports a bit more. Pay only when the call succeeds.</p>
        </div>
        <div class="card">
          <h3>Day / month pass</h3>
          <div class="price">$2 / $25</div>
          <p>Catalog pass if you prefer one payment over many micropayments. See <a href="/docs">docs</a>.</p>
        </div>
        <div class="card">
          <h3>Datasets &amp; API plans</h3>
          <p style="margin-top:10px;">Humans: downloadable zips on Gumroad; monthly API plans on RapidAPI.</p>
        </div>
      </div>
    </section>

    <section>
      <h2>How to buy</h2>
      <div class="steps">
        <div class="step">
          <div class="num">1</div>
          <div>
            <strong>x402 (agents &amp; builders)</strong>
            <span>Call any paid route → get HTTP 402 → pay USDC → retry with payment proof. No key.
              Try <a href="/test-buy">/test-buy</a> or read <a href="/.well-known/x402">/.well-known/x402</a>.</span>
          </div>
        </div>
        <div class="step">
          <div class="num">2</div>
          <div>
            <strong>RapidAPI</strong>
            <span>Monthly plans for on-chain intel endpoints —
              <a href="https://rapidapi.com/scraplingdata/api/scrapling-on-chain-intel" rel="noopener" target="_blank">scrapling-on-chain-intel</a>.</span>
          </div>
        </div>
        <div class="step">
          <div class="num">3</div>
          <div>
            <strong>Gumroad</strong>
            <span>Downloadable datasets (tokenized stocks, hack tracker, flows, treasuries, RealT) —
              <a href="https://scrapling.gumroad.com" rel="noopener" target="_blank">scrapling.gumroad.com</a>.</span>
          </div>
        </div>
      </div>
      <p style="margin-top:14px;color:var(--muted);font-size:13px;">
        Settlement wallet (x402): <span class="mono">{wallet}</span>
      </p>
    </section>

    <section>
      <h2>For machines</h2>
      <div class="cards">
        <div class="card"><h3>Discovery</h3><p><a href="/llms.txt">/llms.txt</a> · <a href="/openapi.json">/openapi.json</a> · <a href="/.well-known/agent-card.json">agent card</a> · <a href="/a2a">/a2a</a></p></div>
        <div class="card"><h3>MCP</h3><p><a href="https://mcp.so/servers/scrapling-x402-data-store" rel="noopener" target="_blank">mcp.so listing</a> · claim <a href="/.well-known/mcprush.com-claim.txt">mcprush claim</a></p></div>
      </div>
    </section>

    <footer>
      <span>© Scrapling x402 Data Store</span>
      <a href="/quality">Quality</a>
      <a href="/bundles">Bundles</a>
      <a href="/docs">Docs</a>
      <a href="/terms">Terms</a>
      <a href="/privacy">Privacy</a>
      <a href="mailto:scraplingdata@mail.grokbot.com">Contact</a>
      <a href="/feed.xml">Feed</a>
    </footer>
  </div>
</body>
</html>"""


def hacks_tracker_page_html() -> str:
    """Self-contained human page for GET /hacks. The browser loads GET /hacks/data."""
    return r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta name="description" content="Where money taken in large public crypto thefts is sitting right now: still held, frozen, moved, bridged, or sent to an exchange. Public blockchain totals, refreshed every 60 seconds." />
  <title>Hack Money Tracker — Scrapling</title>
  <link rel="icon" type="image/png" href="/logo.png" />
  <style>
    :root {
      --bg: #0b0f19;
      --panel: #121a2b;
      --border: #243048;
      --text: #eef2ff;
      --muted: #94a3b8;
      --accent: #34d399;
      --accent2: #38bdf8;
      --warn: #fbbf24;
      --radius: 14px;
      --max: 960px;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
      background: radial-gradient(1200px 600px at 10% -10%, rgba(52,211,153,0.12), transparent 55%),
                  radial-gradient(900px 500px at 100% 0%, rgba(56,189,248,0.10), transparent 50%),
                  var(--bg);
      color: var(--text);
      line-height: 1.6;
      min-height: 100vh;
    }
    a { color: var(--accent); text-decoration: none; }
    a:hover { text-decoration: underline; }
    .wrap { max-width: var(--max); margin: 0 auto; padding: 28px 20px 64px; }
    header {
      display: flex; justify-content: space-between; align-items: center;
      gap: 16px; flex-wrap: wrap; margin-bottom: 32px;
    }
    .brand { display: flex; align-items: center; gap: 12px; font-weight: 700; font-size: 17px; color: var(--text); }
    .brand img { width: 32px; height: 32px; border-radius: 8px; }
    .badge {
      font-size: 11px; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase;
      color: var(--accent); background: rgba(52,211,153,0.12); border: 1px solid rgba(52,211,153,0.35);
      padding: 4px 10px; border-radius: 999px;
    }
    nav { display: flex; gap: 14px; flex-wrap: wrap; font-size: 14px; }
    nav a { color: var(--muted); }
    nav a:hover, nav a[aria-current="page"] { color: var(--text); }
    h1 {
      font-size: clamp(28px, 5vw, 40px); line-height: 1.15; letter-spacing: -0.03em;
      font-weight: 800; margin-bottom: 12px;
    }
    .lead { color: var(--muted); font-size: 17px; max-width: 42rem; margin-bottom: 16px; }
    section { margin-bottom: 32px; }
    section h2 {
      font-size: 13px; letter-spacing: 0.08em; text-transform: uppercase;
      color: var(--accent2); margin-bottom: 12px; font-weight: 700;
    }
    .card {
      background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius);
      padding: 18px 16px; margin-bottom: 12px;
    }
    .card h3 { font-size: 18px; letter-spacing: -0.02em; margin-bottom: 4px; }
    .muted { color: var(--muted); }
    .fine { color: var(--muted); font-size: 13.5px; margin-top: 8px; }
    .mono {
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12.5px; color: #7dd3fc; word-break: break-all;
    }
    .status-bar {
      background: rgba(255,255,255,0.03); border: 1px solid var(--border);
      border-radius: var(--radius); padding: 12px 14px; margin-bottom: 22px; font-size: 14px;
    }
    .status-bar p + p { margin-top: 6px; }
    .legend { color: var(--muted); font-size: 14px; margin-bottom: 14px; }
    .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-top: 14px; }
    .stat {
      background: #0d1424; border: 1px solid var(--border); border-radius: 12px; padding: 12px;
    }
    .stat .k { display: block; color: var(--muted); font-size: 12.5px; line-height: 1.35; margin-bottom: 6px; }
    .stat .v { display: block; font-size: 20px; font-weight: 800; letter-spacing: -0.03em; line-height: 1.2; }
    .stat .s { display: block; color: var(--muted); font-size: 12.5px; margin-top: 4px; }
    .notes { list-style: none; margin-top: 12px; display: grid; gap: 8px; }
    .notes li, .banner {
      background: rgba(251,191,36,0.08); border: 1px solid rgba(251,191,36,0.35);
      color: #fde68a; border-radius: 10px; padding: 8px 10px; font-size: 13.5px;
    }
    .notes li.ok {
      background: rgba(52,211,153,0.08); border-color: rgba(52,211,153,0.3); color: #a7f3d0;
    }
    .chains { display: grid; gap: 8px; margin-top: 12px; }
    .chain {
      display: grid; gap: 2px; border-top: 1px solid var(--border); padding-top: 8px; font-size: 14px;
    }
    .row { padding: 12px 0; border-top: 1px solid var(--border); }
    .row:first-child { border-top: 0; padding-top: 0; }
    .row-top { display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: baseline; }
    .chip-row { display: flex; flex-wrap: wrap; gap: 8px; margin: 8px 0 14px; }
    .chip {
      font-size: 13px; color: var(--text); background: rgba(255,255,255,0.03);
      border: 1px solid var(--border); padding: 6px 10px; border-radius: 999px;
    }
    .chip[aria-current="page"] { border-color: rgba(52,211,153,0.5); color: var(--accent); }
    form.lookup { display: flex; flex-wrap: wrap; gap: 8px; align-items: end; margin-top: 8px; }
    label { font-size: 13px; color: var(--muted); display: grid; gap: 4px; }
    input[type="text"] {
      background: #0d1424; color: var(--text); border: 1px solid var(--border);
      border-radius: 10px; padding: 10px 12px; font-size: 14px; min-width: min(100%, 240px);
    }
    .btn {
      display: inline-flex; align-items: center; justify-content: center; gap: 8px;
      padding: 11px 16px; border-radius: 10px; font-weight: 700; font-size: 14px;
      border: 1px solid transparent; text-decoration: none !important; cursor: pointer;
      font-family: inherit;
    }
    .btn-primary { background: var(--accent); color: #042f2e; }
    .btn-secondary { background: transparent; color: var(--text); border-color: var(--border); }
    button:disabled, .btn:disabled {
      opacity: 0.55; cursor: not-allowed;
    }
    .tag {
      font-size: 11px; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase;
      border-radius: 999px; padding: 2px 8px; border: 1px solid var(--border); color: var(--muted);
    }
    .tag.frozen { color: var(--warn); border-color: rgba(251,191,36,0.45); }
    .tag.returned { color: var(--accent); border-color: rgba(52,211,153,0.45); }
    .tag.retired, .tag.superseded { color: var(--accent2); border-color: rgba(56,189,248,0.4); }
    .cta-box {
      background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius); padding: 18px 16px;
    }
    .cta-box h2 { margin-bottom: 8px; }
    .cta-box p { color: var(--muted); font-size: 14.5px; margin-bottom: 12px; max-width: 40rem; }
    footer {
      margin-top: 12px; padding-top: 20px; border-top: 1px solid var(--border);
      color: var(--muted); font-size: 13.5px; display: flex; flex-wrap: wrap; gap: 10px 16px;
    }
    #js-errors:not(:empty) {
      margin-top: 10px; color: #fecaca; font-size: 13px;
    }
    @media (max-width: 640px) {
      .stats { grid-template-columns: 1fr 1fr; }
      .btn { width: 100%; }
      form.lookup { display: grid; }
      input[type="text"] { width: 100%; }
    }
    @media (max-width: 380px) {
      .stats { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <a class="brand" href="/">
        <img src="/logo.png" alt="" width="32" height="32" />
        <span>Scrapling x402</span>
        <span class="badge">Live</span>
      </a>
      <nav>
        <a href="/hacks" aria-current="page">Hack Tracker</a>
        <a href="/quality">Quality</a>
        <a href="/bundles">Bundles</a>
        <a href="/docs">Docs</a>
        <a href="/llms.txt">llms.txt</a>
        <a href="/openapi.json">OpenAPI</a>
        <a href="/.well-known/x402">x402</a>
        <a href="/test-buy">Try pay</a>
      </nav>
    </header>

    <h1>Hack Money Tracker</h1>
    <p class="lead">Where money taken in large public crypto thefts is sitting right now, from this service's free on-chain feeds. Totals cover wallets the tracker has publicly tied to an incident. They are not a full accounting of the theft.</p>

    <div class="status-bar" id="status-bar">
      <p id="status-line">Loading public totals…</p>
      <p class="muted" id="checked-at">Page check: not yet.</p>
      <p class="muted" id="next-refresh">Refreshes every 60 seconds.</p>
      <p class="fine">This page reads /hacks/data every 60 seconds. The server builds that response in-process and keeps it for 60 seconds, separate from the 20-call daily limit on the free feeds. This route allows about 60 requests a minute per IP.</p>
    </div>

    <section>
      <h2>Incident</h2>
      <p class="legend">One incident per refresh. The page opens on the feed's default. Other incident ids that show up in the cleared / retired list are linked below. You can also type an id.</p>
      <div class="chip-row" id="incident-links"></div>
      <form class="lookup" method="get" action="/hacks">
        <label>Incident id
          <input id="incident-input" type="text" name="incident" maxlength="80" autocomplete="off" spellcheck="false" placeholder="binance-2026" />
        </label>
        <button class="btn btn-secondary" type="submit">Show this incident</button>
      </form>
    </section>

    <section>
      <h2>Where it is now</h2>
      <p class="legend">Held: still in wallets attributed to the incident. Frozen: blocked by the token issuer's on-chain list (for example USDT or USDC), which is not the same as money returned to the owner. Moved: left those wallets. Bridged: sent across chains or through a mixer. Sent to an exchange: arrived at a deposit address the tracker recognizes. The window is the last 24 hours unless a line says last hour. A shown zero is a reported zero. A dash means that field was not in the response.</p>
      <div id="incidents"><p class="muted">Loading incidents…</p></div>
    </section>

    <section>
      <h2>Latest big moves</h2>
      <p class="legend">The free alert feed includes the newest alert overall and the newest alert on each chain, with time, kind, and size when the feed has them. It is not the full alert history.</p>
      <div class="card" id="alerts"><p class="muted">Loading alerts…</p></div>
    </section>

    <section>
      <h2>Cleared / retired</h2>
      <p class="legend">Records the tracker has closed: funds marked frozen or returned, and labels or facts marked retired or replaced. Frozen here means an on-chain blocklist observation. It does not mean the funds were paid back.</p>
      <div class="card" id="cleared"><p class="muted">Loading cleared and retired records…</p></div>
    </section>

    <section>
      <div class="cta-box">
        <h2>Alerts + full detail — $5/month</h2>
        <p>Wallet-by-wallet holdings, freeze status, and the full alert list are not on this free page. A $5/month plan is not for sale here yet.</p>
        <a class="btn btn-primary" href="https://scrapling.gumroad.com/l/wwvltf" target="_blank" rel="noopener">Subscribe &mdash; $5/month</a>
      </div>
    </section>

    <p class="fine">Public blockchain totals from this service's free feeds. Not a loss estimate, not a recovery status, and not financial, legal, or security advice.</p>
    <p id="js-errors"></p>

    <footer>
      <span>© Scrapling x402 Data Store</span>
      <a href="/">Home</a>
      <a href="/quality">Quality</a>
      <a href="/terms">Terms</a>
      <a href="/privacy">Privacy</a>
    </footer>
  </div>
  <script>
  (function () {
    'use strict';

    var REFRESH_MS = 60000;
    var state = { tracker: null, alerts: null, tombs: null, notes: {}, checkedAt: null };
    var serverCacheNote = '';
    var timer = null;
    var tick = null;
    var busy = false;
    var nextAt = 0;

    var CHAIN_NAMES = {
      ethereum: 'Ethereum', bitcoin: 'Bitcoin', tron: 'Tron', base: 'Base', solana: 'Solana',
      arbitrum: 'Arbitrum', polygon: 'Polygon', bnb: 'BNB Chain', optimism: 'Optimism', gnosis: 'Gnosis'
    };
    var KIND_NAMES = {
      transfer: 'Transfer', swap: 'Swap', bridge: 'Bridge', mixer: 'Mixer',
      cex_deposit: 'Sent to an exchange', exchange: 'Sent to an exchange',
      exchange_inflow: 'Sent to an exchange', exchange_deposit: 'Sent to an exchange'
    };
    var STATUS_NAMES = { frozen: 'Frozen', retired: 'Retired', returned: 'Returned', superseded: 'Replaced' };
    var FIGURE_NAMES = {
      held: 'still held', moved_out_1h: 'moved in the last hour', moved_out_24h: 'moved in the last 24 hours'
    };

    function esc(s) {
      return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }
    function chainName(id) {
      var k = String(id || '').toLowerCase();
      return CHAIN_NAMES[k] || (k ? k.charAt(0).toUpperCase() + k.slice(1) : 'Unknown chain');
    }
    function isNum(n) { return typeof n === 'number' && isFinite(n); }
    function moneyExact(n) {
      return (n < 0 ? '-' : '') + '$' + Math.abs(Number(n)).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    function moneyShort(n) {
      if (!isNum(n)) return '—';
      if (n === 0) return '$0';
      var a = Math.abs(n);
      var sign = n < 0 ? '-' : '';
      if (a >= 1e9) return sign + '$' + (a / 1e9).toFixed(2) + ' billion';
      if (a >= 1e6) return sign + '$' + (a / 1e6).toFixed(2) + ' million';
      if (a >= 1e4) return sign + '$' + Math.round(a).toLocaleString('en-US');
      return moneyExact(n);
    }
    function moneyPair(n) {
      if (!isNum(n)) return { short: '—', exact: '' };
      var exact = moneyExact(n);
      var short = moneyShort(n);
      return { short: short, exact: short === exact || short === '$0' ? '' : exact };
    }
    function when(iso) {
      if (!iso) return 'time not provided';
      var d = new Date(iso);
      if (isNaN(d.getTime())) return String(iso);
      var p = function (x) { return (x < 10 ? '0' : '') + x; };
      return d.getUTCFullYear() + '-' + p(d.getUTCMonth() + 1) + '-' + p(d.getUTCDate()) + ' ' + p(d.getUTCHours()) + ':' + p(d.getUTCMinutes()) + ':' + p(d.getUTCSeconds()) + ' UTC';
    }
    function stampOf(body) {
      if (!body || typeof body !== 'object') return null;
      if (body.data_as_of && body.data_as_of.timestamp_utc) return body.data_as_of.timestamp_utc;
      if (typeof body.as_of === 'string') return body.as_of;
      return null;
    }
    function limitMessage(pack) {
      if (!pack) return null;
      if (pack.staleDetail) return pack.staleDetail;
      var body = pack.body;
      if (!body || typeof body !== 'object') {
        return pack.ok ? null : ('HTTP ' + pack.status + (pack.error ? ': ' + pack.error : ''));
      }
      var detail = typeof body.detail === 'string' ? body.detail : '';
      var looksLimited = /limit reached|too many requests|HTTP 429/i.test(detail);
      var hasPayload = !!(body.incident || body.headline || body.headline_all_chains || body.latest_alert || body.tombstones || Array.isArray(body));
      if (pack.status === 429) return detail || 'Free tier limit reached (HTTP 429).';
      if (!pack.ok) return detail || pack.error || ('HTTP ' + pack.status);
      if (looksLimited && !hasPayload) return detail;
      return null;
    }
    function parseIncidents(data) {
      if (!data) return [];
      var list = null;
      if (Array.isArray(data)) list = data;
      else if (Array.isArray(data.incidents)) list = data.incidents;
      else if (Array.isArray(data.items) && (data.items.length === 0 || data.items[0].incident || data.items[0].headline)) list = data.items;
      else if (data.incident || data.headline || data.headline_all_chains || data.name) list = [data];
      else return [];
      return list.filter(function (row) {
        return row && typeof row === 'object' && (row.incident || row.name || row.headline || row.headline_all_chains);
      });
    }
    function pick(all, headline, key) {
      if (all && Object.prototype.hasOwnProperty.call(all, key) && isNum(all[key])) return { value: all[key], scope: 'all' };
      if (headline && Object.prototype.hasOwnProperty.call(headline, key) && isNum(headline[key])) return { value: headline[key], scope: 'eth' };
      return { value: null, scope: 'missing' };
    }
    function staleNotes(inc) {
      var notes = [];
      var all = (inc && inc.headline_all_chains) || {};
      var by = all.by_chain || (inc && inc.chains) || {};
      if (all.stale_note) notes.push({ ok: false, text: String(all.stale_note) });
      var staleChains = all.chains_included_stale;
      if (Array.isArray(staleChains) && staleChains.length) {
        notes.push({ ok: false, text: 'Included from an older read: ' + staleChains.map(chainName).join(', ') + '. A chain that failed its last read keeps its previous balance in the total.' });
      } else if (staleChains && typeof staleChains === 'string') {
        notes.push({ ok: false, text: 'Included from an older read: ' + staleChains + '.' });
      }
      if (all.chains_not_included) {
        var missing = Array.isArray(all.chains_not_included) ? all.chains_not_included.map(chainName).join(', ') : String(all.chains_not_included);
        if (missing) notes.push({ ok: false, text: 'Not included in this total: ' + missing + '.' });
      }
      if (all.spread_ok === false) {
        notes.push({ ok: false, text: 'Chain read times are too far apart to treat this total as one moment (' + all.spread_seconds + 's apart; this feed’s limit is ' + all.max_spread_seconds + 's).' });
      } else if (all.spread_ok === true && isNum(all.spread_seconds)) {
        notes.push({ ok: true, text: 'Chain read times are within this feed’s spread check (' + all.spread_seconds + 's apart; limit ' + all.max_spread_seconds + 's).' });
      }
      var figures = all.figure_spreads || {};
      Object.keys(figures).forEach(function (key) {
        var fig = figures[key];
        if (fig && fig.spread_ok === false) {
          notes.push({ ok: false, text: 'The “' + (FIGURE_NAMES[key] || key) + '” figure is outside its own spread check (' + fig.spread_seconds + 's; limit ' + fig.max_spread_seconds + 's).' });
        }
      });
      Object.keys(by).forEach(function (name) {
        var c = by[name] || {};
        if (c.status && c.status !== 'ok') {
          var extra = '';
          if (c.stale && c.stale.reason) extra += ' ' + c.stale.reason + '.';
          if (c.stale && isNum(c.stale.age_seconds)) extra += ' Last good read ' + Math.round(c.stale.age_seconds) + 's before this response.';
          notes.push({ ok: false, text: chainName(name) + ' is marked ' + c.status + '.' + extra });
        }
        if (c.check && c.check.state && c.check.state !== 'checked_ok' && c.check.state !== 'checked_ok_empty') {
          notes.push({ ok: false, text: chainName(name) + ' check: ' + c.check.state + (c.check.last_error ? ' — ' + c.check.last_error : '') + '.' });
        }
      });
      return notes;
    }
    function alertRows(data) {
      var rows = [];
      if (!data || typeof data !== 'object') return rows;
      var list = Array.isArray(data) ? data : (Array.isArray(data.alerts) ? data.alerts : (Array.isArray(data.latest_alerts) ? data.latest_alerts : null));
      if (list) {
        list.forEach(function (row) {
          if (row && typeof row === 'object') rows.push(row);
        });
      }
      if (!Array.isArray(data) && data.latest_alert && typeof data.latest_alert === 'object') {
        var top = {};
        Object.keys(data.latest_alert).forEach(function (k) { top[k] = data.latest_alert[k]; });
        top.chain = top.chain || 'latest';
        top._slot = 'Newest in this response';
        rows.push(top);
      }
      var chains = (!Array.isArray(data) && data.chains && typeof data.chains === 'object') ? data.chains : {};
      Object.keys(chains).forEach(function (name) {
        var c = chains[name];
        if (c && c.latest_alert && typeof c.latest_alert === 'object') {
          var row = {};
          Object.keys(c.latest_alert).forEach(function (k) { row[k] = c.latest_alert[k]; });
          row.chain = row.chain || name;
          row._slot = 'Newest on ' + chainName(name);
          rows.push(row);
        }
      });
      var seen = {};
      var out = [];
      rows.forEach(function (row) {
        var key = [row.time || '', row.kind || '', row.usd == null ? '' : row.usd, row.asset || '', row.chain || '', row._slot || ''].join('|');
        if (seen[key]) return;
        seen[key] = true;
        out.push(row);
      });
      out.sort(function (a, b) {
        var ta = a.time ? Date.parse(a.time) : 0;
        var tb = b.time ? Date.parse(b.time) : 0;
        return (isNaN(tb) ? 0 : tb) - (isNaN(ta) ? 0 : ta);
      });
      return out;
    }
    function tombstoneList(data) {
      if (!data) return [];
      if (Array.isArray(data)) return data.filter(function (t) { return t && typeof t === 'object'; });
      if (Array.isArray(data.tombstones)) return data.tombstones.filter(function (t) { return t && typeof t === 'object'; });
      return [];
    }
    function isHackTomb(t) {
      if (t.product === 'hack-tracker') return true;
      var blob = [t.entity, t.reason, t.incident, t.old_value && t.old_value.entity, t.old_value && t.old_value.category].join(' ');
      if (/hack|exploit|attacker|stolen/i.test(blob)) return true;
      return t.status === 'frozen' || t.status === 'returned';
    }
    function shortAddr(addr) {
      var s = String(addr || '');
      if (s.length < 16) return s;
      return s.slice(0, 6) + '…' + s.slice(-4);
    }
    function statCard(label, picked, extra) {
      var pair = moneyPair(picked.value);
      var scope = '';
      if (picked.scope === 'eth') scope = 'Ethereum figure only. The all-chain total does not include this field.';
      else if (picked.scope === 'missing') scope = 'Not in this response.';
      return '<div class="stat"><span class="k">' + esc(label) + '</span><span class="v">' + esc(pair.short) + '</span>' +
        (pair.exact ? '<span class="s">' + esc(pair.exact) + '</span>' : '') +
        (scope ? '<span class="s">' + esc(scope) + '</span>' : '') +
        (extra ? '<span class="s">' + extra + '</span>' : '') + '</div>';
    }
    function renderIncidents(incidents, pack) {
      var root = document.getElementById('incidents');
      var limit = limitMessage(pack);
      if (!incidents.length) {
        root.innerHTML = '<div class="card"><p>' + esc(limit || 'No incident in this response.') + '</p></div>';
        return;
      }
      var html = '';
      if (limit) html += '<div class="banner">' + esc(limit) + ' Showing the last incident copy this page loaded.</div>';
      incidents.forEach(function (inc) {
        var all = inc.headline_all_chains || {};
        var head = inc.headline || {};
        var held = pick(all, head, 'held_usd_attributed');
        var frozen = pick(all, head, 'frozen_usd');
        var moved = pick(all, head, 'moved_out_usd_24h');
        var moved1h = pick(all, head, 'moved_out_usd_1h');
        var bridged = pick(all, head, 'bridge_mixer_24h');
        var exch = pick(all, head, 'exchange_inflows_24h');
        var movedExtra = isNum(moved1h.value) ? ('Last hour: ' + esc(moneyShort(moved1h.value)) + (moved1h.scope === 'eth' && moved.scope !== 'eth' ? ' (Ethereum only)' : '')) : '';
        var chains = Array.isArray(all.chains_included) ? all.chains_included.map(chainName).join(', ') : '';
        var minT = all.inputs_as_of_min;
        var maxT = all.inputs_as_of_max;
        var asof = (minT && maxT) ? ('Chain reads from ' + when(minT) + ' to ' + when(maxT) + '.') : ('As of ' + when(stampOf(inc) || inc.as_of) + '.');
        var notes = staleNotes(inc);
        var by = all.by_chain || {};
        var chainKeys = Object.keys(by);
        if (!chainKeys.length && inc.chains) {
          by = inc.chains;
          chainKeys = Object.keys(by);
        }
        html += '<article class="card">';
        html += '<h3>' + esc(inc.name || inc.incident || 'Incident') + '</h3>';
        if (inc.incident) html += '<p class="mono">' + esc(inc.incident) + '</p>';
        html += '<p class="fine">' + esc(asof) + '</p>';
        if (chains) html += '<p class="fine">Chains added into the main figures: ' + esc(chains) + '.</p>';
        else if (inc.headline_scope) html += '<p class="fine">Scope note from the feed: ' + esc(inc.headline_scope) + '</p>';
        html += '<div class="stats">';
        html += statCard('Still held', held, '');
        html += statCard('Frozen by token issuer', frozen, '');
        html += statCard('Moved, last 24 hours', moved, movedExtra);
        html += statCard('Bridged or mixed, last 24 hours', bridged, '');
        html += statCard('Sent to a known exchange, last 24 hours', exch, '');
        html += '</div>';
        if (held.scope === 'all' && isNum(head.held_usd_attributed) && head.held_usd_attributed !== held.value) {
          html += '<p class="fine">Ethereum-only still held: ' + esc(moneyShort(head.held_usd_attributed)) + ' (' + esc(moneyExact(head.held_usd_attributed)) + '). The figures above add every chain listed.</p>';
        }
        var linked = isNum(all.held_usd_incl_linked) ? all.held_usd_incl_linked : null;
        if (linked != null && isNum(held.value) && Math.abs(linked - held.value) >= 1) {
          html += '<p class="fine">If one-hop wallets that are not confirmed are included, still held is ' + esc(moneyShort(linked)) + ' (' + esc(moneyExact(linked)) + ').</p>';
        }
        var addr = isNum(all.addresses_attributed) ? all.addresses_attributed : (isNum(head.addresses_attributed) ? head.addresses_attributed : null);
        var unconf = isNum(all.addresses_linked_unconfirmed) ? all.addresses_linked_unconfirmed : (isNum(head.addresses_linked_unconfirmed) ? head.addresses_linked_unconfirmed : null);
        if (addr != null) {
          html += '<p class="fine">' + esc(String(addr)) + ' wallet' + (addr === 1 ? '' : 's') + ' publicly attributed' + (unconf != null ? ('; ' + unconf + ' more one hop away and not confirmed') : '') + '.</p>';
        }
        if (notes.length) {
          html += '<ul class="notes">';
          notes.forEach(function (n) { html += '<li class="' + (n.ok ? 'ok' : '') + '">' + esc(n.text) + '</li>'; });
          html += '</ul>';
        }
        if (chainKeys.length) {
          html += '<div class="chains">';
          chainKeys.forEach(function (name) {
            var c = by[name] || {};
            var as = (c.data_as_of && c.data_as_of.timestamp_utc) || '';
            var block = c.data_as_of && c.data_as_of.block && isNum(c.data_as_of.block.number) ? c.data_as_of.block.number : null;
            var status = c.status ? String(c.status) : 'status not provided';
            var statusText = status === 'ok' ? 'read marked current' : ('marked ' + status);
            html += '<div class="chain"><strong>' + esc(chainName(name)) + '</strong>';
            html += '<span class="fine">As of ' + esc(when(as)) + (block != null ? (' · block ' + block) : '') + ' · ' + esc(statusText) + '.</span>';
            var bits = [];
            if (isNum(c.held_usd_attributed)) bits.push('held ' + moneyShort(c.held_usd_attributed));
            if (isNum(c.frozen_usd)) bits.push('frozen ' + moneyShort(c.frozen_usd));
            if (isNum(c.moved_out_usd_24h)) bits.push('moved 24h ' + moneyShort(c.moved_out_usd_24h));
            if (isNum(c.exchange_inflows_24h)) bits.push('to exchanges 24h ' + moneyShort(c.exchange_inflows_24h));
            if (bits.length) html += '<span class="fine">' + esc(bits.join(' · ')) + '.</span>';
            html += '</div>';
          });
          html += '</div>';
        }
        html += '</article>';
      });
      root.innerHTML = html;
    }
    function renderAlerts(pack) {
      var root = document.getElementById('alerts');
      var kept = state.notes.alerts;
      var limit = limitMessage(pack);
      var body = pack && pack.body;
      if (limit && (!body || !body.latest_alert)) {
        root.innerHTML = '<p>' + esc(limit) + '</p>' + (kept ? '<p class="fine">Showing nothing newer; the last successful alert copy was at ' + esc(when(kept)) + '.</p>' : '');
        if (!(body && (body.latest_alert || body.alerts))) return;
      }
      if (!body || typeof body !== 'object') {
        root.innerHTML = '<p>' + esc(limit || 'No alert feed loaded.') + '</p>';
        return;
      }
      var rows = alertRows(body);
      var html = '';
      if (limit) html += '<p>' + esc(limit) + '</p>';
      var asof = stampOf(body) || body.as_of;
      if (asof && !limit) html += '<p class="fine">Alert feed as of ' + esc(when(asof)) + '.</p>';
      if (isNum(body.alerts_total) || isNum(body.alerts_1h)) {
        html += '<p class="fine">';
        if (isNum(body.alerts_total)) html += esc(String(body.alerts_total)) + ' alert' + (body.alerts_total === 1 ? '' : 's') + ' on record';
        if (isNum(body.alerts_1h)) html += (isNum(body.alerts_total) ? '; ' : '') + esc(String(body.alerts_1h)) + ' in the last hour';
        html += '.</p>';
      }
      var chains = body.chains && typeof body.chains === 'object' ? body.chains : null;
      if (chains) {
        var bits = [];
        Object.keys(chains).forEach(function (name) {
          var c = chains[name] || {};
          if (isNum(c.alerts_total)) bits.push(chainName(name) + ' ' + c.alerts_total);
        });
        if (bits.length) html += '<p class="fine">Alerts on record by chain: ' + esc(bits.join(' · ')) + '.</p>';
      }
      if (!rows.length) {
        html += '<p>' + (limit ? '' : 'No latest alert in this response.') + '</p>';
      } else {
        rows.forEach(function (row) {
          var kind = KIND_NAMES[String(row.kind || '').toLowerCase()] || (row.kind ? String(row.kind) : 'Move');
          var amount = isNum(row.usd) ? moneyShort(row.usd) : 'Amount not in this free feed';
          var exact = isNum(row.usd) ? moneyPair(row.usd).exact : '';
          html += '<div class="row"><div class="row-top"><strong>' + esc(kind) + '</strong>';
          html += '<span class="muted">' + esc(row._slot || chainName(row.chain)) + '</span>';
          html += '<span class="muted">' + esc(when(row.time)) + '</span></div>';
          html += '<p class="fine">' + esc(amount) + (exact ? (' (' + esc(exact) + ')') : '');
          if (row.asset) html += ' · asset ' + esc(row.asset);
          if (isNum(row.block)) html += ' · block ' + esc(String(row.block));
          html += '.</p></div>';
        });
      }
      root.innerHTML = html;
    }
    function renderTombs(pack) {
      var root = document.getElementById('cleared');
      var limit = limitMessage(pack);
      var body = pack && pack.body;
      if (!body || typeof body !== 'object') {
        root.innerHTML = '<p>' + esc(limit || 'No cleared / retired feed loaded.') + '</p>';
        return;
      }
      var all = tombstoneList(body);
      if (limit && !all.length) {
        root.innerHTML = '<p>' + esc(limit) + '</p>';
        return;
      }
      var hack = [];
      var other = 0;
      all.forEach(function (t) { if (isHackTomb(t)) hack.push(t); else other += 1; });
      var groups = {};
      var order = [];
      hack.forEach(function (t) {
        var key = [t.product || '', t.status || '', t.address || '', t.entity || '', t.reason || '', t.incident || ''].join('|');
        if (!groups[key]) { groups[key] = { row: t, n: 0 }; order.push(key); }
        groups[key].n += 1;
        var prev = groups[key].row.detected_at || '';
        if ((t.detected_at || '') > prev) groups[key].row = t;
      });
      order.sort(function (a, b) {
        return String(groups[b].row.detected_at || '').localeCompare(String(groups[a].row.detected_at || ''));
      });
      var html = '';
      if (limit) html += '<p>' + esc(limit) + '</p>';
      var asof = stampOf(body) || body.as_of;
      html += '<p class="fine">List as of ' + esc(when(asof)) + '.';
      if (isNum(body.count) || isNum(body.total_stored)) {
        html += ' This page of the feed has ' + esc(body.count != null ? String(body.count) : String(all.length)) + ' rows';
        if (isNum(body.total_stored)) html += ' (' + esc(String(body.total_stored)) + ' stored)';
        html += '.';
      }
      html += '</p>';
      html += '<p class="fine">' + esc(String(order.length)) + ' hack-related record' + (order.length === 1 ? '' : 's') + ' after combining repeats.';
      if (other) html += ' ' + other + ' other retired label' + (other === 1 ? '' : 's') + ' on this page ' + (other === 1 ? 'is' : 'are') + ' not a hack record and ' + (other === 1 ? 'is' : 'are') + ' not listed.';
      html += '</p>';
      if (!order.length) {
        html += '<p>No hack-related cleared or retired rows in this response.</p>';
      }
      var shown = order.slice(0, 40);
      shown.forEach(function (key) {
        var g = groups[key];
        var t = g.row;
        var status = String(t.status || 'record');
        var label = STATUS_NAMES[status] || status;
        html += '<div class="row"><div class="row-top">';
        html += '<span class="tag ' + esc(status) + '">' + esc(label) + '</span>';
        html += '<strong>' + esc(t.entity || t.incident || 'Record') + '</strong>';
        html += '<span class="muted">' + esc(when(t.detected_at)) + '</span></div>';
        html += '<p class="fine">';
        if (t.incident) html += 'Incident ' + esc(t.incident) + '. ';
        if (t.address) html += 'Address <span class="mono">' + esc(shortAddr(t.address)) + '</span>. ';
        if (Array.isArray(t.chains) && t.chains.length) html += esc(t.chains.map(chainName).join(', ')) + '. ';
        if (t.reason) html += esc(t.reason) + '.';
        html += '</p>';
        if (g.n > 1) html += '<p class="fine">The same record appears ' + g.n + ' times in this page of the feed. Time shown is the latest.</p>';
        html += '</div>';
      });
      if (order.length > shown.length) {
        html += '<p class="fine">Showing ' + shown.length + ' of ' + order.length + ' combined records.</p>';
      }
      root.innerHTML = html;
    }
    function incidentIds(tombs, incidents) {
      var set = {};
      (incidents || []).forEach(function (inc) { if (inc && inc.incident) set[String(inc.incident)] = true; });
      tombstoneList(tombs).forEach(function (t) { if (t && t.incident) set[String(t.incident)] = true; });
      return Object.keys(set).sort();
    }
    function renderLinks(ids) {
      var root = document.getElementById('incident-links');
      var current = '';
      try { current = (new URLSearchParams(window.location.search).get('incident') || '').trim(); } catch (e) { current = ''; }
      if (!current && state.tracker && state.tracker.body) {
        var parsed = parseIncidents(state.tracker.body);
        if (parsed[0] && parsed[0].incident) current = String(parsed[0].incident);
      }
      if (!ids.length) { root.innerHTML = ''; return; }
      root.innerHTML = ids.map(function (id) {
        var on = id === current;
        return '<a class="chip" href="/hacks?incident=' + encodeURIComponent(id) + '"' + (on ? ' aria-current="page"' : '') + '>' + esc(id) + '</a>';
      }).join('');
      var input = document.getElementById('incident-input');
      if (input && document.activeElement !== input) {
        var q = '';
        try { q = (new URLSearchParams(window.location.search).get('incident') || '').trim(); } catch (e2) { q = ''; }
        input.value = q;
      }
    }
    function render() {
      var trackerPack = state.tracker;
      var incidents = trackerPack ? parseIncidents(trackerPack.body) : [];
      renderIncidents(incidents, trackerPack);
      renderAlerts(state.alerts);
      renderTombs(state.tombs);
      var tombsBody = state.tombs && state.tombs.body;
      renderLinks(incidentIds(tombsBody, incidents));
      var bits = [];
      if (trackerPack && trackerPack.body) bits.push('Tracker response as of ' + when(stampOf(trackerPack.body)));
      if (state.alerts && state.alerts.body && !limitMessage(state.alerts)) bits.push('alerts as of ' + when(stampOf(state.alerts.body)));
      if (state.tombs && state.tombs.body && !limitMessage(state.tombs)) bits.push('cleared / retired list as of ' + when(stampOf(state.tombs.body)));
      var problems = [];
      ['tracker', 'alerts', 'tombs'].forEach(function (key) {
        var msg = limitMessage(state[key]);
        if (msg && state.notes[key]) problems.push(key);
      });
      var line = document.getElementById('status-line');
      if (!trackerPack) line.textContent = 'Loading public totals…';
      else if (bits.length) line.textContent = bits.join(' · ') + '.';
      else line.textContent = 'Feeds responded without an as-of time.';
      var checked = state.checkedAt ? ('Page checked at ' + when(state.checkedAt) + '.') : 'Page check: not yet.';
      if (serverCacheNote) checked += ' ' + serverCacheNote;
      document.getElementById('checked-at').textContent = checked;
      var bar = document.getElementById('status-bar');
      if (bar) bar.setAttribute('data-rendered', incidents.length || limitMessage(trackerPack) ? '1' : '0');
    }
    function take(key, pack) {
      var prev = state[key];
      var useful = false;
      if (key === 'tracker') useful = parseIncidents(pack.body).length > 0 && pack.status !== 429;
      else if (key === 'alerts') useful = !!(pack.body && (pack.body.latest_alert || pack.body.alerts || pack.body.chains || isNum(pack.body.alerts_total)) && pack.status !== 429 && !limitMessage(pack));
      else useful = tombstoneList(pack.body).length > 0 && pack.status !== 429 && !limitMessage(pack);
      if (useful || !prev) {
        state[key] = pack;
        if (useful) state.notes[key] = state.checkedAt;
      } else if (limitMessage(pack) || !pack.ok) {
        state[key] = pack;
        if (prev && prev.body) {
          state[key] = {
            ok: pack.ok, status: pack.status, body: prev.body, error: pack.error,
            staleDetail: limitMessage(pack) || pack.error
          };
          state.notes[key] = state.notes[key] || null;
        }
      } else {
        state[key] = pack;
      }
    }
    function loadJson(url) {
      return fetch(url, { headers: { 'Accept': 'application/json' } }).then(function (res) {
        return res.text().then(function (text) {
          var body = null;
          var error = null;
          try { body = text ? JSON.parse(text) : null; }
          catch (e) { error = 'Response was not JSON'; body = { detail: text ? text.slice(0, 240) : 'Empty response' }; }
          return { ok: res.ok, status: res.status, body: body, error: error };
        });
      }).catch(function (e) {
        return { ok: false, status: 0, body: null, error: (e && e.message) ? e.message : 'Network error' };
      });
    }
    function selectedIncident() {
      try {
        return (new URLSearchParams(window.location.search).get('incident') || '').trim();
      } catch (e) { return ''; }
    }
    function sectionPack(body, key, statusKey, errorKey) {
      var err = body[errorKey] || null;
      var status = isNum(body[statusKey]) ? body[statusKey] : (err ? 502 : 200);
      return { ok: status >= 200 && status < 300 && !err, status: status, body: body[key], error: err };
    }
    function refresh() {
      if (busy) return Promise.resolve();
      busy = true;
      var started = Date.now();
      var incident = selectedIncident();
      var q = incident ? ('?incident=' + encodeURIComponent(incident)) : '';
      return loadJson('/hacks/data' + q).then(function (pack) {
        state.checkedAt = new Date().toISOString();
        var body = pack.body;
        var combined = pack.ok && body && typeof body === 'object' && ('tracker' in body || 'alerts' in body || 'tombstones' in body);
        if (!combined) {
          serverCacheNote = '';
          take('tracker', pack);
          take('alerts', pack);
          take('tombs', pack);
        } else {
          serverCacheNote = isNum(body.cache_age_seconds) ? ('Server cache is ' + body.cache_age_seconds + 's old.') : '';
          take('tracker', sectionPack(body, 'tracker', 'tracker_status', 'tracker_error'));
          take('alerts', sectionPack(body, 'alerts', 'alerts_status', 'alerts_error'));
          take('tombs', sectionPack(body, 'tombstones', 'tombstones_status', 'tombstones_error'));
        }
        render();
      }).catch(function (e) {
        var line = document.getElementById('status-line');
        if (line) line.textContent = 'Could not read the feeds (' + ((e && e.message) || 'error') + '). The page will try again on the next refresh.';
      }).then(function () {
        busy = false;
        var wait = Math.max(0, REFRESH_MS - (Date.now() - started));
        nextAt = Date.now() + wait;
        if (timer) clearTimeout(timer);
        timer = setTimeout(refresh, wait);
        window.__hackTracker._timer = timer;
      });
    }
    function updateCountdown() {
      var el = document.getElementById('next-refresh');
      if (!el) return;
      if (!nextAt) { el.textContent = 'Refreshes every 60 seconds.'; return; }
      var s = Math.max(0, Math.round((nextAt - Date.now()) / 1000));
      el.textContent = 'Refreshes every 60 seconds. Next check in ' + s + 's.';
    }
    window.addEventListener('error', function (e) {
      var n = document.getElementById('js-errors');
      if (n) n.textContent += (n.textContent ? ' ' : '') + (e.message || 'Script error');
    });
    window.addEventListener('unhandledrejection', function (e) {
      var n = document.getElementById('js-errors');
      var msg = (e.reason && e.reason.message) ? e.reason.message : 'Unhandled promise error';
      if (n) n.textContent += (n.textContent ? ' ' : '') + msg;
    });

    window.__hackTracker = {
      moneyShort: moneyShort,
      moneyExact: moneyExact,
      staleNotes: staleNotes,
      parseIncidents: parseIncidents,
      alertRows: alertRows,
      tombstoneList: tombstoneList,
      isHackTomb: isHackTomb,
      limitMessage: limitMessage,
      when: when,
      refresh: refresh,
      render: render,
      state: state
    };
    window.__hackTracker.boot = refresh();
    if (tick) clearInterval(tick);
    tick = setInterval(updateCountdown, 1000);
  })();
  </script>
</body>
</html>
"""


_HACKS_DATA_TTL_SECONDS = 60.0
_HACKS_DATA_PER_IP_PER_MINUTE = 60
_INJECTED_DEFAULT_TYPES = {"Query", "Depends", "FieldInfo", "Path", "Param", "Header", "Cookie", "Body", "Form", "File"}
_UNDEFINED_DEFAULT_TYPES = {"PydanticUndefinedType", "UndefinedType", "EllipsisType"}


def hacks_data_client_ip(request: Any) -> str:
    """Client IP for the page-data limiter. First X-Forwarded-For hop, else the socket peer."""
    forwarded = ""
    headers = getattr(request, "headers", None)
    if headers is not None:
        getter = getattr(headers, "get", None)
        if getter is not None:
            forwarded = getter("x-forwarded-for", "") or ""
    if forwarded:
        return forwarded.split(",")[0].strip() or "unknown"
    client = getattr(request, "client", None)
    host = getattr(client, "host", None) if client is not None else None
    return host or "unknown"


def hacks_incident_key(raw: Optional[str]) -> str:
    """Normalize `?incident=`. Empty means the feed's own default."""
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    if len(text) > 80 or any(not (ch.isalnum() or ch in "._-") for ch in text):
        raise ValueError("Incident id must be 1-80 characters: letters, digits, dot, underscore, or hyphen.")
    return text


def _is_injected_default(default: Any) -> bool:
    if default is inspect.Parameter.empty:
        return False
    name = default.__class__.__name__
    module = getattr(default.__class__, "__module__", "") or ""
    if name in _INJECTED_DEFAULT_TYPES:
        return True
    return module.startswith("fastapi") or module.startswith("pydantic")


def _plain_default(param: inspect.Parameter) -> Any:
    """Value to pass when FastAPI would have filled a Query()/Depends() default."""
    default = param.default
    if not _is_injected_default(default):
        return inspect.Parameter.empty
    inner = getattr(default, "default", inspect.Parameter.empty)
    if inner is None or isinstance(inner, (str, int, float, bool)):
        return inner
    if getattr(inner, "__class__", type).__name__ in _UNDEFINED_DEFAULT_TYPES or inner is Ellipsis:
        return inspect.Parameter.empty
    return inspect.Parameter.empty


def _hacks_internal_request() -> Any:
    """Request object for handlers that require one. Path is /hacks/data, not a free route."""
    from starlette.requests import Request

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/hacks/data",
        "raw_path": b"/hacks/data",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 0),
        "server": ("127.0.0.1", 80),
    }
    return Request(scope)


def _get_route_endpoint(app: Any, path: str) -> Any:
    for route in getattr(app, "routes", None) or []:
        methods = getattr(route, "methods", None) or set()
        if getattr(route, "path", None) == path and "GET" in methods:
            endpoint = getattr(route, "endpoint", None)
            if endpoint is not None:
                return endpoint
    return None


def _unpack_feed_result(result: Any) -> Tuple[int, Any, Optional[str]]:
    if isinstance(result, (dict, list)):
        return 200, result, None
    status = getattr(result, "status_code", None)
    body = getattr(result, "body", None)
    if isinstance(status, int) and body is not None and not isinstance(result, (dict, list)):
        if isinstance(body, str):
            raw = body
        else:
            raw = bytes(body).decode("utf-8")
        parsed = json.loads(raw or "null")
        return status, parsed, None
    if hasattr(result, "model_dump"):
        return 200, result.model_dump(), None
    return 502, None, "Feed returned " + type(result).__name__


async def _invoke_endpoint(endpoint: Any, explicit: Dict[str, Any]) -> Tuple[int, Any, Optional[str]]:
    """Call a route function in-process. Does not go through ASGI or the free-tier middleware."""
    fn = inspect.unwrap(endpoint)
    kwargs: Dict[str, Any] = {}
    for name, param in inspect.signature(fn).parameters.items():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        if name in explicit and explicit[name] is not None:
            kwargs[name] = explicit[name]
            continue
        if name == "request" and param.default is inspect.Parameter.empty:
            kwargs[name] = _hacks_internal_request()
            continue
        if _is_injected_default(param.default):
            plain = _plain_default(param)
            if plain is not inspect.Parameter.empty:
                kwargs[name] = plain
            elif name in {"address", "chain", "product", "status", "since", "incident"}:
                kwargs[name] = None
    result = fn(**kwargs)
    if inspect.isawaitable(result):
        result = await result
    return _unpack_feed_result(result)


async def _call_feed(app: Any, path: str, explicit: Dict[str, Any]) -> Tuple[int, Any, Optional[str]]:
    endpoint = _get_route_endpoint(app, path)
    if endpoint is None:
        return 502, None, path + " is not registered"
    try:
        return await _invoke_endpoint(endpoint, explicit)
    except Exception as exc:
        return 502, None, str(exc)[:300]


async def _assemble_hacks_data(app: Any, incident_key: str) -> Dict[str, Any]:
    """Same three payloads the free routes return, built by calling those functions directly."""
    params = {"incident": incident_key} if incident_key else {}
    tracker_status, tracker, tracker_error = await _call_feed(app, "/v1/free/hack-tracker", params)
    alerts_status, alerts, alerts_error = await _call_feed(app, "/v1/free/hack-tracker/alerts", params)
    tomb_status, tombs, tomb_error = await _call_feed(app, "/v1/tombstones", {})
    out: Dict[str, Any] = {
        "tracker": tracker,
        "tracker_status": tracker_status,
        "alerts": alerts,
        "alerts_status": alerts_status,
        "tombstones": tombs,
        "tombstones_status": tomb_status,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if tracker_error:
        out["tracker_error"] = tracker_error
    if alerts_error:
        out["alerts_error"] = alerts_error
    if tomb_error:
        out["tombstones_error"] = tomb_error
    return out


class HacksDataGate:
    """60s in-memory cache plus a sliding 60/minute per-IP ceiling. Not the free-tier counter."""

    def __init__(self, ttl: float = _HACKS_DATA_TTL_SECONDS, per_minute: int = _HACKS_DATA_PER_IP_PER_MINUTE) -> None:
        self.ttl = ttl
        self.per_minute = per_minute
        self._lock = threading.Lock()
        self._cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._hits: Dict[str, List[float]] = {}
        self._inflight: Dict[str, Any] = {}

    def allow(self, ip: str, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            recent = [stamp for stamp in self._hits.get(ip, []) if now - stamp < 60.0]
            if len(recent) >= self.per_minute:
                self._hits[ip] = recent
                return False
            recent.append(now)
            self._hits[ip] = recent
            if len(self._hits) > 5000:
                self._hits = {key: stamps for key, stamps in self._hits.items() if stamps and now - stamps[-1] < 60.0}
            return True

    def cached(self, key: str, now: Optional[float] = None) -> Optional[Tuple[Dict[str, Any], float]]:
        now = time.monotonic() if now is None else now
        with self._lock:
            row = self._cache.get(key)
            if not row:
                return None
            stored_at, payload = row
            age = now - stored_at
            if age >= self.ttl:
                return None
            return payload, age

    def store(self, key: str, payload: Dict[str, Any], now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._cache[key] = (now, payload)
            if len(self._cache) > 32:
                oldest = sorted(self._cache.items(), key=lambda item: item[1][0])[: len(self._cache) - 32]
                for cache_key, _row in oldest:
                    self._cache.pop(cache_key, None)

    async def get_or_build(self, key: str, builder: Any) -> Tuple[Dict[str, Any], float]:
        hit = self.cached(key)
        if hit is not None:
            return hit
        loop = asyncio.get_running_loop()
        with self._lock:
            future = self._inflight.get(key)
            if future is None:
                future = loop.create_future()
                self._inflight[key] = future
                leader = True
            else:
                leader = False
        if not leader:
            payload = await future
            fresh = self.cached(key)
            return fresh if fresh is not None else (payload, 0.0)
        try:
            payload = await builder()
            self.store(key, payload)
            if not future.done():
                future.set_result(payload)
            return payload, 0.0
        except Exception as exc:
            if not future.done():
                future.set_exception(exc)
            raise
        finally:
            with self._lock:
                self._inflight.pop(key, None)


_HACKS_DATA_GATE = HacksDataGate()


def create_gateway_app(
    config: Optional[PaymentConfig] = None,
    ledger: Optional[RevenueLedger] = None,
    scraper: Optional[SmartScraper] = None,
    marketplace: Optional[DatasetMarketplace] = None,
    vault_dir: Optional[str | Path] = None,
    facilitator: Optional[CdpFacilitator] = None,
) -> FastAPI:
    """Create the plug-and-play FastAPI ASGI application for x402 AI Agents and O: Drive Data Selling."""
    cfg = config or PaymentConfig()
    ldg = ledger or RevenueLedger(cfg)
    scr = scraper or SmartScraper(cfg, ldg)
    mkt = marketplace or DatasetMarketplace(cfg, ldg)
    verifier = X402PaymentVerifier(cfg, ldg)
    mppd = MppDoor(cfg.receiving_address)
    try:
        _trial.ensure_seeded()
    except Exception as _e:  # never block startup
        print('trial codes init failed:', _e)
    fac = facilitator or CdpFacilitator()
    harvester = AutonomousHarvester(cfg, ldg, scr, mkt, vault_dir=vault_dir)
    sales_engine = SalesOutreachEngine(cfg, mkt, harvester)

    app = FastAPI(
        title=SERVICE_NAME,
        version="0.5.1",
        description=(
            "Pay-per-call crypto prices, DeFi yields and TVL, Polymarket odds, Base token scam checks, security "
            "risk-check ($0.0045, 3 free/IP/day), stock perpetual futures / HIP-3 ($0.003), stock/ETF snapshots and "
            "clean web-page text for AI agents. Free endpoints under /v1/free; single lookups from $0.001 "
            "(live cache refreshed about every 30 min, typical response under 100 ms); whole-market bundle "
            "/v1/bundle/market-pulse $0.001; page-to-clean-text /v1/scrape/text $0.003; full scrapes $0.001-$0.025 per "
            "page; dataset listings $0.002. Paid in USDC via x402 v2 on Base, Polygon, Arbitrum or Solana; no account "
            "needed. No charge on failure: payment settles only after valid data is ready. "
            f"EVM payments settle to {cfg.receiving_address}; Solana to {SOLANA_PAY_TO}."
        ),
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )

    from .as_of import AsOfStamp  # asof-20261029: data_as_of time + block on every /v1 JSON response (inner to cache)
    app.add_middleware(AsOfStamp)

    from .response_cache import ResponseCache  # cache-datasets-20261023: free/discovery GET cache (inner to CORS)
    app.add_middleware(ResponseCache)

    class _HeadAsGet:  # conversion-20261025: HEAD on any GET route returns the GET status + headers (402 + PAYMENT-REQUIRED), never settles
        _STRIP = {b"payment-signature", b"x-payment", b"x-tx-hash", b"authorization"}

        def __init__(self, app_):
            self.app = app_

        async def __call__(self, scope, receive, send):
            if scope.get("type") != "http" or scope.get("method") != "HEAD":
                return await self.app(scope, receive, send)
            scope = dict(scope)
            scope["method"] = "GET"
            scope["headers"] = [(k, v) for k, v in scope.get("headers", []) if k.lower() not in self._STRIP]
            done = {"body": False}

            async def _send(msg):
                if msg["type"] == "http.response.body":
                    if done["body"]:
                        return
                    done["body"] = True
                    return await send({"type": "http.response.body", "body": b"", "more_body": False})
                return await send(msg)
            return await self.app(scope, receive, _send)

    app.add_middleware(_HeadAsGet)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["PAYMENT-REQUIRED", "PAYMENT-RESPONSE", "X-Payment-Required", "X-Payment-Response", "WWW-Authenticate", "Payment-Receipt"],
    )

    # Missed-demand radar: logs misses / 404s / unpaid 402s to /collected_data/demand_radar.db
    from .demand_radar import install_demand_radar  # noqa: E402
    install_demand_radar(app)
    try:  # upd38 usage log + free-tier daily limits (outermost)
        from .agent_api import FREE_ENDPOINTS as _FE
    except Exception:
        _FE = {}
    from . import usage as _usage
    _usage.install(app, free_endpoints=_FE)
    try:  # daily totals: in-memory, flushed to sqlite; never blocks a response on failure
        from . import usage_daily as _usage_daily
        _usage_daily.install(app)
    except Exception as _e:
        print("usage daily init failed:", _e)

    # upd19: passive discovery signals (sitemap, IndexNow, pulse feed, updated_at)
    try:
        from .discovery import register as _reg_discovery  # noqa: E402
        _reg_discovery(app, mkt=mkt)
    except Exception as _e:  # never block startup
        print("discovery init failed:", _e)
    try:  # upd24: Agent Skill
        from .skills import register as _reg_skills  # noqa: E402
        _reg_skills(app)
    except Exception as _e:
        print("skills init failed:", _e)

    app.state.payment_config = cfg
    app.state.ledger = ldg
    app.state.scraper = scr
    app.state.marketplace = mkt
    app.state.verifier = verifier
    app.state.harvester = harvester
    app.state.sales_engine = sales_engine

    # Live auto-refreshing datasets from free public APIs (crypto, DeFi, prediction markets, stocks)
    from .live_feeds import maybe_start as _start_live_feeds  # noqa: E402
    app.state.live_feeds = _start_live_feeds(mkt)

    # Optional 24/7 background autopilot loop when SCRAPLING_AUTOPILOT_ENABLED=1
    if environ.get("SCRAPLING_AUTOPILOT_ENABLED", "").lower() in ("1", "true", "yes"):
        interval_sec = int(environ.get("SCRAPLING_AUTOPILOT_INTERVAL_SECONDS", "900"))

        def _autopilot_loop() -> None:
            while True:
                try:
                    harvester.run_default_harvest()
                except Exception:
                    pass
                time.sleep(max(60, interval_sec))

        t = threading.Thread(target=_autopilot_loop, daemon=True, name="scrapling-odrive-autopilot")
        t.start()

    @app.get("/test-buy", response_class=HTMLResponse, include_in_schema=False)
    async def test_buy_page() -> HTMLResponse:
        from .test_buy_page import TEST_BUY_HTML

        return HTMLResponse(content=TEST_BUY_HTML)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def storefront_home() -> HTMLResponse:
        return HTMLResponse(_build_storefront_html(cfg))

    @app.get("/hacks", response_class=HTMLResponse, include_in_schema=False)
    async def hacks_tracker_page() -> HTMLResponse:
        return HTMLResponse(hacks_tracker_page_html())

    @app.get("/hacks/data", include_in_schema=False)
    async def hacks_tracker_data(request: Request, incident: Optional[str] = None) -> JSONResponse:
        """Combined hack-tracker JSON for the human page.

        Calls the three feed functions in-process (not over HTTP), so the free-tier
        per-IP counter does not run. One shared copy is reused for 60 seconds.
        """
        try:
            key = hacks_incident_key(incident)
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"detail": str(exc)}, headers={"Cache-Control": "no-store"})
        if not _HACKS_DATA_GATE.allow(hacks_data_client_ip(request)):
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests for this page (about 60 per minute per IP). Wait a moment and reload."},
                headers={"Cache-Control": "no-store", "Retry-After": "60"},
            )
        try:
            payload, age = await _HACKS_DATA_GATE.get_or_build(key, lambda: _assemble_hacks_data(app, key))
        except Exception as exc:
            return JSONResponse(
                status_code=502,
                content={"detail": "Hack tracker data is temporarily unavailable.", "error": str(exc)[:300]},
                headers={"Cache-Control": "no-store"},
            )
        body = dict(payload)
        body["cache_age_seconds"] = max(0, int(age))
        return JSONResponse(content=body, headers={"Cache-Control": "no-store"})

    def _legal_page(title: str, body: str) -> str:
        return (
            "<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1.0'>"
            f"<title>{title} - Scrapling x402 Data Store</title><link rel='icon' type='image/png' href='/logo.png'>"
            "<style>body{font-family:-apple-system,'Segoe UI',Roboto,sans-serif;background:#0b0f19;color:#f1f5f9;"
            "line-height:1.6;margin:0;padding:40px 20px}main{max-width:760px;margin:0 auto;background:#131b2e;"
            "border:1px solid #23304d;border-radius:12px;padding:28px}h1{color:#10b981}h2{font-size:18px;margin-top:22px}"
            "a{color:#10b981}p,li{color:#cbd5e1}</style></head><body><main>"
            f"<p><a href='/'>&larr; Scrapling x402 Data Store</a></p><h1>{title}</h1>{body}"
            "<p style='margin-top:28px;color:#94a3b8'>Last updated: October 24, 2026</p></main></body></html>"
        )

    _TERMS_BODY = (
        "<h2>Data as-is</h2><p>All data is provided as-is, without warranties of accuracy, completeness or availability.</p>"
        "<h2>No financial advice</h2><p>Nothing here is financial, investment or legal advice. Do your own research.</p>"
        "<h2>Payments</h2><p>Payments are made in USDC via x402 or MPP. All payments are final and non-refundable.</p>"
        "<h2>Fair use</h2><p>Don't abuse, overload or attack the service, or use it for anything illegal. We may limit or block abusive traffic.</p>"
        "<h2>Contact</h2><p><a href='mailto:scraplingdata@mail.grokbot.com'>scraplingdata@mail.grokbot.com</a></p>"
    )
    _PRIVACY_BODY = (
        "<h2>What we collect</h2><p>We don't collect personal data such as names or accounts.</p>"
        "<h2>Logs</h2><p>For operations and security we log IP addresses, request metadata (time, path, user agent) "
        "and wallet addresses from payments.</p>"
        "<h2>No selling</h2><p>We never sell or share your data.</p>"
        "<h2>Contact</h2><p><a href='mailto:scraplingdata@mail.grokbot.com'>scraplingdata@mail.grokbot.com</a></p>"
    )

    @app.get("/terms", response_class=HTMLResponse, include_in_schema=False)
    async def terms_page() -> HTMLResponse:
        return HTMLResponse(_legal_page("Terms of Service", _TERMS_BODY))

    @app.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
    async def privacy_page() -> HTMLResponse:
        return HTMLResponse(_legal_page("Privacy Policy", _PRIVACY_BODY))

    @app.get("/.well-known/x402")
    @app.get("/.well-known/x402.json")
    async def x402_discovery_manifest(request: Request) -> Dict[str, Any]:
        """x402 discovery manifest (x402scan `version`/`resources` fan-out plus store metadata)."""
        base = _public_base_url(request)
        manifest: Dict[str, Any] = {
            "version": 1,
            "resources": _paid_resource_urls(base),
            "x402Version": 2,
            "serviceName": SERVICE_NAME,
            "description": app.description,
            "icon": f"{base}/logo.png",
            "logo": f"{base}/logo.png",
            "receivingAddress": cfg.receiving_address,
            "primaryNetwork": cfg.primary_network,
            "supportedNetworks": [
                {
                    "caip2": net.caip2_id,
                    "name": net.name,
                    "chainId": net.chain_id,
                    "usdcContract": net.usdc_address,
                    "nativeSymbol": net.native_symbol,
                    "payTo": cfg.receiving_address,
                }
                for net in SUPPORTED_EVM_NETWORKS.values()
            ],
            "pricingTiers": {
                tid: {
                    "name": t.name,
                    "priceUsd": t.price_usd,
                    "usdcAtomicUnits": cfg.usdc_atomic_units(t.price_usd),
                    "description": _TIER_DESC.get(tid, t.description),
                }
                for tid, t in cfg.pricing_tiers.items()
            },
            "availableDatasets": [
                {
                    "dataset_id": ds["dataset_id"],
                    "title": ds["title"],
                    "category": ds["category"],
                    "price_usd": ds["price_usd"],
                    "record_count": ds["record_count"],
                    "buy_url": f"/v1/datasets/{ds['dataset_id']}/buy",
                }
                for ds in mkt.list_datasets()
            ],
            "endpoints": {
                "scrape": "/v1/scrape",
                "scrapeText": "/v1/scrape/text",
                "marketPulse": "/v1/bundle/market-pulse",
                "batchScrape": "/v1/batch-scrape",
                "datasets": "/v1/datasets",
                "depositCredits": "/v1/credits/deposit",
                "mcp": f"{base}/mcp",
            },
            "freeEndpoints": [
                {"url": f"{base}{p}", "description": d, "free": True} for p, d in FREE_ENDPOINTS.items()
            ],
            "paidLookups": _paid_lookups_with_quality(base, LOOKUPS, cfg),
            "paidDatasets": [
                {"url": f"{base}{listing_path(k)}", "priceUsd": v["price"],
                 "usdcAtomicUnits": cfg.usdc_atomic_units(v["price"]), "title": v["title"], "description": v["desc"],
                 "networks": ["eip155:8453", "eip155:137", "eip155:42161", SOLANA_MAINNET]}
                for k, v in DATASET_LISTINGS.items()
            ],
            "mcp": {"url": f"{base}/mcp", "transport": "streamable-http", "serverCard": f"{base}/mcp/server-card"},
        }
        try:
            from .x402 import session_offers, session_offers_enabled
            if session_offers_enabled():
                manifest["sessionOffers"] = session_offers(base)
                manifest["endpoints"]["dayPass"] = "/v1/pass/day"
                manifest["endpoints"]["monthPass"] = "/v1/pass/month"
        except Exception:
            pass
        proofs = _ownership_proofs()
        if proofs:
            manifest["ownershipProofs"] = proofs
        return manifest

    @app.get("/.well-known/mcp-registry-auth", response_class=PlainTextResponse)
    async def mcp_registry_auth() -> str:
        return "v=MCPv1; k=ed25519; p=h3ax+AHrXLB6s6QPmIvZFtoFzf3mkUvwnMalMWYDXl4="


    @app.get("/.well-known/ai-plugin.json")
    async def ai_plugin_manifest(request: Request) -> Dict[str, Any]:
        base_url = _public_base_url(request)
        return {
            "schema_version": "v1",
            "name_for_human": SERVICE_NAME,
            "name_for_model": "scrapling_x402_intelligence",
            "description_for_human": "Crypto prices, DeFi yields, Polymarket odds, security risk-check, stock perps (HIP-3), token scam checks and web scraping.",
            "description_for_model": app.description,
            "logo_url": f"{base_url}/logo.png",
            "auth": {"type": "x402_or_bearer", "receiving_wallet": cfg.receiving_address},
            "api": {"type": "openapi", "url": f"{base_url}/openapi.json"},
        }

    @app.get("/llms.txt", response_class=PlainTextResponse)
    async def llms_txt(request: Request) -> str:
        base = _public_base_url(request)
        body = build_llms_txt(base, cfg.receiving_address, mkt.list_datasets())
        try:
            from .x402 import session_offers_enabled, session_offers_llms_section
            if session_offers_enabled():
                body = body.rstrip() + "\n\n" + session_offers_llms_section(base)
        except Exception:
            pass
        return body

    def _gate_or_settle(
        request: Request,
        resource: str,
        required_usd: float,
        tier_id: str,
        description: Optional[str] = None,
        bazaar: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Optional[JSONResponse], Optional[Dict[str, Any]]]:
        """Verify payment or return an HTTP 402 `JSONResponse` with x402 V2 headers."""
        if not cfg.enforce_payments:
            return None, {
                "payment_id": "unmetered_dev",
                "recipient_address": cfg.receiving_address,
                "amount_usd": 0.0,
                "settlement_scheme": "unmetered",
            }
        _acp_tok = environ.get("ACP_INTERNAL_TOKEN", "")
        _acp_hdr = request.headers.get("x-acp-internal", "")
        if len(_acp_tok) >= 32 and _acp_hdr and hmac.compare_digest(_acp_hdr.encode(), _acp_tok.encode()):
            return None, {
                "payment_id": "acp-internal",
                "recipient_address": cfg.receiving_address,
                "amount_usd": 0.0,
                "settlement_scheme": "acp-internal",
            }
        _pp = _passes.check(request, resource, tier_id, required_usd,
                            lambda m: _payment_required(request, resource, required_usd, tier_id, description, bazaar, m))
        if isinstance(_pp, dict):
            _pp["recipient_address"] = cfg.receiving_address
            return None, _pp
        if _pp is not None:
            return _pp, None
        _tc = _trial.code_from_request(request)
        if _tc and tier_id not in _trial.PAID_ONLY_TIERS:
            if _trial.has_calls(_tc):
                return None, {
                    "payment_id": f"trial:{_tc}",
                    "recipient_address": cfg.receiving_address,
                    "amount_usd": 0.0,
                    "settlement_scheme": "trial-code",
                    "_pending_trial": {"code": _tc, "resource": resource, "tier_id": tier_id,
                                       "description": description, "bazaar": bazaar, "usd": required_usd},
                }
            return _payment_required(request, resource, required_usd, tier_id, description, bazaar,
                                     "Trial code is invalid or has no calls left."), None
        _auth = request.headers.get("authorization", "")
        if MppDoor.is_mpp(_auth):
            ok, info, err = mppd.verify(_auth, required_usd, description, http_request=request)
            if not ok:
                return _payment_required(request, resource, required_usd, tier_id, description, bazaar, err), None
            st = {"payment_id": f"mpp:{info['tx_hash']}", "recipient_address": cfg.receiving_address,
                  "amount_usd": required_usd,
                  "settlement_scheme": info.get("settlement_scheme") or "mpp-tempo",
                  "network": "eip155:4217",
                  "tx_hash": info["tx_hash"], "payer_address": info["payer"], "payer": info["payer"],
                  "mpp_intent": info.get("intent") or "charge"}
            try:
                rec = ldg.record_payment(tx_hash=info["tx_hash"] or f"mpp:{time.time()}", network="eip155:4217",
                                         payer_address=info["payer"], amount_usd=required_usd, resource=resource,
                                         asset="USDC.e",
                                         settlement_scheme=info.get("settlement_scheme") or "mpp-tempo")
                st["payment_id"] = rec["payment_id"]
            except Exception:
                pass
            return None, st
        exact_data = _decode_exact_payload(request)
        if exact_data is not None:
            return _verify_exact(request, exact_data, resource, required_usd, tier_id, description, bazaar)
        is_paid, settlement, err_reason = verifier.verify_request_payment(
            headers=request.headers,
            resource=resource,
            required_usd=required_usd,
        )
        if not is_paid or settlement is None:
            return _payment_required(request, resource, required_usd, tier_id, description, bazaar, err_reason), None
        return None, settlement

    def _decode_exact_payload(request: Request) -> Optional[Dict[str, Any]]:
        raw = request.headers.get("payment-signature") or request.headers.get("x-payment")
        if not raw:
            return None
        try:
            data = json_loads(raw.encode("utf-8") if raw.lstrip().startswith("{") else b64decode(raw))
        except Exception:
            return None
        return data if is_exact_evm_payload(data) else None

    def _exact_requirements(required_usd: float, network: str = EXACT_NETWORK) -> Dict[str, Any]:
        if network == SOLANA_MAINNET:
            return {
                "scheme": "exact",
                "network": network,
                "asset": SOLANA_USDC_MINT,
                "amount": cfg.usdc_atomic_units(required_usd),
                "payTo": SOLANA_PAY_TO,
                "maxTimeoutSeconds": 300,
                "extra": {"feePayer": SOLANA_FEE_PAYER},
            }
        net = SUPPORTED_EVM_NETWORKS[network]
        return {
            "scheme": "exact",
            "network": network,
            "asset": net.usdc_address,
            "amount": cfg.usdc_atomic_units(required_usd, network),
            "payTo": cfg.receiving_address,
            "maxTimeoutSeconds": 300,
            "extra": {"name": "USD Coin", "version": "2"},
        }

    def _verify_exact(
        request: Request,
        data: Dict[str, Any],
        resource: str,
        required_usd: float,
        tier_id: str,
        description: Optional[str],
        bazaar: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[JSONResponse], Optional[Dict[str, Any]]]:
        """Standard x402 v2 `exact` (EIP-3009) payment: verify now via the CDP facilitator, settle after serving."""

        def _reject(reason: str) -> Tuple[Optional[JSONResponse], None]:
            return _payment_required(request, resource, required_usd, tier_id, description, bazaar, reason), None

        if not fac.enabled:
            return _reject("x402 `exact` payments are not enabled on this server (no CDP facilitator keys).")
        if data.get("x402Version") != 2:
            return _reject("Only x402 v2 `exact` payment payloads are supported.")
        accepted = data.get("accepted") or {}
        if accepted.get("network") not in fac.exact_networks:
            return _reject(f"x402 `exact` is only accepted on: {', '.join(fac.exact_networks)}.")
        reqs = _exact_requirements(required_usd, accepted["network"])
        is_sol = reqs["network"] == SOLANA_MAINNET
        auth = {} if is_sol else data["payload"]["authorization"]
        _n = (lambda v: str(v)) if is_sol else (lambda v: str(v).lower())  # base58 is case-sensitive
        if (
            accepted.get("network") != reqs["network"]
            or _n(accepted.get("asset", "")) != _n(reqs["asset"])
            or _n(accepted.get("payTo", "")) != _n(reqs["payTo"])
            or str(accepted.get("amount")) != reqs["amount"]
            or (not is_sol and _n(auth.get("to", "")) != _n(reqs["payTo"]))
        ):
            return _reject("Payment does not match this route's requirements (network, asset, payTo or amount).")
        payment_payload = dict(data)
        payment_payload.setdefault(
            "resource",
            {"url": _public_base_url(request) + request.url.path, "description": description or tier_id,
             "mimeType": "application/json"},
        )
        _res = payment_payload.get("resource")
        if isinstance(_res, dict) and len(str(_res.get("description") or "")) > 500:
            payment_payload["resource"] = {**_res, "description": _cdp_desc(_res.get("description"))}
        if bazaar and not (payment_payload.get("extensions") or {}).get("bazaar"):
            payment_payload["extensions"] = {**(payment_payload.get("extensions") or {}), "bazaar": bazaar}
        try:
            result = fac.verify(payment_payload, reqs)
        except FacilitatorError as err:
            return _reject(str(err))
        if not result.get("isValid"):
            return _reject(f"Payment verification failed: {result.get('invalidReason') or 'invalid'}")
        return None, {
            "settlement_scheme": "x402-exact-cdp",
            "network": reqs["network"],
            "payer_address": result.get("payer") or auth.get("from"),
            "recipient_address": reqs["payTo"],
            "amount_usd": required_usd,
            "_pending_exact": {"payload": payment_payload, "requirements": reqs, "resource": resource,
                               "tier_id": tier_id, "description": description, "bazaar": bazaar},
        }

    def _settle_pending(request: Request, settlement: Optional[Dict[str, Any]]) -> Optional[JSONResponse]:
        """Settle a verified `exact` payment after the handler succeeded. Returns a 402 response on failure."""
        if settlement and "_pending_trial" in settlement:
            pt = settlement.pop("_pending_trial")
            left = _trial.consume(pt["code"], pt["resource"])
            if left is None:
                return _payment_required(request, pt["resource"], pt["usd"], pt["tier_id"], pt["description"],
                                         pt["bazaar"], "Trial code is invalid or has no calls left.")
            settlement["trial_calls_left"] = left
            return None
        if not settlement or "_pending_exact" not in settlement:
            return None
        pend = settlement.pop("_pending_exact")
        try:
            res = fac.settle(pend["payload"], pend["requirements"])
        except FacilitatorError as err:
            res = {"success": False, "errorReason": str(err)}
        if not res.get("success"):
            return _payment_required(request, pend["resource"], settlement["amount_usd"], pend["tier_id"],
                                     pend["description"], pend["bazaar"],
                                     f"Payment settlement failed: {res.get('errorReason') or 'unknown'}")
        tx = str(res.get("transaction") or "")
        payer = res.get("payer") or settlement.get("payer_address") or "0xUnknown"
        settlement.update({"tx_hash": tx, "payer_address": payer, "network": res.get("network") or pend["requirements"]["network"],
                           "payer": payer, "transaction": tx})
        try:
            receipt = ldg.record_payment(
                tx_hash=tx or f"cdp:{(pend['payload']['payload'].get('authorization') or {}).get('nonce') or pend['payload']['payload'].get('transaction', '')[:64]}",
                network=settlement["network"],
                payer_address=payer,
                amount_usd=settlement["amount_usd"],
                resource=pend["resource"],
                asset="USDC",
                settlement_scheme="x402-exact-cdp",
            )
            settlement["payment_id"] = receipt["payment_id"]
        except ValueError:
            pass  # already recorded; funds are settled, so still serve
        return None

    _SCRAPE_USAGE = {
        "required_params": {"url": "absolute http(s) URL of the page to scrape"},
        "optional_params": {"tier": "auto | cached_intel | static_scrape | stealth_browser",
                            "css_selector": "optional CSS selector"},
        "example_get": "GET /v1/scrape?url=https://example.com&tier=static_scrape",
        "example_post": {"method": "POST", "path": "/v1/scrape", "body": {"url": "https://example.com", "tier": "static_scrape"}},
        "text_only": "GET /v1/scrape/text?url=https://example.com ($0.003)",
        "how_to_pay": "Retry the same request with a PAYMENT-SIGNATURE (x402 v2) or X-PAYMENT (v1) header built from `accepts`.",
        "no_charge_on_failure": True,
    }

    def _payment_required(
        request: Request,
        resource: str,
        required_usd: float,
        tier_id: str,
        description: Optional[str],
        bazaar: Optional[Dict[str, Any]],
        err_reason: Optional[str],
    ) -> JSONResponse:
        headers_402, body_402 = verifier.build_402_headers(
            resource=resource,
            amount_usd=required_usd,
            tier_id=tier_id,
            description=_cdp_desc(description) if description else description,
            resource_url=_public_base_url(request) + request.url.path,
            bazaar=bazaar,
            exact_networks=fac.exact_networks if fac.enabled else None,
        )
        body_402["verificationError"] = err_reason
        _payment_required_body_extras(body_402, request, bazaar, required_usd)
        # Keep PAYMENT-REQUIRED header in sync with body extensions (sessionOffers).
        try:
            from base64 import b64encode
            from scrapling.core._compat import json_dumps_bytes
            _std = ("scheme", "network", "amount", "asset", "payTo", "maxTimeoutSeconds")
            lean = {
                "x402Version": 2,
                "error": body_402.get("error"),
                "resource": body_402.get("resource"),
                "accepts": [
                    {
                        **{k: acc[k] for k in _std if k in acc},
                        **({"extra": {k: acc["extra"][k] for k in ("name", "version", "feePayer") if k in acc.get("extra", {})}}
                           if isinstance(acc.get("extra"), dict) else {}),
                    }
                    for acc in body_402.get("accepts", [])
                ],
            }
            if body_402.get("extensions"):
                lean["extensions"] = body_402["extensions"]
            lean["receivingAddress"] = body_402.get("receivingAddress")
            lean["priceUsd"] = body_402.get("priceUsd")
            headers_402 = dict(headers_402)
            headers_402["PAYMENT-REQUIRED"] = b64encode(json_dumps_bytes(lean)).decode("ascii")
        except Exception:
            pass
        _www = mppd.challenge_header(required_usd, description, http_request=request)
        if _www:
            headers_402 = dict(headers_402)
            headers_402["WWW-Authenticate"] = _www
            body_402["mpp"] = {"note": mppd.challenge_note()}
        if tier_id not in _trial.PAID_ONLY_TIERS:
            body_402["trial"] = _trial.NOTE
        if resource.startswith(("scrape:probe", "scrape_text:probe")):
            u = dict(_SCRAPE_USAGE)
            if resource.startswith("scrape_text"):
                u["example_get"] = "GET /v1/scrape/text?url=https://example.com"
                u.pop("example_post", None); u["optional_params"] = {}
                u["price_usd"] = 0.003
            else:
                try:
                    u["price_usd_by_tier"] = {t: cfg.get_price(t) for t in ("cached_intel", "static_scrape", "stealth_browser")}
                except Exception:
                    pass
            body_402["usage"] = u
            body_402["error"] = body_402.get("error") or "Payment required. Pass the required `url` parameter; see `usage`."
        return JSONResponse(status_code=402, content=body_402, headers=headers_402)

    def _scrape_bazaar(method: str) -> Dict[str, Any]:
        if method == "GET":
            return _bazaar_extension("GET", {"url": "https://example.com", "tier": "static_scrape"},
                                     _SCRAPE_QUERY_SCHEMA, _SCRAPE_OUTPUT_EXAMPLE)
        return _bazaar_extension("POST", {"url": "https://example.com", "tier": "static_scrape"},
                                 _SCRAPE_BODY_SCHEMA, _SCRAPE_OUTPUT_EXAMPLE)

    def _batch_bazaar() -> Dict[str, Any]:
        return _bazaar_extension(
            "POST", {"urls": ["https://example.com", "https://example.org"], "tier": "static_scrape"},
            _BATCH_BODY_SCHEMA, {"count": 2, "results": [_SCRAPE_OUTPUT_EXAMPLE]},
        )

    def _buy_bazaar(method: str, preview: Dict[str, Any]) -> Dict[str, Any]:
        sample = (preview.get("sample_preview") or [{}])[:1]
        out = {"dataset_id": preview["dataset_id"], "format": "json", "record_count": preview.get("record_count"),
               "records": sample}
        if method == "GET":
            return _bazaar_extension("GET", {"format": "json"}, _BUY_QUERY_SCHEMA, out)
        return _bazaar_extension("POST", {}, {"type": "object"}, out)

    def _ownership_proofs() -> List[str]:
        return [p.strip() for p in environ.get("SCRAPLING_OWNERSHIP_PROOFS", "").split(",") if p.strip()]

    def _paid_resource_urls(base: str) -> List[str]:
        urls = [f"{base}/v1/scrape", f"{base}/v1/scrape/text", f"{base}/v1/batch-scrape"]
        urls += [f"{base}/v1/datasets/{ds['dataset_id']}/buy" for ds in mkt.list_datasets()]
        urls += paid_lookup_urls(base)
        urls += paid_dataset_urls(base)
        return urls

    register_agent_api(
        app, mkt=mkt, gate=_gate_or_settle, settle=_settle_pending,
        payment_response_header=verifier.build_payment_response_header,
        bazaar_ext=_bazaar_extension, public_base=_public_base_url,
    )

    def _vault_root() -> Path:
        if vault_dir is not None:
            return Path(vault_dir)
        return Path(environ.get("SCRAPLING_VAULT_DIR") or "/collected_data")

    register_dataset_listings(
        app, vault_dir=_vault_root, gate=_gate_or_settle, settle=_settle_pending,
        payment_response_header=verifier.build_payment_response_header,
        bazaar_ext=_bazaar_extension, public_base=_public_base_url,
    )

    @app.post("/v1/scrape")
    async def scrape_post(request: Request, body: Optional[ScrapeRequestBody] = None) -> JSONResponse:
        if body is None:
            # Let unauthenticated discovery probes see the 402 challenge before body validation.
            if _has_payment_attempt(request):
                raise HTTPException(status_code=400, detail="JSON body with `url` is required.")
            return _payment_required(request, "scrape:probe", cfg.get_price("static_scrape"), "static_scrape",
                                     "Any web page as clean, LLM-ready Markdown with boilerplate stripped, plus JSON-LD, tables and links; live fetch. REQUIRED: url (e.g. GET /v1/scrape?url=https://example.com&tier=static_scrape, or POST JSON {\"url\":...}). Optional tier: auto|cached_intel|static_scrape|stealth_browser. Price $0.001-$0.025 by tier ($0.005 typical); no charge on failure", _scrape_bazaar("POST"),
                                     "PAYMENT-SIGNATURE header is required")
        rules_sig = str(sorted((body.extract_rules or {}).items()))
        selector_key = f"{body.css_selector or ''}|{body.xpath_selector or ''}|{rules_sig}"
        effective_tier = body.tier
        if effective_tier == "auto":
            cached_check = ldg.get_cached_intel(
                url=body.url,
                css_selector=selector_key,
                extraction_type=body.tier,
                revenue_delta_usd=0.0,
            )
            effective_tier = "cached_intel" if cached_check is not None else "static_scrape"

        required_usd = cfg.get_price(effective_tier)
        resp_402, settlement = _gate_or_settle(
            request=request,
            resource=f"scrape:{body.url}",
            required_usd=required_usd,
            tier_id=effective_tier,
            description="Any web page as clean, LLM-ready Markdown with boilerplate stripped, plus JSON-LD, tables and links; live fetch. REQUIRED: url (e.g. GET /v1/scrape?url=https://example.com&tier=static_scrape, or POST JSON {\"url\":...}). Optional tier: auto|cached_intel|static_scrape|stealth_browser. Price $0.001-$0.025 by tier ($0.005 typical); no charge on failure",
            bazaar=_scrape_bazaar(request.method),
        )
        if resp_402 is not None:
            return resp_402

        try:
            result = scr.scrape(
                url=body.url,
                tier=body.tier,
                css_selector=body.css_selector,
                xpath_selector=body.xpath_selector,
                extract_rules=body.extract_rules,
                html_override=body.html_override,
                use_cache=body.use_cache,
                adaptive=body.adaptive,
                revenue_delta_usd=required_usd,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"{exc} (payment NOT settled, no charge)") from exc
        try:
            _st = int((result or {}).get("status") or 200)
        except (TypeError, ValueError):
            _st = 200
        if not result or _st >= 400:
            raise HTTPException(status_code=502, detail=f"Target page returned HTTP {_st}; payment NOT settled (no charge).")

        settle_fail = _settle_pending(request, settlement)
        if settle_fail is not None:
            return settle_fail
        result["payment_settlement"] = settlement
        resp_headers = verifier.build_payment_response_header(settlement or {})
        return JSONResponse(status_code=200, content=result, headers=resp_headers)

    @app.get("/v1/scrape")
    async def scrape_get(
        request: Request,
        url: Optional[str] = Query(None, description="Target URL to scrape (required once paying)."),
        tier: str = Query("auto", description="Scraping tier."),
        css_selector: Optional[str] = Query(None, description="Optional CSS selector."),
    ) -> JSONResponse:
        if not url:
            if _has_payment_attempt(request):
                raise HTTPException(status_code=400, detail="Query parameter `url` is required.")
            return _payment_required(request, "scrape:probe", cfg.get_price("static_scrape"), "static_scrape",
                                     "Any web page as clean, LLM-ready Markdown with boilerplate stripped, plus JSON-LD, tables and links; live fetch. REQUIRED: url (e.g. GET /v1/scrape?url=https://example.com&tier=static_scrape, or POST JSON {\"url\":...}). Optional tier: auto|cached_intel|static_scrape|stealth_browser. Price $0.001-$0.025 by tier ($0.005 typical); no charge on failure", _scrape_bazaar("GET"),
                                     "PAYMENT-SIGNATURE header is required")
        body = ScrapeRequestBody(url=url, tier=tier, css_selector=css_selector)
        return await scrape_post(request, body)

    _TEXT_PRICE = 0.003
    _TEXT_DESC = ("Page to clean text. REQUIRED: url (example GET /v1/scrape/text?url=https://example.com). Any web page as clean, LLM-ready Markdown text "
                  "with navigation, ads and boilerplate stripped; title, text and character count only. $0.003 per page. "
                  "No charge if the page cannot be fetched")

    @app.get("/v1/scrape/text", summary="Page to clean text: any URL as LLM-ready Markdown ($0.003)")
    async def scrape_text(request: Request, url: Optional[str] = Query(None, description="Page URL")) -> JSONResponse:
        bz = _bazaar_extension("GET", {"url": "https://example.com"},
                               {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
                               {"url": "https://example.com", "title": "Example Domain", "text": "# Example Domain ...",
                                "chars": 180})
        if not url:
            if _has_payment_attempt(request):
                raise HTTPException(status_code=400, detail="Query parameter `url` is required.")
            return _payment_required(request, "scrape_text:probe", _TEXT_PRICE, "static_scrape", _TEXT_DESC, bz,
                                     "PAYMENT-SIGNATURE header is required")
        resp_402, settlement = _gate_or_settle(request=request, resource=f"scrape_text:{url}", required_usd=_TEXT_PRICE,
                                               tier_id="static_scrape", description=_TEXT_DESC, bazaar=bz)
        if resp_402 is not None:
            return resp_402
        b = ScrapeRequestBody(url=url)
        try:
            result = await run_in_threadpool(lambda: scr.scrape(
                url=b.url, tier="auto", css_selector=None, xpath_selector=None, extract_rules=None,
                html_override=None, use_cache=b.use_cache, adaptive=b.adaptive, revenue_delta_usd=_TEXT_PRICE))
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"{exc} (payment NOT settled, no charge)") from exc
        data = (result or {}).get("data") or {}
        text = (data.get("markdown") or "").strip()
        try:
            _st = int((result or {}).get("status") or 200)
        except (TypeError, ValueError):
            _st = 200
        if not text or _st >= 400:
            raise HTTPException(status_code=502, detail=f"No readable text (HTTP {_st}); payment NOT settled (no charge).")
        settle_fail = _settle_pending(request, settlement)
        if settle_fail is not None:
            return settle_fail
        body = {"url": url, "title": data.get("title"), "text": text, "chars": len(text), "price_usd": _TEXT_PRICE,
                "payment_settlement": settlement}
        return JSONResponse(body, headers=verifier.build_payment_response_header(settlement or {}))

    @app.get("/v1/batch-scrape", include_in_schema=False)
    async def batch_scrape_get(request: Request) -> JSONResponse:
        return _payment_required(request, "batch_scrape:probe", cfg.get_price("static_scrape"), "static_scrape",
                                 "Use POST with JSON {\"urls\": [...]} (max 100). Batch web scraping: up to 100 URLs as clean Markdown, JSON-LD and tables; priced per page", _batch_bazaar(),
                                 "Send POST /v1/batch-scrape with a JSON body and a PAYMENT-SIGNATURE header")

    @app.post("/v1/batch-scrape")
    async def batch_scrape_endpoint(request: Request, body: Optional[BatchScrapeRequestBody] = None) -> JSONResponse:
        if body is None:
            if _has_payment_attempt(request):
                raise HTTPException(status_code=400, detail="JSON body with `urls` is required.")
            return _payment_required(request, "batch_scrape:probe", cfg.get_price("static_scrape"), "static_scrape",
                                     "Batch web scraping API: scrape up to 100 URLs in one call, each page returned as clean LLM-ready Markdown plus JSON-LD and tables; priced per page", _batch_bazaar(),
                                     "PAYMENT-SIGNATURE header is required")
        if not body.urls:
            raise HTTPException(status_code=400, detail="urls list cannot be empty.")
        if len(body.urls) > MAX_BATCH_URLS:
            raise HTTPException(status_code=400, detail=f"At most {MAX_BATCH_URLS} urls per batch.")
        tier_for_quote = "static_scrape" if body.tier == "auto" else body.tier
        required_usd = cfg.get_price(tier_for_quote, pages=len(body.urls))

        resp_402, settlement = _gate_or_settle(
            request=request,
            resource=f"batch_scrape:{len(body.urls)}_urls",
            required_usd=required_usd,
            tier_id=tier_for_quote,
            description="Batch web scraping API: scrape up to 100 URLs in one call, each page returned as clean LLM-ready Markdown plus JSON-LD and tables; priced per page",
            bazaar=_batch_bazaar(),
        )
        if resp_402 is not None:
            return resp_402

        per_url_rev = round(required_usd / len(body.urls), 6)
        results: List[Dict[str, Any]] = []
        records_for_dataset: List[Dict[str, Any]] = []
        for u in body.urls:
            item = scr.scrape(
                url=u,
                tier=body.tier,
                extract_rules=body.extract_rules,
                use_cache=body.use_cache,
                revenue_delta_usd=per_url_rev,
            )
            results.append(item)
            records_for_dataset.append(
                {
                    "url": u,
                    "title": item["data"]["title"],
                    "quality_score": item["quality_score"],
                    "custom_fields": item["data"]["custom_fields"],
                    "markdown_preview": item["data"]["markdown"][:500],
                    "sha256": item["provenance"]["sha256"],
                }
            )

        published_ds = None
        if body.publish_as_dataset:
            slug = "ds_" + "".join(c if c.isalnum() else "_" for c in body.publish_as_dataset.lower()).strip("_")[:32]
            published_ds = mkt.publish_dataset(
                dataset_id=slug,
                title=body.publish_as_dataset,
                category="Custom Agent Crawl",
                description=f"Batch intelligence crawl across {len(body.urls)} URLs.",
                price_usd=body.dataset_price_usd,
                records=records_for_dataset,
            )

        settle_fail = _settle_pending(request, settlement)
        if settle_fail is not None:
            return settle_fail
        resp_headers = verifier.build_payment_response_header(settlement or {})
        return JSONResponse(
            status_code=200,
            content={
                "count": len(results),
                "results": results,
                "published_dataset": published_ds,
                "payment_settlement": settlement,
            },
            headers=resp_headers,
        )

    @app.get("/v1/datasets")
    async def list_datasets_endpoint() -> Dict[str, Any]:
        return {
            "receiving_wallet": cfg.receiving_address,
            "datasets": mkt.list_datasets(),
        }

    _RETIRED_REDIRECTS = {"ds_crypto_defi_intel": "/v1/bundle/defi-intel",
                          "ds_ecommerce_price_stock": "/v1/ecom/snapshot"}

    def _dataset_not_found(dataset_id: str, request: Request) -> JSONResponse:
        # conversion-20261025: unknown / template ids get the list of valid ids instead of a bare 404
        try:
            ids = [d.get("dataset_id") or d.get("id") for d in (mkt.list_datasets() or [])]
            ids = [i for i in ids if i]
        except Exception:
            ids = []
        base = _public_base_url(request)
        return JSONResponse(status_code=404, content={
            "error": "dataset_not_found", "dataset_id": dataset_id,
            "detail": f"Dataset '{dataset_id}' not found (no charge). Replace {{dataset_id}} with one of `valid_dataset_ids`.",
            "valid_dataset_ids": ids,
            "example_preview": f"{base}/v1/datasets/{ids[0]}/preview" if ids else None,
            "example_buy": f"{base}/v1/datasets/{ids[0]}/buy" if ids else None,
            "catalog": f"{base}/v1/free/datasets",
        })

    @app.api_route("/v1/datasets/{dataset_id}/preview", methods=["GET", "POST"])
    async def preview_dataset_endpoint(dataset_id: str, request: Request) -> Any:
        if dataset_id in _RETIRED_REDIRECTS:
            return {"dataset_id": dataset_id, "retired": True, "replacement": _RETIRED_REDIRECTS[dataset_id],
                    "note": "Retired. Buy the live replacement (x402) at the replacement URL; /buy redirects there."}
        preview = mkt.get_dataset_preview(dataset_id)
        if not preview:
            return _dataset_not_found(dataset_id, request)
        return preview

    _BUY_LEAD = {
        "live_us_stocks_etfs": "Stock and ETF price feed: US stocks, ETFs and commodities from Chainlink on-chain oracles - price and 1d/5d % change",
        "live_base_new_token_risk": "Base new token security scan: honeypot check, buy/sell tax and owner risk score for newly launched Base tokens",
        "live_chain_tvl": "Blockchain TVL rankings: total value locked per chain (56 chains) from our on-chain protocol reads",
        "live_dex_volumes": "DEX volume rankings: 24h trading volume per decentralized exchange, computed on-chain",
        "live_stablecoin_supply": "Stablecoin supply and peg tracker: circulating supply and USD peg per stablecoin",
        "live_crypto_top500_markets": "Crypto price feed: about 155 coins from Chainlink on-chain oracles - price and 1h/24h/7d/30d % change",
        "live_defi_protocols_tvl": "DeFi protocol TVL rankings: tracked on-chain TVL per protocol with 1d/7d change",
        "live_defi_yield_pools": "DeFi yield pools: APY, TVL and risk for 1,000+ DEX pools across 16 chains",
        "live_risk_ranked_yields": "DeFi yields ranked by risk-adjusted APY (pools with at least $5M TVL)",
        "live_polymarket_odds": "Polymarket prediction market odds with 1h/24h/7d odds change",
        "live_new_pools_base_solana": "New token pool launches on Base and Solana (last 3 hours) with risk flags",
    }

    @app.post("/v1/datasets/{dataset_id}/buy")
    @app.get("/v1/datasets/{dataset_id}/buy")
    async def buy_dataset_endpoint(
        dataset_id: str,
        request: Request,
        format: str = Query("json", description="Export format: json, jsonl, csv, or markdown"),
    ) -> JSONResponse:
        if dataset_id in _RETIRED_REDIRECTS:
            from fastapi.responses import RedirectResponse
            qs = request.url.query
            return RedirectResponse(_RETIRED_REDIRECTS[dataset_id] + (f"?{qs}" if qs else ""), status_code=307)
        preview = mkt.get_dataset_preview(dataset_id)
        if not preview:
            return _dataset_not_found(dataset_id, request)
        price_usd = float(preview["price_usd"])

        resp_402, settlement = _gate_or_settle(
            request=request,
            resource=f"dataset:{dataset_id}",
            required_usd=price_usd,
            tier_id="spider_crawl",
            description=(f"{_BUY_LEAD.get(dataset_id, preview['title'])} - full dataset, {preview['record_count']} rows in one paid "
                         f"download (JSON, JSONL, CSV or Markdown), refreshed about every 30 min. Free 10-row preview at "
                         f"/v1/datasets/{dataset_id}/preview"),
            bazaar=_buy_bazaar(request.method, preview),
        )
        if resp_402 is not None:
            return resp_402

        settle_fail = _settle_pending(request, settlement)
        if settle_fail is not None:
            return settle_fail
        unlocked = mkt.unlock_dataset(dataset_id, export_format=format, revenue_usd=price_usd)
        unlocked["payment_settlement"] = settlement
        resp_headers = verifier.build_payment_response_header(settlement or {})
        return JSONResponse(status_code=200, content=unlocked, headers=resp_headers)

    @app.post("/v1/datasets/publish", include_in_schema=False, dependencies=[Depends(require_admin)])
    async def publish_dataset_endpoint(body: PublishDatasetRequestBody) -> Dict[str, Any]:
        return mkt.publish_dataset(
            dataset_id=body.dataset_id,
            title=body.title,
            category=body.category,
            description=body.description,
            price_usd=body.price_usd,
            records=body.records,
            quality_score=body.quality_score,
        )

    @app.post("/v1/credits/create-key", include_in_schema=False, dependencies=[Depends(require_admin)])
    async def create_api_key_endpoint(body: CreateKeyRequestBody) -> Dict[str, Any]:
        initial_usd = 0.0
        receipt = None
        if body.tx_hash and body.deposit_usd > 0:
            try:
                if ldg.is_tx_used(body.tx_hash, body.network):
                    raise ValueError(f"Transaction '{body.tx_hash}' on {body.network} has already been redeemed (replay blocked).")
                verified = verifier.verify_evm_tx_onchain(body.tx_hash, network=body.network, min_amount_usd=body.deposit_usd)
                receipt = ldg.record_payment(
                    tx_hash=body.tx_hash,
                    network=body.network,
                    payer_address=verified["payer_address"],
                    amount_usd=verified["amount_usd"],
                    resource="api_key_creation",
                    asset=verified["asset"],
                    buyer_type=body.buyer_type,
                    settlement_scheme="onchain-deposit",
                )
                initial_usd = verified["amount_usd"]
            except ValueError as err:
                raise HTTPException(status_code=400, detail=str(err)) from err
        elif cfg.allow_demo_preview:
            initial_usd = cfg.free_trial_credits_usd

        created = ldg.create_api_key(
            owner_id=body.owner_id,
            initial_balance_usd=initial_usd,
            buyer_type=body.buyer_type,
        )
        if receipt:
            created["deposit_receipt"] = receipt
        return created

    @app.post("/v1/credits/deposit")
    async def topup_api_key_endpoint(body: TopupKeyRequestBody) -> Dict[str, Any]:
        try:
            # Always verify the deposit on-chain; credit the amount actually received, not the claimed one.
            if ldg.is_tx_used(body.tx_hash, body.network):
                raise ValueError(f"Transaction '{body.tx_hash}' on {body.network} has already been redeemed (replay blocked).")
            if not ldg.get_api_key_info(body.api_key):
                raise ValueError("API key not found.")
            verified = verifier.verify_evm_tx_onchain(body.tx_hash, network=body.network, min_amount_usd=body.amount_usd)
            return ldg.topup_api_key(
                api_key=body.api_key,
                amount_usd=verified["amount_usd"],
                tx_hash=body.tx_hash,
                network=body.network,
                payer_address=verified["payer_address"],
                asset=verified["asset"],
            )
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err

    @app.get("/v1/credits/balance")
    async def check_balance_endpoint(
        request: Request,
        api_key: Optional[str] = Query(None, description="API key (`sk_scrapling_...`)"),
    ) -> Dict[str, Any]:
        key = api_key
        if not key:
            auth = request.headers.get("authorization", "")
            if auth.lower().startswith("bearer "):
                key = auth[7:].strip()
        if not key:
            raise HTTPException(status_code=400, detail="Provide `api_key` query param or `Authorization: Bearer` header.")
        info = ldg.get_api_key_info(key)
        if not info:
            raise HTTPException(status_code=404, detail="API key not found.")
        info["receiving_wallet"] = cfg.receiving_address
        return info

    @app.post("/v1/harvest", include_in_schema=False, dependencies=[Depends(require_admin)])
    async def trigger_harvest_endpoint() -> Dict[str, Any]:
        """Run the AutonomousHarvester to pre-warm cache, save to O: drive, and refresh marketplace datasets."""
        summaries = harvester.run_default_harvest()
        return {
            "status": "completed",
            "receiving_wallet": cfg.receiving_address,
            "vault_status": harvester.get_vault_status(),
            "harvested_datasets": summaries,
        }

    @app.get("/v1/autopilot/status", include_in_schema=False, dependencies=[Depends(require_admin)])
    async def autopilot_status_endpoint() -> Dict[str, Any]:
        return {
            "receiving_wallet": cfg.receiving_address,
            "vault": harvester.get_vault_status(),
            "custom_targets": harvester.get_custom_targets(),
        }

    @app.post("/v1/autopilot/add-target", include_in_schema=False, dependencies=[Depends(require_admin)])
    async def autopilot_add_target_endpoint(body: AddAutopilotTargetBody) -> Dict[str, Any]:
        harvested = harvester.add_custom_target(
            url=body.url,
            title=body.title,
            category=body.category,
            price_usd=body.price_usd,
            css_selector=body.css_selector,
        )
        return {
            "status": "added_and_harvested_to_odrive",
            "receiving_wallet": cfg.receiving_address,
            "harvested": harvested,
            "vault": harvester.get_vault_status(),
        }

    @app.post("/v1/autopilot/scan-odrive", include_in_schema=False, dependencies=[Depends(require_admin)])
    async def autopilot_scan_odrive_endpoint() -> Dict[str, Any]:
        imported = harvester.scan_and_import_odrive_files()
        return {
            "status": "scanned_odrive",
            "receiving_wallet": cfg.receiving_address,
            "imported_files_count": len(imported),
            "imported_datasets": imported,
            "vault": harvester.get_vault_status(),
        }

    @app.post("/v1/autopilot/ingest-browser-page", include_in_schema=False, dependencies=[Depends(require_admin)])
    async def autopilot_ingest_browser_page_endpoint(body: IngestBrowserPageBody) -> Dict[str, Any]:
        return harvester.ingest_browsed_page(
            url=body.url,
            html_content=body.html_content,
            title=body.title,
            price_usd=body.price_usd,
            auto_group_by_domain=body.auto_group_by_domain,
        )

    @app.get("/v1/sales/kit", include_in_schema=False, dependencies=[Depends(require_admin)])
    async def get_sales_kit_endpoint(
        public_url: Optional[str] = Query(None, description="Optional public HTTPS tunnel URL for your node.")
    ) -> Dict[str, Any]:
        """Generate copy-paste social ads, direct buy links, and AI agent directory registration payloads."""
        return sales_engine.generate_sales_kit(public_url_override=public_url)

    @app.get("/admin/usage", include_in_schema=False)
    async def admin_usage_daily(request: Request) -> JSONResponse:
        """Daily request totals. Hidden unless ADMIN_TOKEN is set. Not part of public discovery."""
        expected = environ.get("ADMIN_TOKEN", "")
        if not expected:
            raise HTTPException(status_code=404)
        supplied = request.headers.get("x-admin-token", "")
        supplied_b = supplied.encode("utf-8")
        expected_b = expected.encode("utf-8")
        if len(supplied_b) != len(expected_b) or not hmac.compare_digest(supplied_b, expected_b):
            raise HTTPException(status_code=401, detail="Admin token required.")
        raw = request.query_params.get("days", "7")
        try:
            days = int(raw)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="days must be an integer from 1 to 366.")
        if days < 1 or days > 366:
            raise HTTPException(status_code=400, detail="days must be an integer from 1 to 366.")
        from . import usage_daily as _usage_daily
        try:
            return JSONResponse(_usage_daily.totals(days))
        except Exception:
            raise HTTPException(status_code=500, detail="Usage totals are unavailable.")

    @app.get("/admin/trial-codes", include_in_schema=False)
    async def trial_codes_admin(request: Request, token: str = "") -> JSONResponse:
        given = (request.headers.get("authorization", "").removeprefix("Bearer ").strip()
                 or request.headers.get("x-admin-token", "") or token)
        valid = [t for t in (environ.get("ADMIN_TOKEN", ""), environ.get("WHALE_INGEST_TOKEN", "")) if t]
        import hmac as _h
        if not given or not any(_h.compare_digest(given, t) for t in valid):
            raise HTTPException(status_code=401, detail="Unauthorized")
        return JSONResponse({"codes": _trial.status(), "calls_per_code_default": _trial.DEFAULT_CALLS,
                             "excluded_tiers": sorted(_trial.PAID_ONLY_TIERS)})

    @app.get("/v1/revenue", include_in_schema=False, dependencies=[Depends(require_admin)])
    async def revenue_analytics_endpoint() -> Dict[str, Any]:
        return ldg.get_revenue_analytics()

    # ------------------------------------------------------------------
    # OpenAPI with x402 discovery metadata (x402scan / @agentcash/discovery)
    # ------------------------------------------------------------------
    def _usd(v: float) -> str:
        return f"{v:.6f}".rstrip("0").rstrip(".")

    _resp_402 = {
        "description": "Payment Required (x402 v2). Challenge in the PAYMENT-REQUIRED header (base64 JSON) and body.",
        "headers": {"PAYMENT-REQUIRED": {"schema": {"type": "string"}, "description": "Base64 x402 PaymentRequired"}},
        "content": {"application/json": {"schema": {"type": "object"}}},
    }

    def _mark_paid(op: Dict[str, Any], price: Dict[str, Any]) -> None:
        op["x-payment-info"] = {"price": price, "protocols": [{"x402": {}}] + mppd.discovery_protocols()}
        op.setdefault("responses", {})["402"] = _resp_402

    @app.get("/openapi.json", include_in_schema=False)
    async def openapi_json(request: Request) -> JSONResponse:
        base = _public_base_url(request)
        contact: Dict[str, str] = {"name": SERVICE_NAME, "url": base}
        if environ.get("SCRAPLING_CONTACT_EMAIL"):
            contact["email"] = environ["SCRAPLING_CONTACT_EMAIL"].strip()
        spec = get_openapi(
            title=app.title, version=app.version, description=app.description, routes=app.routes,
            contact=contact, servers=[{"url": base}],
        )
        spec.setdefault("components", {}).setdefault("securitySchemes", {})["bearerApiKey"] = {
            "type": "http", "scheme": "bearer",
            "description": "Optional prepaid key (sk_scrapling_...) as an alternative to per-call x402 payment.",
        }
        tier_prices = [t.price_usd for t in cfg.pricing_tiers.values()]
        scrape_price = {"mode": "dynamic", "currency": "USD", "min": _usd(min(tier_prices)), "max": _usd(max(tier_prices))}
        batch_price = {"mode": "dynamic", "currency": "USD", "min": _usd(min(tier_prices)),
                       "max": _usd(max(cfg.get_price(t, pages=MAX_BATCH_URLS) for t in cfg.pricing_tiers))}
        paths = spec.get("paths", {})
        for method in ("get", "post"):
            if method in paths.get("/v1/scrape", {}):
                _mark_paid(paths["/v1/scrape"][method], scrape_price)
        openapi_mark(paths, _mark_paid, _usd)
        openapi_mark_datasets(paths, _mark_paid, _usd)
        if "get" in paths.get("/v1/scrape/text", {}):
            _mark_paid(paths["/v1/scrape/text"]["get"], {"mode": "fixed", "currency": "USD", "amount": _usd(_TEXT_PRICE)})
            paths["/v1/scrape/text"]["get"]["description"] = _TEXT_DESC + ". Paid via x402 (Base, Polygon, Arbitrum or Solana)."
        if "post" in paths.get("/v1/batch-scrape", {}):
            _mark_paid(paths["/v1/batch-scrape"]["post"], batch_price)
        # Concrete per-dataset buy routes so probes hit a real 402 instead of a templated 404.
        template = paths.get("/v1/datasets/{dataset_id}/buy")
        if template:
            for ds in mkt.list_datasets():
                concrete: Dict[str, Any] = {}
                for method, op in template.items():
                    op = json_loads(json_dumps_bytes(op))
                    op["parameters"] = [p for p in op.get("parameters", []) if p.get("name") != "dataset_id"]
                    op["operationId"] = f"buy_{ds['dataset_id']}_{method}"
                    op["summary"] = f"Buy dataset: {ds['title']}"
                    _mark_paid(op, {"mode": "fixed", "currency": "USD", "amount": _usd(float(ds["price_usd"]))})
                    concrete[method] = op
                paths[f"/v1/datasets/{ds['dataset_id']}/buy"] = concrete
            del paths["/v1/datasets/{dataset_id}/buy"]
        for path_item in paths.values():
            for op in path_item.values():
                if isinstance(op, dict) and (op.get("x-payment-info") or {}).get("free"):
                    op.pop("x-payment-info", None)
                if isinstance(op, dict) and "x-payment-info" not in op:
                    op["security"] = []  # explicitly public
        spec["info"]["x-guidance"] = (
            "Pay per call with x402 v2 (USDC on Base, Polygon, Arbitrum or Solana mainnet) or MPP (USDC.e on Tempo; WWW-Authenticate: Payment challenge with intent=charge and, when enabled, intent=session; retry with Authorization: Payment). x402 is settled only after valid data is ready; MPP settles on submit. No charge on failure: payment is settled only after valid data is ready. "
            "An unpaid call to a paid route returns without payment a 402 challenge (PAYMENT-REQUIRED header) with the amount and payTo; "
            "a retry carrying the payment proof returns the data. POST /v1/scrape {\"url\": ...} scrapes a page into "
            "Markdown, JSON-LD and tables; POST /v1/batch-scrape takes up to 100 urls; GET /v1/datasets lists "
            "datasets (free previews of up to 10 rows at /v1/datasets/<dataset_id>/preview, ids from GET /v1/datasets) and GET /v1/datasets/{id}/buy?format=json "
            "buys one. FREE: /v1/free/feeds, /v1/free/crypto/top10, /v1/free/defi/top-yields. Single lookups: "
            "/v1/lookup/coin?q= $0.001, /v1/lookup/protocol?q= $0.001, /v1/lookup/yields?chain=&token= $0.002, "
            "/v1/lookup/pool?pool_id= $0.001, /v1/lookup/polymarket?q= $0.005, /v1/lookup/token-risk?address= $0.005, "
            "/v1/ecom/product?q= $0.01 (E-commerce price + stock lookup across 180+ Shopify stores, 30-day history; free teaser /v1/free/ecom), /v1/ecom/changes $0.02 (price drops, restocks, sell-outs, new products), /v1/ecom/snapshot?category=|brand=|domain= $0.05, /v1/ecom/catalog?domain= $0.25 (full store catalog), "
            "/v1/regime/now $0.02 (Market Regime: risk-on/off score 0-100, label, why, components; free delayed teaser /v1/free/regime), /v1/regime/full $0.05 (all metrics, 30-day history, methodology), /v1/dd/report?chain=&address= $1.00 (Crypto Due-Diligence Agent: full report on a token or wallet), /v1/dd/quick?chain=&address= $0.25 (due-diligence quick check), /v1/trust/agent?chain=&address= $0.04 (Trust Bundle: allow/caution/block + trust score for an agent, wallet or token), /v1/check/counterparty?chain=&address= $0.004 (safe/caution/block for a wallet or token), /v1/wallet/profile?chain=&address= $0.005 (wallet label + activity score), /v1/token/holders?chain=solana&address= $0.005 (holder concentration) "
            "(go/caution/avoid verdict). Bundle: /v1/bundle/market-pulse $0.001 (top coins by rank with Chainlink prices, volume leaders, on-chain DeFi TVL, "
            "DEX volume, heatmap in one call). Page to clean text: /v1/scrape/text?url= $0.003. "
            "Dataset listings (optional q, limit, offset, format=json|csv; catalog free at /v1/free/datasets): "
            "/v1/datasets/coingecko-markets $0.002, /v1/datasets/defillama-protocols $0.002, "
            "/v1/datasets/volume-leaders $0.002 (live), /v1/datasets/market-heatmap $0.002 (live), "
            "/v1/datasets/dex-volume $0.002 (live). "
            "(misses return 404, no charge). Prices: clean text $0.003; full scrape $0.001-$0.025 per page ($0.005 typical); datasets $0.01 (up to 10 rows), "
            "$0.05 (up to 100), $0.49 (up to 1,000), $1.99 (up to 10,000), max $4.99."
        )
        proofs = _ownership_proofs()
        if proofs:
            spec["x-discovery"] = {"ownershipProofs": proofs}
        spec["info"]["x-logo"] = {"url": f"{base}/logo.png", "altText": SERVICE_NAME}
        return JSONResponse(spec)

    @app.get("/logo.png", include_in_schema=False)
    @app.get("/favicon.ico", include_in_schema=False)
    async def logo_png() -> Response:
        if LOGO_PATH.is_file():
            return Response(LOGO_PATH.read_bytes(), media_type="image/png",
                            headers={"Cache-Control": "public, max-age=86400"})
        try:
            import base64 as _b64
            from .logo_data import LOGO_PNG_B64
            return Response(_b64.b64decode(LOGO_PNG_B64), media_type="image/png",
                            headers={"Cache-Control": "public, max-age=86400"})
        except Exception:
            raise HTTPException(404, detail="logo not found")

    @app.get("/favicon.svg", include_in_schema=False)
    async def favicon() -> HTMLResponse:
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="12" '
               'fill="#0b0f19"/><text x="32" y="42" font-size="28" text-anchor="middle" fill="#10b981" '
               'font-family="monospace">402</text></svg>')
        return HTMLResponse(svg, media_type="image/svg+xml")

    register_mcp_routes(app, _public_base_url)

    @app.get("/docs/swagger", include_in_schema=False)
    async def swagger_docs() -> HTMLResponse:
        return get_swagger_ui_html(openapi_url="/openapi.json", title=f"{app.title} - Swagger UI")

    return app
