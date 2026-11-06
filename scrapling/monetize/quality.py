"""Transparent quality grades for every product/route family (upd45).

Measured from real probes + feed metadata + usage logs — not hand-assigned.
Free endpoints: GET /v1/quality, GET /v1/quality/{product}
Human page: GET /quality
Discovery: quality block on catalog entries
Background recompute every SCRAPLING_QUALITY_INTERVAL_MINUTES (default 30).

Rubric weights (published; anyone can recompute):
  freshness    0.30  age of newest data vs promised refresh interval
  completeness 0.25  pct of key fields non-null in recent free/sample response
  coverage     0.20  counts vs published targets
  uptime       0.15  success rate of the route family (usage.db last 24h, else probe OK)
  validation   0.10  product checks (ecom placeholder pct, company fill rate, ...)

Letter: A>=90, B>=80, C>=70, D>=60, F<60. Score 0-100.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

log = logging.getLogger("scrapling.quality")

WEIGHTS = {
    "freshness": 0.30,
    "completeness": 0.25,
    "coverage": 0.20,
    "uptime": 0.15,
    "validation": 0.10,
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9
LETTER = ((90, "A"), (80, "B"), (70, "C"), (60, "D"), (0, "F"))
INTERVAL_MIN = max(15, int(os.environ.get("SCRAPLING_QUALITY_INTERVAL_MINUTES", "30")))
PROBE_BASE = os.environ.get("SCRAPLING_QUALITY_PROBE_BASE", "http://127.0.0.1:8080").rstrip("/")
CACHE_DIR = "/collected_data/quality" if os.path.isdir("/collected_data") else "/tmp/scrapling_quality"
os.makedirs(CACHE_DIR, exist_ok=True)
CACHE_PATH = os.path.join(CACHE_DIR, "grades.json")
USAGE_DB = "/collected_data/usage/usage.db"
R30, R15, R60, R6H, R24H, RLIVE = 30*60, 15*60, 60*60, 6*3600, 24*3600, 120


PRODUCTS = json.loads(r'''{
  "coin": {
    "title": "Coin price (Chainlink)",
    "path": "/v1/lookup/coin",
    "family": "crypto-prices",
    "sources": [
      "Chainlink Data Feeds (on-chain, Ethereum/Base/Arbitrum public RPC)"
    ],
    "refresh_promised_sec": 1800,
    "free_probe": "/v1/free/crypto/top10",
    "feed_id": "live_crypto_top500_markets",
    "key_fields": [
      "coins.0.symbol",
      "coins.0.price_usd",
      "snapshot_at"
    ],
    "coverage_target": {
      "coins": 155
    },
    "coverage_from_feed_rows": true,
    "validation": "none"
  },
  "prices": {
    "title": "Bulk token prices",
    "path": "/v1/lookup/prices",
    "family": "crypto-prices",
    "sources": [
      "Chainlink Data Feeds via Multicall3 (Ethereum preferred, else Arbitrum/Base)"
    ],
    "refresh_promised_sec": 1800,
    "free_probe": "/v1/free/prices?tokens=btc,eth,sol",
    "feed_id": "live_crypto_top500_markets",
    "key_fields": [
      "prices.0.symbol",
      "prices.0.price_usd",
      "read_at"
    ],
    "coverage_target": {
      "coins": 155
    },
    "coverage_from_feed_rows": true,
    "validation": "none"
  },
  "protocol": {
    "title": "DeFi protocol TVL",
    "path": "/v1/lookup/protocol",
    "family": "defi",
    "sources": [
      "Own on-chain lending+DEX pool reads (Aave v3, Compound v3, Moonwell, Morpho Blue, Uniswap/etc.)"
    ],
    "refresh_promised_sec": 1800,
    "free_probe": "/v1/free/feeds",
    "feed_id": "live_defi_protocols_tvl",
    "key_fields": [
      "feeds"
    ],
    "coverage_target": {
      "protocols": 200
    },
    "coverage_from_feed_rows": true,
    "validation": "none"
  },
  "yields": {
    "title": "DeFi yields (risk-ranked)",
    "path": "/v1/lookup/yields",
    "family": "defi",
    "sources": [
      "Own on-chain pool APY/TVL reads (Aave/Compound/Moonwell/Morpho + DEX pools)"
    ],
    "refresh_promised_sec": 1800,
    "free_probe": "/v1/free/defi/top-yields",
    "feed_id": "live_risk_ranked_yields",
    "key_fields": [
      "yields.0.apy",
      "yields.0.tvl_usd"
    ],
    "coverage_target": {
      "pools": 200
    },
    "coverage_from_feed_rows": true,
    "validation": "none"
  },
  "pool": {
    "title": "Single DeFi pool",
    "path": "/v1/lookup/pool",
    "family": "defi",
    "sources": [
      "Own on-chain lending market reads"
    ],
    "refresh_promised_sec": 1800,
    "free_probe": "/v1/free/defi/top-yields",
    "feed_id": "live_defi_yield_pools",
    "key_fields": [
      "yields.0.symbol",
      "yields.0.apy_pct",
      "snapshot_at"
    ],
    "coverage_target": {
      "pools": 500
    },
    "coverage_from_feed_rows": true,
    "validation": "none",
    "notes": "Coverage from live_defi_yield_pools feed row count; completeness from free top-yields sample fields."
  },
  "polymarket": {
    "title": "Polymarket odds",
    "path": "/v1/lookup/polymarket",
    "family": "prediction",
    "sources": [
      "Polymarket public API (gamma-api.polymarket.com)"
    ],
    "refresh_promised_sec": 1800,
    "free_probe": "/v1/free/feeds",
    "feed_id": "live_polymarket_odds",
    "key_fields": [
      "feeds"
    ],
    "coverage_target": {
      "markets": 100
    },
    "coverage_from_feed_rows": true,
    "validation": "none"
  },
  "token-risk": {
    "title": "Token risk (Base)",
    "path": "/v1/lookup/token-risk",
    "family": "onchain-risk",
    "sources": [
      "Own Base public-RPC contract/bytecode heuristics (onchain_risk)"
    ],
    "refresh_promised_sec": 1800,
    "free_probe": "/v1/free/feeds",
    "feed_id": "live_base_new_token_risk",
    "key_fields": [
      "feeds"
    ],
    "coverage_target": {
      "tokens": 40
    },
    "coverage_from_feed_rows": true,
    "validation": "none",
    "notes": "Holder count / CEX listing / verified-source fields intentionally unavailable."
  },
  "market-pulse": {
    "title": "Market pulse bundle",
    "path": "/v1/bundle/market-pulse",
    "family": "bundles",
    "sources": [
      "Chainlink on-chain prices",
      "Own DeFi pool/DEX reads",
      "Polymarket public API"
    ],
    "refresh_promised_sec": 1800,
    "free_probe": "/v1/free/crypto/top10",
    "feed_id": "live_crypto_top500_markets",
    "key_fields": [
      "coins.0.price_usd",
      "snapshot_at"
    ],
    "coverage_target": {
      "coins": 10
    },
    "coverage_from_feed_rows": true,
    "validation": "none"
  },
  "defi-intel": {
    "title": "DeFi intel bundle",
    "path": "/v1/bundle/defi-intel",
    "family": "bundles",
    "sources": [
      "Own on-chain protocol/pool/stablecoin reads"
    ],
    "refresh_promised_sec": 1800,
    "free_probe": "/v1/free/defi/top-yields",
    "feed_id": "live_defi_protocols_tvl",
    "key_fields": [
      "yields"
    ],
    "coverage_target": {
      "protocols": 200
    },
    "coverage_from_feed_rows": true,
    "validation": "none"
  },
  "price-moves": {
    "title": "Price-move alerts",
    "path": "/v1/alerts/price-moves",
    "family": "alerts",
    "sources": [
      "Chainlink on-chain price history cache"
    ],
    "refresh_promised_sec": 1800,
    "free_probe": "/v1/free/crypto/top10",
    "feed_id": "live_crypto_top500_markets",
    "key_fields": [
      "coins.0.change_24h_pct"
    ],
    "coverage_target": {
      "coins": 155
    },
    "coverage_from_feed_rows": true,
    "validation": "none"
  },
  "ad-cost-benchmarks": {
    "title": "Ad cost benchmarks",
    "path": "/v1/ads/cost-benchmarks",
    "family": "ads",
    "sources": [
      "Curated public marketing benchmark tables (WordStream et al., as_of stamped)"
    ],
    "refresh_promised_sec": 2592000,
    "free_probe": null,
    "key_fields": [],
    "coverage_target": {
      "platforms": 4
    },
    "static_coverage": {
      "platforms": 4
    },
    "validation": "none",
    "notes": "Curated public marketing benchmark tables; monthly refresh promise. Unmeasured age scores as catalog (not a dead feed).",
    "live_fetch": false,
    "catalog": true
  },
  "whales-moves": {
    "title": "Whale moves (Hyperliquid)",
    "path": "/v1/whales/moves",
    "family": "whales",
    "sources": [
      "Hyperliquid public trade stream / info API"
    ],
    "refresh_promised_sec": 120,
    "free_probe": "/v1/free/traders",
    "key_fields": [
      "top.0.address",
      "source"
    ],
    "coverage_target": {
      "markets": 40
    },
    "static_coverage": {
      "markets": 40
    },
    "validation": "none"
  },
  "whales-token": {
    "title": "Whale flow by token",
    "path": "/v1/whales/token",
    "family": "whales",
    "sources": [
      "Hyperliquid public info API"
    ],
    "refresh_promised_sec": 120,
    "free_probe": "/v1/free/traders",
    "key_fields": [
      "top"
    ],
    "coverage_target": {
      "markets": 40
    },
    "static_coverage": {
      "markets": 40
    },
    "validation": "none"
  },
  "whale-netflow": {
    "title": "Whale netflow signal",
    "path": "/v1/signals/whale-netflow",
    "family": "whales",
    "sources": [
      "Hyperliquid public trade stream (aggregated)"
    ],
    "refresh_promised_sec": 900,
    "free_probe": "/v1/free/traders",
    "key_fields": [
      "top"
    ],
    "coverage_target": {
      "venues": 1
    },
    "static_coverage": {
      "venues": 1
    },
    "validation": "none"
  },
  "traders-top": {
    "title": "Top traders (Hyperliquid)",
    "path": "/v1/traders/top",
    "family": "traders",
    "sources": [
      "Hyperliquid public leaderboard + info API"
    ],
    "refresh_promised_sec": 3600,
    "free_probe": "/v1/free/traders",
    "key_fields": [
      "top.0.address",
      "source"
    ],
    "coverage_target": {
      "traders_teaser": 3
    },
    "coverage_paths": {
      "traders_teaser": "top"
    },
    "validation": "none"
  },
  "traders-wallet": {
    "title": "Trader wallet profile",
    "path": "/v1/traders/wallet",
    "family": "traders",
    "sources": [
      "Hyperliquid public info API (positions/fills)"
    ],
    "refresh_promised_sec": 120,
    "free_probe": "/v1/free/traders",
    "key_fields": [
      "top",
      "source"
    ],
    "coverage_target": {
      "venue": 1
    },
    "static_coverage": {
      "venue": 1
    },
    "validation": "none"
  },
  "perp-funding": {
    "title": "Perp funding rates",
    "path": "/v1/perps/funding",
    "family": "perps",
    "sources": [
      "Hyperliquid public info API (metaAndAssetCtxs / funding)"
    ],
    "refresh_promised_sec": 3600,
    "free_probe": "/v1/free/funding",
    "key_fields": [
      "funding"
    ],
    "coverage_target": {
      "coins_teaser": 3
    },
    "coverage_paths": {
      "coins_teaser": "funding"
    },
    "validation": "none"
  },
  "stock-perps": {
    "title": "Stock perpetual futures (HIP-3)",
    "family": "perps",
    "paid_path": "/v1/perps/stocks",
    "free_path": "/v1/free/stock-perps.json",
    "sources": [
      "Hyperliquid public info API (metaAndAssetCtxs dex=xyz/HIP-3 builder DEXes, recentTrades)"
    ]
  },
  "token-verdict": {
    "title": "Token verdict",
    "path": "/v1/token/verdict",
    "family": "onchain-risk",
    "sources": [
      "Own multi-chain public RPC heuristics (onchain_risk + DEX liquidity reads)"
    ],
    "refresh_promised_sec": 1800,
    "free_probe": "/v1/free/feeds",
    "feed_id": "live_base_new_token_risk",
    "key_fields": [
      "feeds"
    ],
    "coverage_target": {
      "chains": 8
    },
    "static_coverage": {
      "chains": 8
    },
    "validation": "none",
    "notes": "Live RPC heuristics at request time; standing Base new-token risk feed is a coverage signal (~30m). No vendor risk APIs.",
    "live_fetch": true
  },
  "wallet-profile": {
    "title": "Wallet profile",
    "path": "/v1/wallet/profile",
    "family": "wallet",
    "sources": [
      "Public EVM/Solana RPC (nonce, balance, recent txs); own label lists"
    ],
    "refresh_promised_sec": 120,
    "free_probe": "/v1/free/wallet-label?address=0x28c6c06298d514db089934071355e5743bf21d60",
    "key_fields": [],
    "coverage_target": {
      "chains": 3
    },
    "static_coverage": {
      "chains": 3
    },
    "validation": "none",
    "live_fetch": true,
    "notes": "Live-fetch: data is read at request time; freshness scored from recent successful calls (usage logs) and/or self-probe success \u2014 not a standing feed age."
  },
  "wallet-label": {
    "title": "Wallet label",
    "path": "/v1/wallet-label",
    "family": "wallet",
    "sources": [
      "Own curated + scraped public label lists (exchanges, bridges, known entities)"
    ],
    "refresh_promised_sec": 21600,
    "free_probe": "/v1/free/wallet-label?address=0x28c6c06298d514db089934071355e5743bf21d60",
    "key_fields": [
      "match",
      "entity",
      "corpus_as_of"
    ],
    "coverage_target": {
      "labeled_addresses": 10000
    },
    "validation": "none",
    "coverage_paths": {
      "labeled_addresses": "labeled_addresses"
    },
    "notes": "Freshness = corpus rebuild time (OFAC/MEW/DefiLlama/eth-labels + store lists), not per-label source publication dates."
  },
  "wallet-label-bulk": {
    "title": "Wallet label bulk",
    "path": "/v1/wallet-label/bulk",
    "family": "wallet",
    "sources": [
      "Same as wallet-label"
    ],
    "refresh_promised_sec": 21600,
    "free_probe": "/v1/free/wallet-label?address=0x28c6c06298d514db089934071355e5743bf21d60",
    "key_fields": [
      "match",
      "entity",
      "corpus_as_of"
    ],
    "coverage_target": {
      "labeled_addresses": 10000
    },
    "validation": "none",
    "coverage_paths": {
      "labeled_addresses": "labeled_addresses"
    },
    "notes": "Freshness = corpus rebuild time (OFAC/MEW/DefiLlama/eth-labels + store lists), not per-label source publication dates."
  },
  "token-holders": {
    "title": "Token holders (Solana)",
    "path": "/v1/token/holders",
    "family": "onchain-risk",
    "sources": [
      "Solana public RPC getTokenLargestAccounts / token program"
    ],
    "refresh_promised_sec": 120,
    "free_probe": null,
    "key_fields": [],
    "coverage_target": {
      "chains": 1
    },
    "static_coverage": {
      "chains": 1
    },
    "validation": "none",
    "notes": "Solana-only; EVM holder concentration not offered without a licensed indexer. Live-fetch: data is read at request time; freshness scored from recent successful calls (usage logs) and/or self-probe success \u2014 not a standing feed age.",
    "live_fetch": true
  },
  "counterparty-check": {
    "title": "Counterparty check",
    "path": "/v1/check/counterparty",
    "family": "onchain-risk",
    "sources": [
      "Composes wallet-profile + token-verdict + own labels (public RPC)"
    ],
    "refresh_promised_sec": 120,
    "free_probe": null,
    "key_fields": [],
    "coverage_target": {
      "chains": 3
    },
    "static_coverage": {
      "chains": 3
    },
    "validation": "none",
    "live_fetch": true,
    "notes": "Live-fetch: data is read at request time; freshness scored from recent successful calls (usage logs) and/or self-probe success \u2014 not a standing feed age."
  },
  "dd-report": {
    "title": "Due-diligence report",
    "path": "/v1/dd/report",
    "family": "onchain-risk",
    "sources": [
      "Composes on-chain risk, whales, yields, labels (public RPC + Hyperliquid)"
    ],
    "refresh_promised_sec": 120,
    "free_probe": null,
    "key_fields": [],
    "coverage_target": {
      "composed_signals": 5
    },
    "static_coverage": {
      "composed_signals": 5
    },
    "validation": "none",
    "live_fetch": true,
    "notes": "Live-fetch: data is read at request time; freshness scored from recent successful calls (usage logs) and/or self-probe success \u2014 not a standing feed age."
  },
  "dd-quick": {
    "title": "Due-diligence quick",
    "path": "/v1/dd/quick",
    "family": "onchain-risk",
    "sources": [
      "Subset of dd-report"
    ],
    "refresh_promised_sec": 120,
    "free_probe": null,
    "key_fields": [],
    "coverage_target": {
      "composed_signals": 3
    },
    "static_coverage": {
      "composed_signals": 3
    },
    "validation": "none",
    "live_fetch": true,
    "notes": "Live-fetch: data is read at request time; freshness scored from recent successful calls (usage logs) and/or self-probe success \u2014 not a standing feed age."
  },
  "trust-agent": {
    "title": "Trust agent bundle",
    "path": "/v1/trust/agent",
    "family": "onchain-risk",
    "sources": [
      "counterparty-check + wallet-profile + token-verdict"
    ],
    "refresh_promised_sec": 120,
    "free_probe": null,
    "key_fields": [],
    "coverage_target": {
      "composed_signals": 3
    },
    "static_coverage": {
      "composed_signals": 3
    },
    "validation": "none",
    "live_fetch": true,
    "notes": "Live-fetch: data is read at request time; freshness scored from recent successful calls (usage logs) and/or self-probe success \u2014 not a standing feed age."
  },
  "address-poisoning": {
    "title": "Address poisoning check",
    "path": "/v1/lookup/address-poisoning",
    "family": "onchain-risk",
    "sources": [
      "Public EVM RPC recent Transfer logs (lookalike dust)"
    ],
    "refresh_promised_sec": 120,
    "free_probe": "/v1/free/address-poisoning",
    "key_fields": [],
    "coverage_target": {
      "chains": 1
    },
    "static_coverage": {
      "chains": 1
    },
    "validation": "none",
    "live_fetch": true,
    "notes": "Live-fetch: data is read at request time; freshness scored from recent successful calls (usage logs) and/or self-probe success \u2014 not a standing feed age."
  },
  "intel-risk-check": {
    "title": "Security intel risk-check",
    "path": "/v1/intel/risk-check",
    "family": "security",
    "sources": [
      "Own ~57k wallet labels (OFAC/sanctions, scam/phishing, exploit, mixer, exchange)",
      "Live hack-tracker attribution",
      "Base USDC address-poisoning flags (public RPC)"
    ],
    "refresh_promised_sec": 120,
    "free_probe": "/v1/free/intel/risk-check",
    "key_fields": [],
    "coverage_target": {
      "signals": 3
    },
    "static_coverage": {
      "signals": 3
    },
    "validation": "none",
    "live_fetch": true,
    "notes": "Live-fetch: labels+hack+sanctions+poisoning into one verdict. 3 free/IP/UTC-day then paid. Freshness from usage logs / self-probe."
  },
  "intel-risk-batch": {
    "title": "Security intel risk-check (batch)",
    "path": "/v1/intel/risk-check/batch",
    "family": "security",
    "sources": [
      "Same engine as intel-risk-check (labels + hack-tracker; batch skips live poisoning RPC)"
    ],
    "refresh_promised_sec": 120,
    "free_probe": "/v1/free/intel/risk-check",
    "key_fields": [],
    "coverage_target": {
      "max_addresses": 50
    },
    "static_coverage": {
      "max_addresses": 50
    },
    "validation": "none",
    "live_fetch": true,
    "notes": "Live-fetch batch up to 50 addresses. Freshness from usage logs / self-probe."
  },
  "bundle-stolen-funds": {
    "title": "Stolen-funds download bundle",
    "path": "/v1/bundles/stolen-funds/download",
    "family": "security",
    "sources": [
      "Own labeled hacker_exploit/scam_phishing/sanctioned/mixer corpus",
      "Live hack-tracker attributed/linked wallets"
    ],
    "refresh_promised_sec": 900,
    "free_probe": "/v1/free/hack-tracker",
    "key_fields": [],
    "coverage_target": {
      "addresses": 1700
    },
    "static_coverage": {
      "addresses": 1700
    },
    "validation": "none",
    "live_fetch": true,
    "notes": "Premium pay-per-download ($12); not covered by day/month passes. Coverage target is approximate corpus size."
  },
  "regime-now": {
    "title": "Market regime now",
    "path": "/v1/regime/now",
    "family": "regime",
    "sources": [
      "FRED public series",
      "Own on-chain reads",
      "Hyperliquid public metrics"
    ],
    "refresh_promised_sec": 3900,
    "free_probe": "/v1/free/regime",
    "key_fields": [
      "regime",
      "score_0_100",
      "as_of"
    ],
    "coverage_target": {
      "components": 9
    },
    "static_coverage": {
      "components": 9
    },
    "validation": "none",
    "notes": "Free teaser is delayed \u22651h by design; paid /v1/regime/* refreshes every 15m. Quality grades free-probe age against the 1h delay promise.",
    "use_live_state": "regime"
  },
  "regime-full": {
    "title": "Market regime full",
    "path": "/v1/regime/full",
    "family": "regime",
    "sources": [
      "Same as regime-now (full component detail)"
    ],
    "refresh_promised_sec": 3900,
    "free_probe": "/v1/free/regime",
    "key_fields": [
      "regime",
      "score_0_100"
    ],
    "coverage_target": {
      "components": 9
    },
    "static_coverage": {
      "components": 9
    },
    "validation": "none",
    "notes": "Free teaser is delayed \u22651h by design; paid /v1/regime/* refreshes every 15m. Quality grades free-probe age against the 1h delay promise.",
    "use_live_state": "regime"
  },
  "ecom-product": {
    "title": "E-com product lookup",
    "path": "/v1/ecom/product",
    "family": "ecom",
    "sources": [
      "Public Shopify /products.json crawls (own Umbrel crawler to ecom.sqlite)"
    ],
    "refresh_promised_sec": 86400,
    "free_probe": "/v1/free/ecom/stores",
    "key_fields": [
      "stores"
    ],
    "coverage_target": {
      "stores": 180
    },
    "validation": "ecom_placeholder",
    "notes": ""
  },
  "ecom-changes": {
    "title": "E-com price/stock changes",
    "path": "/v1/ecom/changes",
    "family": "ecom",
    "sources": [
      "Own daily Shopify snapshot diffs"
    ],
    "refresh_promised_sec": 86400,
    "free_probe": "/v1/free/ecom/stores",
    "key_fields": [
      "stores"
    ],
    "coverage_target": {
      "stores": 180
    },
    "validation": "ecom_placeholder",
    "notes": ""
  },
  "ecom-snapshot": {
    "title": "E-com brand/category snapshot",
    "path": "/v1/ecom/snapshot",
    "family": "ecom",
    "sources": [
      "Own Shopify index"
    ],
    "refresh_promised_sec": 86400,
    "free_probe": "/v1/free/ecom/stores",
    "key_fields": [
      "stores"
    ],
    "coverage_target": {
      "stores": 180
    },
    "validation": "ecom_placeholder",
    "notes": ""
  },
  "ecom-catalog": {
    "title": "E-com full store catalog",
    "path": "/v1/ecom/catalog",
    "family": "ecom",
    "sources": [
      "Own Shopify /products.json snapshots"
    ],
    "refresh_promised_sec": 86400,
    "free_probe": "/v1/free/ecom/stores",
    "key_fields": [
      "stores.0.domain"
    ],
    "coverage_target": {
      "stores": 180
    },
    "coverage_paths": {
      "stores": "stores"
    },
    "validation": "ecom_placeholder"
  },
  "stocks-price": {
    "title": "Stock/ETF price (Chainlink)",
    "path": "/v1/stocks/price",
    "family": "stocks",
    "sources": [
      "Chainlink equity/ETF Data Feeds on-chain (Ethereum)"
    ],
    "refresh_promised_sec": 1800,
    "free_probe": "/v1/free/stocks",
    "feed_id": "live_us_stocks_etfs",
    "key_fields": [],
    "coverage_target": {
      "tickers": 15
    },
    "coverage_from_feed_rows": true,
    "validation": "none",
    "notes": "No Yahoo Finance. Coverage limited to Chainlink-listed tickers."
  },
  "stocks-fundamentals": {
    "title": "Stock fundamentals (SEC XBRL)",
    "path": "/v1/stocks/fundamentals",
    "family": "stocks",
    "sources": [
      "SEC EDGAR companyfacts XBRL (data.sec.gov)"
    ],
    "refresh_promised_sec": 21600,
    "free_probe": "/v1/free/stocks",
    "key_fields": [],
    "coverage_target": {
      "tickers": 10000
    },
    "static_coverage": {
      "tickers": 10000
    },
    "validation": "none"
  },
  "stocks-filings": {
    "title": "SEC filings",
    "path": "/v1/stocks/filings",
    "family": "stocks",
    "sources": [
      "SEC EDGAR submissions API"
    ],
    "refresh_promised_sec": 21600,
    "free_probe": "/v1/free/stocks",
    "key_fields": [],
    "coverage_target": {
      "tickers": 10000
    },
    "static_coverage": {
      "tickers": 10000
    },
    "validation": "none"
  },
  "stocks-insiders": {
    "title": "Insider trades (Form 4)",
    "path": "/v1/stocks/insiders",
    "family": "stocks",
    "sources": [
      "SEC EDGAR Form 4 XML"
    ],
    "refresh_promised_sec": 21600,
    "free_probe": "/v1/free/stocks",
    "key_fields": [],
    "coverage_target": {
      "tickers": 10000
    },
    "static_coverage": {
      "tickers": 10000
    },
    "validation": "none"
  },
  "meme-trending": {
    "title": "Meme trending",
    "path": "/v1/meme/trending",
    "family": "meme",
    "sources": [
      "Own DEX Swap/PoolCreated logs via public RPC (Base, BNB) + pump.fun Solana logs; Chainlink ETH/BNB/SOL USD"
    ],
    "refresh_promised_sec": 900,
    "free_probe": "/v1/free/meme-trending",
    "feed_id": "live_new_pools_base_solana",
    "key_fields": [
      "trending"
    ],
    "coverage_target": {
      "chains": 3
    },
    "static_coverage": {
      "chains": 3
    },
    "validation": "none",
    "notes": "No GeckoTerminal/CoinGecko."
  },
  "meme-new-pools": {
    "title": "New DEX pools",
    "path": "/v1/meme/new-pools",
    "family": "meme",
    "sources": [
      "Own factory event reads (Base/BNB public RPC)"
    ],
    "refresh_promised_sec": 900,
    "free_probe": "/v1/free/meme-trending",
    "feed_id": "live_new_pools_base_solana",
    "key_fields": [
      "trending"
    ],
    "coverage_target": {
      "new_pools": 200
    },
    "coverage_from_feed_rows": true,
    "validation": "none"
  },
  "pumpfun": {
    "title": "Pump.fun feed",
    "path": "/v1/meme/pumpfun",
    "family": "meme",
    "sources": [
      "Solana public RPC: pump.fun program logs + bonding-curve accounts"
    ],
    "refresh_promised_sec": 900,
    "free_probe": "/v1/free/meme-trending",
    "key_fields": [
      "trending"
    ],
    "coverage_target": {
      "chains": 1
    },
    "static_coverage": {
      "chains": 1
    },
    "validation": "none"
  },
  "meme-history": {
    "title": "Meme price history",
    "path": "/v1/meme/history",
    "family": "meme",
    "sources": [
      "Own 15-min on-chain swap snapshots (meme.sqlite)"
    ],
    "refresh_promised_sec": 900,
    "free_probe": "/v1/free/meme-trending",
    "key_fields": [
      "trending"
    ],
    "coverage_target": {
      "history_days": 30
    },
    "static_coverage": {
      "history_days": 30
    },
    "validation": "none"
  },
  "staking-yields": {
    "title": "Staking / LST yields",
    "path": "/v1/yields/staking",
    "family": "defi",
    "sources": [
      "Own on-chain LST exchange-rate eth_call history (stETH, rETH, cbETH, weETH, ...)"
    ],
    "refresh_promised_sec": 3600,
    "free_probe": "/v1/free/staking-yields",
    "key_fields": [
      "yields"
    ],
    "coverage_target": {
      "lsts": 8
    },
    "coverage_paths": {
      "lsts": "yields"
    },
    "validation": "none"
  },
  "pendle-fixed": {
    "title": "Pendle fixed yields",
    "path": "/v1/yields/pendle",
    "family": "defi",
    "sources": [
      "Own on-chain Pendle market _storage().lastLnImpliedRate (Ethereum/Arbitrum/Base)"
    ],
    "refresh_promised_sec": 3600,
    "free_probe": "/v1/free/staking-yields",
    "key_fields": [
      "yields"
    ],
    "coverage_target": {
      "chains": 3
    },
    "static_coverage": {
      "chains": 3
    },
    "validation": "none"
  },
  "crypto-treasuries": {
    "title": "Corporate crypto treasuries",
    "path": "/v1/lookup/crypto-treasuries",
    "family": "rwa",
    "sources": [
      "SEC EDGAR filings + own parsing; USD via Chainlink"
    ],
    "refresh_promised_sec": 86400,
    "free_probe": "/v1/free/crypto-treasuries",
    "key_fields": [
      "companies_tracked",
      "top10"
    ],
    "coverage_target": {
      "companies": 20
    },
    "coverage_paths": {
      "companies": "companies_tracked"
    },
    "validation": "none"
  },
  "tokenized-stocks": {
    "title": "Tokenized stocks",
    "path": "/v1/lookup/tokenized-stocks",
    "family": "rwa",
    "sources": [
      "Own on-chain supply/DEX price reads (Ondo/xStocks contracts); share price via Chainlink"
    ],
    "refresh_promised_sec": 1800,
    "free_probe": "/v1/free/tokenized-stocks",
    "key_fields": [
      "tokens_tracked",
      "top10",
      "updated_at"
    ],
    "coverage_target": {
      "tokens": 10
    },
    "coverage_paths": {
      "tokens": "tokens_tracked"
    },
    "validation": "none"
  },
  "tokenized-real-estate": {
    "title": "Tokenized real estate (RealT)",
    "path": "/v1/lookup/tokenized-real-estate",
    "family": "rwa",
    "sources": [
      "Own Gnosis public-RPC reads of RealT RealToken contracts"
    ],
    "refresh_promised_sec": 3600,
    "free_probe": "/v1/free/tokenized-real-estate",
    "key_fields": [
      "summary",
      "top10",
      "source"
    ],
    "coverage_target": {
      "properties": 10
    },
    "coverage_paths": {
      "properties": "top10"
    },
    "validation": "none"
  },
  "hack-tracker": {
    "title": "Hack / stolen-funds tracker",
    "path": "/v1/lookup/hack-tracker",
    "family": "security",
    "sources": [
      "Own Ethereum public-RPC balance/tx reads of curated incident wallets; public timelines"
    ],
    "refresh_promised_sec": 900,
    "free_probe": "/v1/free/hack-tracker",
    "key_fields": [
      "incident",
      "as_of"
    ],
    "coverage_target": {
      "incidents": 1
    },
    "static_coverage": {
      "incidents": 1
    },
    "validation": "none",
    "notes": "Coverage is incident-scoped; not a global hack DB."
  },
  "binance-flows": {
    "title": "Binance exchange flows",
    "path": "/v1/lookup/binance-flows",
    "family": "security",
    "sources": [
      "Own multi-chain public-RPC Transfer logs to/from known Binance deposit clusters"
    ],
    "refresh_promised_sec": 900,
    "free_probe": "/v1/free/binance-flows",
    "key_fields": [
      "as_of",
      "per_chain",
      "sources"
    ],
    "coverage_target": {
      "chains": 4
    },
    "coverage_paths": {
      "chains": "per_chain"
    },
    "validation": "none"
  },
  "binance-flows-report": {
    "title": "Binance flows report",
    "path": "/v1/lookup/binance-flows/report",
    "family": "security",
    "sources": [
      "Same as binance-flows (deeper window)"
    ],
    "refresh_promised_sec": 900,
    "free_probe": "/v1/free/binance-flows",
    "key_fields": [
      "as_of",
      "per_chain"
    ],
    "coverage_target": {
      "chains": 4
    },
    "coverage_paths": {
      "chains": "per_chain"
    },
    "validation": "none"
  },
  "company": {
    "title": "Crypto company / project enrich",
    "path": "/v1/company",
    "family": "company",
    "sources": [
      "Own curated project DB + live homepage crawls (public sites)"
    ],
    "refresh_promised_sec": 3600,
    "free_probe": "/v1/free/company/coverage",
    "key_fields": [
      "projects",
      "avg_completeness",
      "as_of"
    ],
    "coverage_target": {
      "projects": 8000
    },
    "coverage_paths": {
      "projects": "projects"
    },
    "validation": "company_fill"
  },
  "domain": {
    "title": "Company-from-domain enrich",
    "path": "/v1/lookup/domain",
    "family": "company",
    "sources": [
      "Live homepage crawl of the requested domain (public HTML)"
    ],
    "refresh_promised_sec": 120,
    "free_probe": "/v1/free/company/coverage",
    "key_fields": [
      "homepage_crawl"
    ],
    "coverage_target": {
      "sites_crawled": 4000
    },
    "validation": "company_fill"
  },
  "candles": {
    "title": "OHLCV candles + indicators",
    "path": "/v1/lookup/candles",
    "family": "market-data",
    "sources": [
      "OKX public API",
      "Binance.US public API",
      "Kraken public API"
    ],
    "refresh_promised_sec": 120,
    "free_probe": "/v1/free/candles?symbol=ETHUSDT&interval=1h",
    "key_fields": [],
    "coverage_target": {
      "exchanges": 3
    },
    "static_coverage": {
      "exchanges": 3
    },
    "validation": "none",
    "notes": "Falls across OKX then Binance.US then Kraken."
  },
  "ens": {
    "title": "ENS resolve",
    "path": "/v1/lookup/ens",
    "family": "identity",
    "sources": [
      "ensdata.net public API",
      "Ethereum public RPC fallback"
    ],
    "refresh_promised_sec": 120,
    "free_probe": "/v1/free/ens?name=vitalik.eth",
    "key_fields": [],
    "coverage_target": {
      "networks": 1
    },
    "static_coverage": {
      "networks": 1
    },
    "validation": "none"
  },
  "evm-address": {
    "title": "EVM address type",
    "path": "/v1/lookup/evm-address",
    "family": "identity",
    "sources": [
      "Public RPC eth_getCode / eth_getTransactionCount (Base/Eth/Arb/BSC/Polygon/Optimism/Avalanche)"
    ],
    "refresh_promised_sec": 120,
    "free_probe": null,
    "key_fields": [],
    "coverage_target": {
      "chains": 7
    },
    "static_coverage": {
      "chains": 7
    },
    "validation": "none",
    "live_fetch": true,
    "notes": "Live-fetch: data is read at request time; freshness scored from recent successful calls (usage logs) and/or self-probe success \u2014 not a standing feed age."
  },
  "evm-balance": {
    "title": "EVM multi-chain balances",
    "path": "/v1/lookup/evm-balance",
    "family": "identity",
    "sources": [
      "Public RPC balanceOf + native balance; USD via Chainlink"
    ],
    "refresh_promised_sec": 120,
    "free_probe": null,
    "key_fields": [],
    "coverage_target": {
      "chains": 7,
      "stablecoins": 4
    },
    "static_coverage": {
      "chains": 7,
      "stablecoins": 4
    },
    "validation": "none",
    "live_fetch": true,
    "notes": "Live-fetch: data is read at request time; freshness scored from recent successful calls (usage logs) and/or self-probe success \u2014 not a standing feed age."
  },
  "fx": {
    "title": "FX + crypto convert",
    "path": "/v1/lookup/fx",
    "family": "market-data",
    "sources": [
      "frankfurter.app (ECB)",
      "Chainlink crypto USD feeds"
    ],
    "refresh_promised_sec": 86400,
    "free_probe": "/v1/free/fx",
    "key_fields": [
      "amount",
      "from",
      "to",
      "sources",
      "as_of"
    ],
    "coverage_target": {
      "fiats": 30
    },
    "static_coverage": {
      "fiats": 30
    },
    "validation": "none"
  },
  "news": {
    "title": "News headlines",
    "path": "/v1/lookup/news",
    "family": "web",
    "sources": [
      "Google News RSS"
    ],
    "refresh_promised_sec": 120,
    "free_probe": "/v1/free/news?q=bitcoin",
    "key_fields": [
      "items",
      "source",
      "as_of"
    ],
    "coverage_target": {
      "items_teaser": 3
    },
    "coverage_paths": {
      "items_teaser": "items"
    },
    "validation": "none"
  },
  "web-search": {
    "title": "Web search",
    "path": "/v1/lookup/web-search",
    "family": "web",
    "sources": [
      "Bing HTML (primary)",
      "DuckDuckGo HTML (fallback)"
    ],
    "refresh_promised_sec": 120,
    "free_probe": null,
    "key_fields": [],
    "coverage_target": {
      "engines": 2
    },
    "static_coverage": {
      "engines": 2
    },
    "validation": "none",
    "notes": "DDG often challenges bots; Bing is the reliable path. Live-fetch: data is read at request time; freshness scored from recent successful calls (usage logs) and/or self-probe success \u2014 not a standing feed age.",
    "live_fetch": true
  },
  "pdf": {
    "title": "PDF text extract",
    "path": "/v1/lookup/pdf",
    "family": "web",
    "sources": [
      "Fetched PDF URL + poppler pdftotext"
    ],
    "refresh_promised_sec": 120,
    "free_probe": null,
    "key_fields": [],
    "coverage_target": {
      "max_pages": 30
    },
    "static_coverage": {
      "max_pages": 30
    },
    "validation": "none",
    "live_fetch": true,
    "notes": "Live-fetch: data is read at request time; freshness scored from recent successful calls (usage logs) and/or self-probe success \u2014 not a standing feed age."
  },
  "youtube-transcript": {
    "title": "YouTube transcript",
    "path": "/v1/lookup/youtube-transcript",
    "family": "web",
    "sources": [
      "YouTube Innertube player API + timedtext/srv3 captions"
    ],
    "refresh_promised_sec": 120,
    "free_probe": null,
    "key_fields": [],
    "coverage_target": {
      "caption_formats": 2
    },
    "static_coverage": {
      "caption_formats": 2
    },
    "validation": "none",
    "notes": "Subject to YouTube rate limits (HTTP 429). Live-fetch: data is read at request time; freshness scored from recent successful calls (usage logs) and/or self-probe success \u2014 not a standing feed age.",
    "live_fetch": true
  },
  "market-overview": {
    "title": "Crypto market overview",
    "path": "/v1/lookup/market-overview",
    "family": "market-data",
    "sources": [
      "alternative.me Fear & Greed",
      "Chainlink BTC/ETH",
      "Own market cache"
    ],
    "refresh_promised_sec": 1800,
    "free_probe": "/v1/free/market-overview",
    "key_fields": [
      "fear_greed",
      "btc",
      "eth",
      "as_of"
    ],
    "coverage_target": {
      "indices": 1,
      "majors": 2
    },
    "static_coverage": {
      "indices": 1,
      "majors": 2
    },
    "validation": "none"
  },
  "ping": {
    "title": "Payment ping",
    "path": "/v1/ping",
    "family": "service",
    "sources": [
      "Local (settlement test)"
    ],
    "refresh_promised_sec": 120,
    "free_probe": "/v1/free/ping",
    "key_fields": [],
    "coverage_target": {
      "always": 1
    },
    "static_coverage": {
      "always": 1
    },
    "validation": "none"
  },
  "pass-day": {
    "title": "Day pass",
    "path": "/v1/pass/day",
    "family": "service",
    "sources": [
      "Local entitlement"
    ],
    "refresh_promised_sec": 120,
    "free_probe": "/v1/pass/status",
    "key_fields": [],
    "coverage_target": {
      "always": 1
    },
    "static_coverage": {
      "always": 1
    },
    "validation": "none",
    "live_fetch": true,
    "notes": "Live-fetch: data is read at request time; freshness scored from recent successful calls (usage logs) and/or self-probe success \u2014 not a standing feed age."
  },
  "pass-month": {
    "title": "Month pass",
    "path": "/v1/pass/month",
    "family": "service",
    "sources": [
      "Local entitlement"
    ],
    "refresh_promised_sec": 120,
    "free_probe": "/v1/pass/status",
    "key_fields": [],
    "coverage_target": {
      "always": 1
    },
    "static_coverage": {
      "always": 1
    },
    "validation": "none",
    "live_fetch": true,
    "notes": "Live-fetch: data is read at request time; freshness scored from recent successful calls (usage logs) and/or self-probe success \u2014 not a standing feed age."
  },
  "alerts-week": {
    "title": "Alerts subscription (week)",
    "path": "/v1/alerts/subscribe/week",
    "family": "alerts",
    "sources": [
      "Own hack/whale detectors to webhook"
    ],
    "refresh_promised_sec": 120,
    "free_probe": "/v1/free/alerts",
    "key_fields": [],
    "coverage_target": {
      "topics": 2
    },
    "static_coverage": {
      "topics": 2
    },
    "validation": "none"
  },
  "alerts-month": {
    "title": "Alerts subscription (month)",
    "path": "/v1/alerts/subscribe/month",
    "family": "alerts",
    "sources": [
      "Own hack/whale detectors to webhook"
    ],
    "refresh_promised_sec": 120,
    "free_probe": "/v1/free/alerts",
    "key_fields": [],
    "coverage_target": {
      "topics": 2
    },
    "static_coverage": {
      "topics": 2
    },
    "validation": "none"
  },
  "scrape-text": {
    "title": "Page to clean text",
    "path": "/v1/scrape/text",
    "family": "scrape",
    "sources": [
      "Live HTTP fetch of requested URL + boilerplate strip"
    ],
    "refresh_promised_sec": 120,
    "free_probe": null,
    "key_fields": [],
    "coverage_target": {
      "any_url": 1
    },
    "static_coverage": {
      "any_url": 1
    },
    "validation": "none",
    "in_lookups": false,
    "live_fetch": true,
    "notes": "Live-fetch: data is read at request time; freshness scored from recent successful calls (usage logs) and/or self-probe success \u2014 not a standing feed age."
  },
  "scrape": {
    "title": "Structured scrape",
    "path": "/v1/scrape",
    "family": "scrape",
    "sources": [
      "Live HTTP fetch + JSON-LD/tables/links extract"
    ],
    "refresh_promised_sec": 120,
    "free_probe": null,
    "key_fields": [],
    "coverage_target": {
      "any_url": 1
    },
    "static_coverage": {
      "any_url": 1
    },
    "validation": "none",
    "in_lookups": false,
    "live_fetch": true,
    "notes": "Live-fetch: data is read at request time; freshness scored from recent successful calls (usage logs) and/or self-probe success \u2014 not a standing feed age."
  },
  "batch-scrape": {
    "title": "Batch scrape",
    "path": "/v1/batch-scrape",
    "family": "scrape",
    "sources": [
      "Same as scrape, up to 100 URLs"
    ],
    "refresh_promised_sec": 120,
    "free_probe": null,
    "key_fields": [],
    "coverage_target": {
      "max_urls": 100
    },
    "static_coverage": {
      "max_urls": 100
    },
    "validation": "none",
    "in_lookups": false,
    "live_fetch": true,
    "notes": "Live-fetch: data is read at request time; freshness scored from recent successful calls (usage logs) and/or self-probe success \u2014 not a standing feed age."
  }
}''')


def _iso(ts=None):
    return datetime.fromtimestamp(ts or time.time(), tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_ts(v):
    if v is None:
        return None
    try:
        if isinstance(v, (int, float)):
            return float(v / 1000 if v > 1e12 else v)
        if isinstance(v, str) and len(v) >= 10:
            d = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return d.timestamp()
    except Exception:
        return None
    return None


def _get_path(obj, dotted):
    cur = obj
    for part in dotted.split("."):
        if cur is None:
            return None
        if part.isdigit():
            try:
                cur = cur[int(part)]
            except Exception:
                return None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def letter_for(score):
    for thr, let in LETTER:
        if score >= thr:
            return let
    return "F"


def freshness_score(age_sec, promised):
    if age_sec is None:
        # Long-interval catalog/curated products: unmeasured is not a feed failure
        if promised and promised >= 7 * 86400:
            return 75.0, {"age_seconds": None, "promised_seconds": promised, "note": "unmeasured_catalog"}
        return 40.0, {"age_seconds": None, "promised_seconds": promised, "note": "unmeasured"}
    if promised <= 0:
        promised = R30
    if age_sec <= promised:
        s = 100.0
    elif age_sec >= 2 * promised:
        s = 0.0
    else:
        s = 100.0 * (1.0 - (age_sec - promised) / promised)
    return round(s, 1), {"age_seconds": int(age_sec), "promised_seconds": promised}


def completeness_score(payload, key_fields):
    if not key_fields:
        if isinstance(payload, dict) and payload and not payload.get("detail"):
            return 85.0, {"fields_checked": 0, "note": "no key_fields declared; non-empty response"}
        return 50.0, {"fields_checked": 0, "note": "no key_fields; empty/error"}
    ok = 0
    detail = {}
    for f in key_fields:
        v = _get_path(payload, f)
        present = v is not None and v != "" and v != [] and v != {}
        detail[f] = bool(present)
        if present:
            ok += 1
    pct = 100.0 * ok / len(key_fields)
    return round(pct, 1), {"fields_checked": len(key_fields), "fields_present": ok, "fields": detail}


def coverage_score(measured, targets):
    if not targets:
        return 70.0, {"note": "no coverage target"}
    parts, detail = [], {}
    for k, tgt in targets.items():
        m = measured.get(k)
        if m is None:
            detail[k] = {"measured": None, "target": tgt, "ratio": None}
            parts.append(50.0)
            continue
        ratio = float(m) / float(tgt) if tgt else 1.0
        s = max(0.0, min(100.0, 100.0 * ratio))
        parts.append(s)
        detail[k] = {"measured": m, "target": tgt, "ratio": round(ratio, 3)}
    return round(sum(parts) / len(parts), 1), detail


def uptime_score(rate, probe_ok):
    if rate is not None:
        return round(100.0 * rate, 1), {"success_rate_24h": round(rate, 4), "source": "usage.db"}
    if probe_ok is True:
        return 95.0, {"success_rate_24h": None, "source": "probe_ok"}
    if probe_ok is False:
        return 20.0, {"success_rate_24h": None, "source": "probe_fail"}
    return 60.0, {"success_rate_24h": None, "source": "unknown"}


def validation_score(kind, ctx):
    if kind == "ecom_placeholder":
        pq = ctx.get("ecom_price_quality") or {}
        flagged = pq.get("flagged_pct")
        if flagged is None:
            return 70.0, {"note": "ecom price-quality unavailable", **pq}
        s = max(0.0, min(100.0, 100.0 * (1.0 - float(flagged) / 20.0)))
        return round(s, 1), {"flagged_pct": flagged, "rows_checked": pq.get("rows_checked")}
    if kind == "company_fill":
        avg = ctx.get("company_avg_completeness")
        if avg is None:
            return 60.0, {"note": "company coverage unavailable"}
        return round(float(avg), 1), {"avg_completeness": avg, "projects": ctx.get("company_projects")}
    return 100.0, {"note": "no extra validation for this product"}


def combine(parts):
    return round(sum(WEIGHTS[k] * float(parts[k]) for k in WEIGHTS), 1)


_lock = threading.Lock()
_state = {"grades": {}, "as_of": None, "rubric": None, "last_error": None}
_started = False


def _http_get(path, timeout=25):
    url = PROBE_BASE + path
    req = urllib.request.Request(url, headers={"User-Agent": "ScraplingQuality/1.0", "Accept": "application/json", "X-Internal-Monitor": "1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, None
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            body = None
        return e.code, body
    except Exception as e:
        log.debug("probe %s failed: %s", path, e)
        return 0, None


def _extract_as_of(payload):
    if not isinstance(payload, dict):
        return None
    for k in ("corpus_as_of", "index_built", "as_of", "snapshot_at", "updated_at", "read_at", "fx_date", "generated_at", "last_crawl"):
        t = _parse_ts(payload.get(k))
        if t:
            return t
    dao = payload.get("data_as_of")
    if isinstance(dao, dict):
        t = _parse_ts(dao.get("unix") or dao.get("timestamp_utc") or dao.get("timestamp"))
        if t:
            return t
    stores = payload.get("stores")
    if isinstance(stores, list) and stores:
        times = [_parse_ts(s.get("last_crawl")) for s in stores if isinstance(s, dict)]
        times = [t for t in times if t]
        if times:
            return max(times)
    return None


def _count_at(payload, path):
    v = _get_path(payload, path) if path else None
    if isinstance(v, list):
        return float(len(v))
    if isinstance(v, dict):
        return float(len(v))
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _usage_stats(window_sec=86400):
    """Return {route: {rate, ok, n, last_ok_ts, last_ts, avg_latency_ms}} from usage.db."""
    if not os.path.isfile(USAGE_DB):
        return {}
    cutoff = time.time() - window_sec
    out = {}
    try:
        con = sqlite3.connect(USAGE_DB, timeout=3)
        cols = {r[1] for r in con.execute("PRAGMA table_info(calls)")}
        lat_col = "latency_ms" if "latency_ms" in cols else ("ms" if "ms" in cols else None)
        sql = "SELECT route, status, ts" + (f", {lat_col}" if lat_col else ", NULL") + " FROM calls WHERE ts>=? AND via<>'test'"
        for row in con.execute(sql, (cutoff,)):
            route, status, ts, lat = row[0], row[1], row[2], row[3]
            if not route:
                continue
            st = int(status)
            ok = 1 if (200 <= st < 500) else 0
            b = out.setdefault(route, {"ok": 0, "n": 0, "last_ok_ts": None, "last_ts": None, "lat_sum": 0.0, "lat_n": 0})
            b["n"] += 1
            b["ok"] += ok
            if ts is not None:
                b["last_ts"] = max(b["last_ts"] or 0, float(ts))
                if ok:
                    b["last_ok_ts"] = max(b["last_ok_ts"] or 0, float(ts))
            if lat is not None:
                try:
                    b["lat_sum"] += float(lat)
                    b["lat_n"] += 1
                except Exception:
                    pass
        con.close()
    except Exception as e:
        log.debug("usage.db read: %s", e)
        return {}
    for r, b in out.items():
        b["rate"] = (b["ok"] / b["n"]) if b["n"] else 0.0
        b["avg_latency_ms"] = (b["lat_sum"] / b["lat_n"]) if b["lat_n"] else None
    return out


def _usage_rates(window_sec=86400):
    return {r: v["rate"] for r, v in _usage_stats(window_sec).items()}


def _rate_for(path, rates):
    if path in rates:
        return rates[path]
    best, best_len = None, -1
    for r, v in rates.items():
        if path.startswith(r.rstrip("/")) or r.startswith(path):
            if len(r) > best_len:
                best, best_len = v, len(r)
    return best


def _stats_for(path, stats):
    if path in stats:
        return stats[path]
    best, best_len = None, -1
    for r, v in stats.items():
        if path.startswith(r.rstrip("/")) or r.startswith(path):
            if len(r) > best_len:
                best, best_len = v, len(r)
    return best


def live_fetch_freshness(age_from_usage, promised, probe_ok, latency_ms=None, success_rate=None):
    """Freshness for request-time products (no standing feed).

    Data is fetched at request time. We score whether the live path is healthy:
    recent successful calls (usage.db) and/or self-probe success, plus latency.
    The health window is max(promised, 6h) — a call 30m ago still means the path
    delivers fresh request-time data; we do NOT treat that like a stale feed age.
    """
    health_window = max(int(promised or 0), 6 * 3600)
    detail = {
        "mode": "live_fetch",
        "promised_seconds": promised,
        "health_window_seconds": health_window,
        "last_success_age_seconds": int(age_from_usage) if age_from_usage is not None else None,
        "probe_ok": probe_ok,
        "latency_ms": latency_ms,
        "success_rate_24h": success_rate,
    }
    s = None
    if age_from_usage is not None:
        s, d = freshness_score(age_from_usage, health_window)
        detail.update({k: v for k, v in d.items() if k != "promised_seconds"})
        detail["source"] = "usage_last_ok"
    elif probe_ok is True:
        s = 90.0
        detail["source"] = "self_probe"
    elif probe_ok is False:
        s = 25.0
        detail["source"] = "self_probe_fail"
    else:
        s = 55.0
        detail["source"] = "unmeasured"
    # Blend in success rate when we have traffic (don't inflate a failing path)
    if success_rate is not None and age_from_usage is not None:
        s = round(0.7 * s + 0.3 * (100.0 * float(success_rate)), 1)
        detail["blended_with_success_rate"] = True
    if latency_ms is not None and latency_ms > 15000 and s is not None:
        s = max(0.0, s - min(20.0, (latency_ms - 15000) / 1000.0))
        detail["latency_penalty"] = True
    return round(float(s), 1), detail



def _load_cache():
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        with _lock:
            _state.update(data)
    except Exception:
        pass


def _save_cache():
    with _lock:
        blob = dict(_state)
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(blob, f, indent=2, default=str)
    os.replace(tmp, CACHE_PATH)



def _tombstone_count():
    try:
        from .tombstones import count as _tc
        return int(_tc())
    except Exception:
        return 0

def recompute():
    """Probe free endpoints + feeds; write grades. Safe to call from any thread."""
    t0 = time.time()
    feeds_code, feeds_body = _http_get("/v1/free/feeds", timeout=40)
    feed_map = {}
    if isinstance(feeds_body, dict):
        for f in feeds_body.get("feeds") or []:
            if isinstance(f, dict) and f.get("dataset_id"):
                feed_map[f["dataset_id"]] = f

    ctx = {}
    pq_code, pq = _http_get("/v1/free/ecom/price-quality", timeout=60)
    if pq_code == 200 and isinstance(pq, dict):
        ctx["ecom_price_quality"] = {
            "rows_checked": pq.get("rows_checked"),
            "flagged_pct": pq.get("flagged_pct"),
            "placeholder_flagged": pq.get("placeholder_flagged"),
        }
    cov_code, cov = _http_get("/v1/free/company/coverage", timeout=30)
    if cov_code == 200 and isinstance(cov, dict):
        ctx["company_avg_completeness"] = cov.get("avg_completeness")
        ctx["company_projects"] = cov.get("projects")
        ctx["company_payload"] = cov
    # Wallet-label corpus stats (changelog carries labeled_addresses + as_of)
    lc_code, lc = _http_get("/v1/free/labels/changelog?limit=1", timeout=30)
    if lc_code == 200 and isinstance(lc, dict):
        ctx["labels_payload"] = lc

    ecom_code, ecom = _http_get("/v1/free/ecom", timeout=40)
    ecom_stores_code, ecom_stores = _http_get("/v1/free/ecom/stores", timeout=40)

    stats = _usage_stats()
    rates = {r: v["rate"] for r, v in stats.items()}
    probe_cache = {}
    if feeds_code:
        probe_cache["/v1/free/feeds"] = (feeds_code, feeds_body)
    if cov_code:
        probe_cache["/v1/free/company/coverage"] = (cov_code, cov)
    if ecom_code:
        probe_cache["/v1/free/ecom"] = (ecom_code, ecom)
    if ecom_stores_code:
        probe_cache["/v1/free/ecom/stores"] = (ecom_stores_code, ecom_stores)

    grades = {}
    now = time.time()

    for pid, meta in PRODUCTS.items():
        try:
            probe = meta.get("free_probe")
            code, payload = None, None
            if probe:
                if probe not in probe_cache:
                    probe_cache[probe] = _http_get(probe)
                code, payload = probe_cache[probe]
            probe_ok = code == 200 if code is not None else None

            age = None
            as_of_ts = _extract_as_of(payload) if payload else None
            # Prefer corpus_as_of for wallet-label style products
            if isinstance(payload, dict) and payload.get("corpus_as_of"):
                ct = _parse_ts(payload.get("corpus_as_of"))
                if ct:
                    as_of_ts = ct
            feed = feed_map.get(meta.get("feed_id") or "")
            if feed and not meta.get("live_fetch"):
                ft = _parse_ts(feed.get("updated_at"))
                if ft and (as_of_ts is None or ft > as_of_ts):
                    as_of_ts = ft
            if as_of_ts:
                age = max(0.0, now - as_of_ts)
            if age is None and probe_ok and meta.get("refresh_promised_sec", R30) <= RLIVE * 2 and not meta.get("live_fetch"):
                age = 5.0
                as_of_ts = now

            promised = int(meta.get("refresh_promised_sec") or R30)
            ust = _stats_for(meta["path"], stats) or {}
            lat = ust.get("avg_latency_ms")
            if meta.get("live_fetch"):
                usage_age = None
                if ust.get("last_ok_ts"):
                    usage_age = max(0.0, now - float(ust["last_ok_ts"]))
                # Request-time products: usage last-success age and/or self-probe — not feed staleness
                f_score, f_detail = live_fetch_freshness(usage_age, promised, probe_ok, lat, ust.get("rate"))
                if usage_age is not None:
                    age = usage_age
                    as_of_ts = ust["last_ok_ts"]
                elif probe_ok and as_of_ts is None:
                    as_of_ts = now
                    age = 0.0
            else:
                f_score, f_detail = freshness_score(age, promised)

            c_payload = payload
            if pid == "company" and isinstance(ctx.get("company_payload"), dict):
                c_payload = ctx["company_payload"]
            if meta.get("catalog") and not c_payload:
                c_payload = {"catalog": True, "title": meta.get("title")}
            c_score, c_detail = completeness_score(c_payload, list(meta.get("key_fields") or []))
            if meta.get("validation") == "company_fill" and ctx.get("company_avg_completeness") is not None:
                c_score = round(float(ctx["company_avg_completeness"]), 1)
                c_detail = {"avg_completeness": ctx["company_avg_completeness"], "source": "company/coverage"}

            measured = {}
            if meta.get("static_coverage"):
                measured.update({k: float(v) for k, v in meta["static_coverage"].items()})
            if meta.get("coverage_from_feed_rows") and feed and feed.get("rows") is not None:
                targets = meta.get("coverage_target") or {}
                if targets:
                    first = next(iter(targets))
                    measured[first] = float(feed["rows"])
            for metric, path in (meta.get("coverage_paths") or {}).items():
                n = _count_at(payload, path)
                if n is not None:
                    measured[metric] = n
            if pid.startswith("ecom") and isinstance(ecom_stores, dict):
                stores = ecom_stores.get("stores")
                if isinstance(stores, list):
                    measured["stores"] = float(len(stores))
            if isinstance(ecom, dict):
                for k in ("stores", "products", "variants"):
                    if k in ecom and isinstance(ecom[k], (int, float)):
                        measured.setdefault(k, float(ecom[k]))
                summary = ecom.get("summary") if isinstance(ecom.get("summary"), dict) else {}
                for src in (ecom, summary):
                    if not isinstance(src, dict):
                        continue
                    for k, alias in (("store_count", "stores"), ("product_count", "products"), ("variant_count", "variants")):
                        if k in src and isinstance(src[k], (int, float)):
                            measured.setdefault(alias, float(src[k]))
            if pid == "company" and ctx.get("company_projects"):
                measured["projects"] = float(ctx["company_projects"])
            if pid in ("wallet-label", "wallet-label-bulk") and isinstance(ctx.get("labels_payload"), dict):
                la = ctx["labels_payload"].get("labeled_addresses")
                if isinstance(la, (int, float)):
                    measured["labeled_addresses"] = float(la)
            if pid == "domain" and isinstance(ctx.get("company_payload"), dict):
                hc = ctx["company_payload"].get("homepage_crawl") or {}
                if hc.get("done"):
                    measured["sites_crawled"] = float(hc["done"])
            cov_score, cov_detail = coverage_score(measured, dict(meta.get("coverage_target") or {}))

            u_rate = _rate_for(meta["path"], rates)
            u_score, u_detail = uptime_score(u_rate, probe_ok)
            v_score, v_detail = validation_score(meta.get("validation") or "none", ctx)

            parts = {
                "freshness": f_score,
                "completeness": c_score,
                "coverage": cov_score,
                "uptime": u_score,
                "validation": v_score,
            }
            score = combine(parts)
            grade = letter_for(score)
            coverage_human = ", ".join(
                f"{k}={int(v) if float(v).is_integer() else v}" for k, v in sorted(measured.items())
            ) or "n/a"

            grades[pid] = {
                "product": pid,
                "title": meta.get("title") or pid,
                "family": meta.get("family"),
                "path": meta.get("path"),
                "grade": grade,
                "score": score,
                "as_of": _iso(as_of_ts) if as_of_ts else None,
                "freshness_seconds": int(age) if age is not None else None,
                "refresh_promised_seconds": int(meta.get("refresh_promised_sec") or R30),
                "coverage": coverage_human,
                "coverage_detail": cov_detail,
                "sources": list(meta.get("sources") or []),
                "components": parts,
                "component_detail": {
                    "freshness": f_detail,
                    "completeness": c_detail,
                    "coverage": cov_detail,
                    "uptime": u_detail,
                    "validation": v_detail,
                },
                "probe": {"path": probe, "http_status": code},
                "notes": meta.get("notes"),
                "last_checked": _iso(now),
            }
        except Exception as e:
            log.exception("grade %s", pid)
            grades[pid] = {
                "product": pid, "title": meta.get("title"), "path": meta.get("path"),
                "grade": "F", "score": 0, "error": str(e), "last_checked": _iso(now),
                "sources": list(meta.get("sources") or []),
            }

    rubric = {
        "scale": "0-100 with letter A-F",
        "letters": {"A": "90-100", "B": "80-89", "C": "70-79", "D": "60-69", "F": "<60"},
        "weights": WEIGHTS,
        "formulas": {
            "freshness": "Standing feeds: 100 if age<=promised; linear to 0 at 2*promised; 40 if unmeasured. Live-fetch products: path-health from usage.db last success age (6h+ window) blended with success rate, and/or self-probe success/latency — request-time data, not feed age.",
            "completeness": "% of declared key_fields non-null (or company avg_completeness)",
            "coverage": "mean of measured/target ratios clipped to 100",
            "uptime": "usage.db success rate last 24h (2xx/3xx/402/404=up); else probe_ok 95 / fail 20 / unknown 60",
            "validation": "ecom: 100*(1-flagged_pct/20); company: avg_completeness; else 100",
        },
        "recompute_every_minutes": INTERVAL_MIN,
        "sources_policy": "Public RPCs, redistributable public APIs, SEC EDGAR, Shopify products.json, public HTML/RSS. No Yahoo Finance, CoinGecko/GeckoTerminal, GoPlus, Blockscout, Arkham, Nansen, Whale Alert, Cielo, or Chainalysis.",
    }
    summary = {
        "as_of": _iso(now),
        "product_count": len(grades),
        "grade_counts": {let: sum(1 for g in grades.values() if g.get("grade") == let) for let in ("A", "B", "C", "D", "F")},
        "tombstones_total": _tombstone_count(),
        "recompute_ms": int((time.time() - t0) * 1000),
        "probe_base": PROBE_BASE,
    }
    with _lock:
        _state["grades"] = grades
        _state["as_of"] = summary["as_of"]
        _state["rubric"] = rubric
        _state["summary"] = summary
        _state["last_error"] = None
    _save_cache()
    log.info("quality recompute: %s products in %sms grades=%s", len(grades), summary["recompute_ms"], summary["grade_counts"])
    return dict(_state)




def get_all():
    with _lock:
        if _state.get("grades"):
            return _pack()
    _load_cache()
    with _lock:
        if _state.get("grades"):
            return _pack()
    recompute()
    with _lock:
        return _pack()


def _pack():
    summary = dict(_state.get("summary") or {})
    summary["tombstones_total"] = _tombstone_count()
    return {
        "as_of": _state.get("as_of"),
        "summary": summary,
        "rubric": _state.get("rubric"),
        "products": _state.get("grades"),
        "endpoints": {"all": "/v1/quality", "one": "/v1/quality/{product}", "human": "/quality",
                      "tombstones": "/v1/tombstones", "tombstones_check": "/v1/tombstones/check"},
    }


def get_one(product):
    data = get_all()
    g = (data.get("products") or {}).get(product)
    if not g:
        return None
    return {"as_of": data.get("as_of"), "rubric": data.get("rubric"), "product": g}


def quality_block(product):
    with _lock:
        g = (_state.get("grades") or {}).get(product)
    if not g:
        _load_cache()
        with _lock:
            g = (_state.get("grades") or {}).get(product)
    if not g:
        return None
    return {
        "grade": g.get("grade"),
        "score": g.get("score"),
        "as_of": g.get("as_of"),
        "freshness_seconds": g.get("freshness_seconds"),
        "sources": g.get("sources"),
        "details": "/v1/quality/" + product,
    }


def ensure_lookups_covered(lookups):
    return sorted(k for k in lookups if k not in PRODUCTS)


def _html_page(data):
    products = data.get("products") or {}
    rows = sorted(products.values(), key=lambda g: (-(g.get("score") or 0), g.get("product") or ""))
    as_of = data.get("as_of") or "—"
    counts = (data.get("summary") or {}).get("grade_counts") or {}
    tomb_n = (data.get("summary") or {}).get("tombstones_total")
    if tomb_n is None:
        tomb_n = _tombstone_count()

    def esc(s):
        return (str(s) if s is not None else "—").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def grade_cls(g):
        return {"A": "ga", "B": "gb", "C": "gc", "D": "gd", "F": "gf"}.get(g or "", "")

    trs = []
    for g in rows:
        src = ", ".join(g.get("sources") or []) or "—"
        fresh = g.get("freshness_seconds")
        if isinstance(fresh, int):
            fresh_s = f"{fresh}s" if fresh < 120 else (f"{fresh // 60}m" if fresh < 3600 else f"{fresh / 3600:.1f}h")
        else:
            fresh_s = "—"
        trs.append(
            "<tr><td><a href=\"/v1/quality/{p}\">{p}</a><div class='sub'>{t}</div></td>"
            "<td><span class='grade {gc}'>{gr}</span> <span class='score'>{sc}</span></td>"
            "<td>{fr}</td><td class='cov'>{cv}</td><td class='src'>{sr}</td>"
            "<td class='mono'>{lc}</td></tr>".format(
                p=esc(g.get("product")), t=esc(g.get("title")), gc=grade_cls(g.get("grade")),
                gr=esc(g.get("grade")), sc=esc(g.get("score")), fr=esc(fresh_s),
                cv=esc(g.get("coverage")), sr=esc(src), lc=esc(g.get("last_checked")),
            )
        )
    wrow = " · ".join(f"{k} {int(v*100)}%" for k, v in WEIGHTS.items())
    body_rows = "".join(trs)
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<meta name="description" content="Transparent, measured quality grades for every Scrapling x402 product."/>
<title>Data quality — Scrapling x402</title>
<link rel="icon" type="image/png" href="/logo.png">
<style>
:root {{ --bg:#0b0f19; --panel:#121a2b; --border:#243048; --text:#eef2ff; --muted:#94a3b8; --accent:#34d399; --accent2:#38bdf8; --radius:14px; --max:1100px; }}
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ font-family:Inter,ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; background:radial-gradient(1200px 600px at 10% -10%,rgba(52,211,153,0.12),transparent 55%),radial-gradient(900px 500px at 100% 0%,rgba(56,189,248,0.10),transparent 50%),var(--bg); color:var(--text); line-height:1.55; min-height:100vh; }}
a {{ color:var(--accent); text-decoration:none; }} a:hover {{ text-decoration:underline; }}
.wrap {{ max-width:var(--max); margin:0 auto; padding:28px 20px 64px; }}
header {{ display:flex; justify-content:space-between; align-items:center; gap:16px; flex-wrap:wrap; margin-bottom:28px; }}
.brand {{ display:flex; align-items:center; gap:12px; font-weight:700; font-size:17px; }}
.brand img {{ width:32px; height:32px; border-radius:8px; }}
nav {{ display:flex; gap:14px; flex-wrap:wrap; font-size:14px; }} nav a {{ color:var(--muted); }} nav a:hover {{ color:var(--text); }}
h1 {{ font-size:clamp(26px,4vw,34px); letter-spacing:-0.03em; margin-bottom:8px; }}
.lead {{ color:var(--muted); margin-bottom:18px; max-width:52rem; }}
.pills {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:22px; }}
.pill {{ font-size:12.5px; color:var(--muted); background:rgba(255,255,255,0.03); border:1px solid var(--border); padding:6px 10px; border-radius:999px; }}
section {{ margin-bottom:32px; }}
section h2 {{ font-size:13px; letter-spacing:0.08em; text-transform:uppercase; color:var(--accent2); margin-bottom:12px; font-weight:700; }}
.card {{ background:var(--panel); border:1px solid var(--border); border-radius:var(--radius); padding:16px 18px; }}
.card p, .card li {{ color:var(--muted); font-size:14px; }} .card ul {{ margin:8px 0 0 18px; }}
.mono {{ font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:12px; color:#7dd3fc; }}
table {{ width:100%; border-collapse:collapse; font-size:13.5px; }}
th, td {{ text-align:left; padding:10px 8px; border-bottom:1px solid var(--border); vertical-align:top; }}
th {{ color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:0.04em; }}
.sub {{ color:var(--muted); font-size:12px; margin-top:2px; }}
.grade {{ display:inline-block; min-width:1.6em; font-weight:800; }}
.ga {{ color:#34d399; }} .gb {{ color:#38bdf8; }} .gc {{ color:#fbbf24; }} .gd {{ color:#fb923c; }} .gf {{ color:#f87171; }}
.score {{ color:var(--muted); font-size:12px; }}
.src, .cov {{ color:var(--muted); font-size:12.5px; max-width:280px; }}
.scroll {{ overflow-x:auto; border:1px solid var(--border); border-radius:var(--radius); background:var(--panel); }}
footer {{ margin-top:40px; padding-top:18px; border-top:1px solid var(--border); color:var(--muted); font-size:13px; display:flex; flex-wrap:wrap; gap:10px 16px; }}
</style></head><body><div class="wrap">
<header><div class="brand"><img src="/logo.png" alt="" width="32" height="32"/><span>Scrapling x402</span></div>
<nav><a href="/">Home</a><a href="/quality">Quality</a><a href="/docs">Docs</a><a href="/llms.txt">llms.txt</a><a href="/v1/quality">JSON</a><a href="/openapi.json">OpenAPI</a></nav></header>
<h1>Data quality grades</h1>
<p class="lead">Measured from live probes, feed ages, coverage counts and route uptime — not hand-picked letters. Recomputed about every {INTERVAL_MIN} minutes. Machine-readable at <a href="/v1/quality">/v1/quality</a> and <span class="mono">/v1/quality/{{product}}</span>.</p>
<div class="pills">
<span class="pill">as_of {esc(as_of)}</span>
<span class="pill">A {counts.get('A',0)} · B {counts.get('B',0)} · C {counts.get('C',0)} · D {counts.get('D',0)} · F {counts.get('F',0)}</span>
<span class="pill">Weights: {esc(wrow)}</span>
<span class="pill">Retired facts (tombstones): {tomb_n}</span>
</div>
<p class="lead" style="margin-top:8px">When a label or hack fact stops being true we keep a <a href="/v1/tombstones">tombstone</a> (retired / superseded / frozen / returned) instead of silently dropping it. Check one address: <span class="mono">/v1/tombstones/check</span> ($0.001).</p>
<section><h2>Products</h2><div class="scroll"><table>
<thead><tr><th>Product</th><th>Grade</th><th>Freshness</th><th>Coverage</th><th>Sources</th><th>Last checked</th></tr></thead>
<tbody>{body_rows}</tbody></table></div></section>
<section><h2>Rubric</h2><div class="card">
<p>Score = 0.30·freshness + 0.25·completeness + 0.20·coverage + 0.15·uptime + 0.10·validation.</p>
<ul>
<li><strong>Freshness</strong> — standing feeds: age of newest data vs promised refresh (100 if on time, 0 at 2× late). Live-fetch routes (scrape, pdf, search, evm, composed tools, …): request-time — scored from recent successful-call age in gateway logs and/or a self-probe; not penalized for lacking a standing feed.</li>
<li><strong>Completeness</strong> — share of key fields non-null in the latest free/sample response.</li>
<li><strong>Coverage</strong> — measured counts vs published targets.</li>
<li><strong>Uptime</strong> — HTTP success rate from gateway usage logs (24h), else last probe result.</li>
<li><strong>Validation</strong> — e-com placeholder-price % and company field fill rate where available.</li>
</ul>
<p style="margin-top:10px">Letters: A ≥90 · B ≥80 · C ≥70 · D ≥60 · F &lt;60. Full JSON includes per-component math so anyone can recompute.</p>
</div></section>
<section><h2>Compliance</h2><div class="card"><ul>
<li><strong>Business / public data only.</strong> On-chain, market, company and public-web data — not personal dossiers.</li>
<li><strong>No personal phone numbers or personal emails</strong> as a product. Public homepage business contact emails may appear for KYB/lead research; we do not enrich consumer PII.</li>
<li><strong>How data is collected.</strong> Public blockchain RPCs, redistributable public market APIs, SEC EDGAR, public Shopify <span class="mono">/products.json</span>, public HTML/RSS, and our own derived metrics. We do not use Yahoo Finance, CoinGecko/GeckoTerminal, GoPlus, Blockscout, Arkham, Nansen, Whale Alert, Cielo or Chainalysis.</li>
<li><strong>Retention.</strong> Paid response bodies are not kept as a customer archive. Operational caches rotate on their refresh schedule. Usage logs store a salted IP hash (never raw IP), route, status and time.</li>
<li><strong>No certifications claimed.</strong> We do not claim SOC 2, ISO 27001, or similar. Contact via the store site / docs.</li>
</ul></div></section>
<footer><a href="/">Home</a><a href="/v1/quality">/v1/quality JSON</a><a href="/llms.txt">llms.txt</a><a href="/docs">Docs</a><span>Grades recomputed ~every {INTERVAL_MIN} min</span></footer>
</div></body></html>"""


def _loop():
    time.sleep(8)
    while True:
        try:
            recompute()
        except Exception as e:
            log.exception("quality recompute failed: %s", e)
            with _lock:
                _state["last_error"] = str(e)
        time.sleep(INTERVAL_MIN * 60)


def maybe_start():
    global _started
    if _started or os.environ.get("SCRAPLING_QUALITY", "1") == "0":
        return
    _started = True
    _load_cache()
    threading.Thread(target=_loop, daemon=True, name="quality-grades").start()
    log.info("quality grader started (every %s min, probe %s)", INTERVAL_MIN, PROBE_BASE)


def register(app, *, rate_limit, free_endpoints=None, lookups=None):
    maybe_start()
    if free_endpoints is not None:
        free_endpoints["/v1/quality"] = (
            "Free quality grades for every product: letter A-F + 0-100 score from measured freshness, "
            "completeness, coverage, uptime and validation. Includes published rubric weights. "
            "Human page at /quality; per-product at /v1/quality/{product}."
        )
        free_endpoints["/v1/quality/{product}"] = (
            "Free quality grade for one product id (e.g. coin, yields, ecom-product, candles). "
            "Same rubric as /v1/quality."
        )

    @app.get("/v1/quality", tags=["free"], summary="Free: measured quality grades for every product")
    async def quality_all(request: Request, refresh: int = 0):
        rate_limit(request)
        if refresh:
            await run_in_threadpool(recompute)
        return await run_in_threadpool(get_all)

    @app.get("/v1/quality/{product}", tags=["free"], summary="Free: quality grade for one product")
    async def quality_one(product: str, request: Request):
        rate_limit(request)
        g = await run_in_threadpool(get_one, product)
        if not g:
            raise HTTPException(404, detail={"error": f"unknown product '{product}'", "known": sorted(PRODUCTS.keys())})
        return g

    @app.get("/quality", response_class=HTMLResponse, include_in_schema=False)
    async def quality_page():
        data = await run_in_threadpool(get_all)
        return HTMLResponse(_html_page(data), headers={"Cache-Control": "public, max-age=60"})

    if lookups is not None:
        missing = ensure_lookups_covered(lookups)
        if missing:
            log.warning("quality PRODUCTS missing LOOKUPS keys: %s", ", ".join(missing[:30]))
    log.info("quality routes registered (%s products in rubric)", len(PRODUCTS))
