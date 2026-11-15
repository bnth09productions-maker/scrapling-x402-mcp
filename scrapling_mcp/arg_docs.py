"""Argument and return-value sentences for every MCP tool.

These strings document the live schemas. They do not add enums, defaults, or
prices that the captured catalog does not already state.
"""

API_KEY = (
    "Optional payment credential. Format: a prepaid key sk_scrapling_... or a "
    "catalog pass token sdp_... from pass_day or pass_month. Example: sdp_... . "
    "When omitted on a paid tool, the call is unpaid and the server answers with "
    "x402 PaymentRequired; retry the same call with the signed payment in "
    "params._meta['x402/payment']."
)

TOOL_RETURNS = {
    "list_datasets": "Returns JSON for every dataset for sale: id, title, category, row count, field names, created_at, a few sample rows, and the USDC price.",
    "preview_dataset": "Returns JSON with up to 10 real rows (never more than half the dataset), every field name, the total row count, created_at, and the exact USDC price buy_dataset charges.",
    "scrape_url": "Returns JSON with clean Markdown, JSON-LD, HTML tables, and links from the fetched page.",
    "buy_dataset": "Returns every row of the dataset in the requested format (json, jsonl, csv, or markdown).",
    "free_feeds": "Returns a JSON catalog of every live feed with row counts, freshness, full-feed price, and the matching paid lookup tools.",
    "free_crypto_top10": "Returns JSON for the top 10 coins: rank, USD price, and 24h change from Chainlink on-chain feeds.",
    "free_defi_top_yields": "Returns JSON for the top 5 yields: APY, TVL, risk level, chain, and pool, ranked by risk-adjusted APY.",
    "free_regime": "Returns JSON with the delayed risk-on or risk-off label and a 0-100 score.",
    "free_ecom": "Returns JSON index stats (stores, products, variants by category), last crawl time, sample delayed price drops, and the deepest current discounts.",
    "free_staking_yields": "Returns JSON for the top 3 liquid-staking and yield-bearing stablecoin APYs.",
    "free_funding": "Returns JSON funding rates for BTC, ETH, and SOL, delayed up to 15 minutes.",
    "free_stock_perps": "Returns JSON for the top tradeXYZ equity perps: mark, 24h change, funding APR, open interest, and volume, delayed up to 15 minutes.",
    "stock_perps": "Returns JSON for the matching HIP-3 equity perps: mark, oracle, 24h change, funding, open interest, 24h volume, large taker prints, and whale net by symbol.",
    "free_hl_twaps": "Returns JSON for 3 recent Hyperliquid TWAP slice fills, delayed up to 15 minutes.",
    "free_hl_spot": "Returns JSON for the top 3 Hyperliquid spot markets by 24h volume, delayed up to 15 minutes.",
    "free_hl_perps": "Returns JSON for the top 3 canonical Hyperliquid perp markets by 24h volume, delayed up to 15 minutes.",
    "free_hl_hip3": "Returns JSON for the top 3 HIP-3 builder-DEX perp markets by 24h volume, delayed up to 15 minutes.",
    "free_hl_launches": "Returns JSON for the 3 newest Hyperliquid HIP-1 spot token deploys, delayed up to 15 minutes.",
    "hl_twaps": "Returns JSON TWAP slice fills from Hyperliquid's public info API (not a complete book of every open TWAP).",
    "hl_twap": "Returns JSON with that wallet's TWAP history and recent slice fills.",
    "hl_spot": "Returns JSON for Hyperliquid spot markets: mark or mid, 24h change, volume, and supply.",
    "hl_perps": "Returns JSON for canonical Hyperliquid perps: mark, oracle, funding, open interest, and volume.",
    "hl_hip3": "Returns JSON for HIP-3 builder-DEX perp markets: mark, funding, open interest, and volume.",
    "hl_launches": "Returns JSON for new HIP-1 spot deploys: deploy time, deployer, supply, and mark.",
    "staking_yields": "Returns JSON APY, TVL, and exchange rate for the eight documented LST and yield-bearing tokens, or the one token requested.",
    "pendle_fixed": "Returns JSON for each matching Pendle market: implied fixed APY, expiry, days left, and PT price.",
    "perp_funding": "Returns JSON Hyperliquid funding (1h, 8h, APR), direction, premium, mark and oracle price, open interest, and 24h volume.",
    "free_tokenized_stocks": "Returns JSON for the top 10 tokenized stocks by on-chain supply value, including the Chainlink oracle price and oracle_round.",
    "tokenized_stocks": "Returns JSON per token: contract, underlying ticker, chain, issuer, on-chain supply, supply value, DEX price, Chainlink oracle price, and premium or discount percent.",
    "free_prices": "Returns JSON USD prices for up to 3 tokens, each with the Chainlink feed, chain, and block.",
    "prices": "Returns JSON USD prices for up to 50 tokens, each with feed address, chain, oracle update time, and block, plus a not_found list.",
    "free_crypto_treasuries": "Returns JSON for the top 10 SEC-reporting companies by crypto treasury value.",
    "free_etf_holdings": "Returns JSON all-fund BTC, ETH, and LTC coin totals plus the top funds.",
    "free_country_btc_holdings": "Returns JSON country and government Bitcoin holdings with as-of dates, sources, and method badges.",
    "address_poisoning": "Returns JSON of recent Base USDC transfers for the address, newest first, with tx hash, block, time, and lookalike flags.",
    "free_binance_flows": "Returns JSON 1h and 24h Binance net USD outflow, all-chain and per chain, with as-of time, block, and per-chain check state.",
    "binance_flows": "Returns JSON 1h and 24h inflow, outflow, and net by chain and by asset, with as-of time, block, check state, and spread fields.",
    "binance_flows_report": "Returns the binance_flows snapshot plus the largest 24h withdrawals, destination labels, an hourly net-flow series, and the tracked wallet list.",
    "pass_day": "Returns JSON with a pass_token (sdp_...) shown once; later calls send that token as api_key.",
    "pass_month": "Returns JSON with a pass_token (sdp_...) shown once; later calls send that token as api_key.",
    "alerts_subscribe_week": "Returns JSON subscription_id, manage_token, and signing_secret, each shown once.",
    "alerts_subscribe_month": "Returns JSON subscription_id, manage_token, and signing_secret for a 30-day subscription, each shown once.",
    "free_alerts_info": "Returns JSON describing topics, default USD thresholds, the sample signed payload, and how to verify, test, and unsubscribe.",
    "crypto_treasuries": "Returns JSON company rows (ticker, CIK, asset, units, filed fair value, cost basis, filing link, live USD value) and per-asset totals.",
    "etf_holdings": "Returns JSON per fund (ticker, product type, coins, shares, NAV, bought, sold, net change, AUM) plus all-fund totals and missing-fund notes.",
    "etf_flows": "Returns JSON day-over-day coins bought and sold for each matching US spot crypto ETF.",
    "free_tokenized_real_estate": "Returns JSON for the top 10 RealT property tokens: token, contract, city, state, and on-chain price.",
    "tokenized_real_estate": "Returns JSON property tokens with address, city, supply, on-chain price, market cap, holders, trade volume, and RMM collateral.",
    "hack_tracker": "Returns JSON attacker and linked wallets with holdings, frozen status, swaps, bridge or mixer use, exchange-inflow alerts, and 1h/24h moved amounts.",
    "free_traders": "Returns JSON for the top 3 Hyperliquid traders this week: address, PnL, and ROI.",
    "top_traders": "Returns a JSON ranking of Hyperliquid wallets with address, PnL, ROI, volume, and account value.",
    "trader_wallet": "Returns JSON ranks, PnL by window, open positions, and recent win rate for that wallet.",
    "free_stream_status": "Returns JSON collector status: subscriber count, watchlist size, and buffer. It does not stream trades.",
    "trader_profile": "Returns JSON ranks, equity, and open perps across Hyperliquid and, when the address matches, GMX v2 and dYdX v4.",
    "trader_positions": "Returns JSON open Hyperliquid and GMX v2 perps plus recent entry and exit events (side, size, entry, leverage, PnL).",
    "trader_history": "Returns JSON fills classified as open, increase, reduce, or close, with price, size, side, and realized PnL.",
    "stream_window": "Returns JSON with an X-Stream-Token that covers the majors, positions, and meme SSE routes for 15 minutes.",
    "free_bot_sheet_sample": "Returns JSON for the top 3 Hyperliquid traders this week plus the Fear and Greed value.",
    "bot_sheet": "Returns a JSON analytics sheet for that Hyperliquid wallet or vault, or Thirdfy agent.",
    "bots_top": "Returns a JSON ranking of Hyperliquid traders for the window, with Thirdfy agents joined only when requested.",
    "free_odds_sample": "Returns JSON Fear and Greed plus 3 high-volume Polymarket questions (prices only).",
    "odds_pulse": "Returns JSON Polymarket matches for the query plus the Fear and Greed reading.",
    "odds_movers": "Returns JSON Polymarket markets ordered by 24h volume, with a Fear and Greed overlay.",
    "odds_hypothesis": "Returns a JSON research card with the market-implied probability and Fear and Greed. It is not a trade instruction.",
    "free_postmortem_sample": "Returns JSON for 2 curated trading-agent post-mortem rows (schema preview only).",
    "postmortems_search": "Returns JSON curated post-mortems with agent, sentiment, timing, model, risk notes, and citations.",
    "bot_history": "Returns JSON local Hyperliquid or Thirdfy snapshots for the id plus the live day, week, and month windows.",
    "odds_history": "Returns JSON stored Polymarket or Manifold snapshots for the market plus daily Fear and Greed.",
    "free_trading_history_status": "Returns JSON collector status and how many days of trading history are stored.",
    "free_stocks": "Returns JSON latest annual revenue and net income plus the newest SEC filing for AAPL, MSFT, and NVDA.",
    "stock_fundamentals": "Returns JSON latest annual and quarterly fundamentals and the balance sheet from SEC XBRL.",
    "stock_filings": "Returns JSON recent SEC filings for the ticker, with form type and document links.",
    "stock_insiders": "Returns JSON parsed Form 4 rows (insider, role, code, shares, price, value, 10b5-1 flag) and buy or sell totals.",
    "stock_price": "Returns JSON Chainlink on-chain price for the ticker with 24h and 7d change.",
    "free_meme_trending": "Returns JSON for the top 3 trending meme coins on each of Base, BNB Chain, and Solana, delayed 30 minutes.",
    "meme_trending": "Returns JSON ranked meme coins: price, change, buys and sells, liquidity, pool age, and a go, caution, or avoid verdict.",
    "pumpfun_feed": "Returns JSON pump.fun rows: mint, symbol, creator, age, bonding-curve progress, and SOL in the curve.",
    "meme_new_pools": "Returns JSON new pools: pool, DEX, token, quote, creation time, and volume and liquidity since launch.",
    "meme_history": "Returns JSON about-15-minute price and volume snapshots (kept 30 days) with a high, low, and change summary.",
    "meme_trader": "Returns JSON tokens bought and sold in the observed prints, with estimated cost basis and unrealized PnL for those prints only.",
    "free_stream_meme_status": "Returns JSON meme-stream status: subscribers, buffer, and USD size floor. It does not stream trades.",
    "ecom_product": "Returns JSON matching variants: current price, compare-at price, discount percent, in-stock flag, URL, image, and 30-day daily history.",
    "ecom_changes": "Returns JSON crawl-to-crawl changes (price drops and rises, restocks, sell-outs, new products, removals) for the filters.",
    "ecom_snapshot": "Returns JSON counts, in-stock percent, price stats, brands, deepest discounts, newest products, and 7-day change counts for the slice.",
    "ecom_catalog": "Returns JSON for every current variant of that store: price, compare-at price, stock flag, SKU, tags, URL, and image.",
    "lookup_coin": "Returns JSON USD price, 1h, 24h, 7d, and 30d change, and 24h high and low for one major coin.",
    "lookup_protocol": "Returns JSON protocol TVL in USD, per-chain TVL, category, and chains.",
    "lookup_yields": "Returns JSON for the top 10 matching pools: APY, TVL, and a 0-100 risk score, ranked by risk-adjusted APY.",
    "lookup_pool": "Returns JSON for that pool: supply and borrow APY, utilization, TVL, impermanent-loss risk, and risk score.",
    "lookup_polymarket": "Returns JSON for up to 10 matching markets: YES probability, 1h, 24h, and 7d odds change, volume, and liquidity.",
    "lookup_token_risk": "Returns JSON verdict go, caution, or avoid, honeypot and tax flags, and a 0-100 risk score for the Base token.",
    "lookup_tx": "Returns JSON transaction and receipt: from, to, value, status, gas used, and ERC-20 Transfer logs.",
    "lookup_token_info": "Returns JSON name, symbol, decimals, totalSupply, ERC-20, ERC-721, or ERC-1155 class, and proxy implementation when present.",
    "lookup_evm_gas": "Returns JSON chain id, block height, gas price, base fee, and head age for the requested chain or all 7.",
    "lookup_allowance": "Returns JSON for allowance(owner, spender) on that ERC-20 contract.",
    "lookup_nft": "Returns JSON owner and resolved metadata for that token id.",
    "free_datasets": "Returns a JSON catalog of the paid dataset listings with price, row count, and last update time.",
    "get_dataset": "Returns rows from the chosen listing in json or csv, filtered and paged by the arguments.",
    "market_pulse": "Returns one JSON bundle: top coins, volume leaders, top protocols by TVL, DEX volume, and DeFi heatmap highlights.",
    "price_moves": "Returns JSON coins and, when included, DeFi protocols whose move exceeds the threshold, biggest first. An empty list is a valid answer.",
    "defi_intel": "Returns one JSON bundle: top protocols by TVL, 1d TVL movers, top chains, top stablecoins, and top risk-ranked yields.",
    "ad_cost_benchmarks": "Returns JSON CPM, CPC, and CTR figures, each with the source report, URL, and as-of date.",
    "ping": "Returns JSON ok, server time, and catalog links. This is the cheapest paid call and charges $0.001 USDC.",
    "whale_moves": "Returns JSON large Hyperliquid taker orders plus net whale buy and sell per coin.",
    "whale_netflow": "Returns JSON whale-to-exchange transfers with a signal label and per-token inflow, outflow, and net USD, plus Hyperliquid whale perp flow.",
    "whale_token": "Returns JSON whale orders and net buy or sell for that coin, plus mark, 24h change, funding, and open interest.",
    "token_verdict": "Returns JSON verdict go, caution, or avoid, a 0-100 risk score, and the liquidity, holder, tax, and contract facts behind it.",
    "wallet_profile": "Returns JSON label, 0-100 activity score, balances, activity dates, exchange label, and whale-transfer history.",
    "regime_now": "Returns JSON regime label, 0-100 score, confidence, sub-scores, drivers, and the nine component readings.",
    "regime_full": "Returns the regime_now payload plus raw component metrics, 30-day score history, the 7-day trend, and the methodology.",
    "dd_report": "Returns a JSON due-diligence report: verdict, flags, liquidity, holders, whale flow, lending, recommendation, confidence, and proof links.",
    "dd_quick": "Returns JSON allow, caution, or avoid, a trust score, the top 3 red flags, DEX liquidity, and holder concentration.",
    "trust_agent": "Returns JSON allow, caution, or block, a 0-100 trust score, and the reasons.",
    "counterparty_check": "Returns JSON safe, caution, or block, a 0-100 safety score, and the reasons.",
    "intel_risk_check": "Returns JSON verdict MALICIOUS, HIGH_RISK, WATCH, LOW_RISK, or UNKNOWN, plus a 0-100 score and the label evidence.",
    "intel_risk_batch": "Returns JSON with the same verdict fields as intel_risk_check for each address, up to 50.",
    "stolen_funds_bundle": "Returns the labeled address pack with as_of, freeze flags, and a SHA-256 checksum.",
    "free_intel_risk_check": "Returns the same JSON verdict as intel_risk_check while the free daily tries remain.",
    "token_holders": "Returns JSON top-10 share, largest holder share, exchange-labelled share, and a low, medium, or high verdict.",
    "scrape_text": "Returns JSON title, clean Markdown text, and character count for the page.",
}

ARGS = {
    "preview_dataset": {
        "dataset_id": "Dataset id from list_datasets. Format: the dataset_id string, lowercase with underscores. Example: live_crypto_top500_markets. Required.",
    },
    "scrape_url": {
        "url": "Absolute page URL to fetch. Format: https:// host and path. Example: https://example.com. Required.",
        "tier": "Fetch tier. Format: one of the allowed values. Example: static_scrape. Default: auto, which picks static_scrape for a normal page. cached_intel is the recent cached copy, static_scrape is a live fetch, and stealth_browser is a headless browser.",
        "css_selector": "Optional CSS selector that limits extraction to one part of the page. Format: a CSS selector. Example: article. Omit to extract the whole page.",
    },
    "buy_dataset": {
        "dataset_id": "Dataset id from list_datasets. Format: the dataset_id string. Example: live_crypto_top500_markets. Required. The USDC price is the one list_datasets shows for that id.",
        "format": "File format of the download. Format: one of the allowed values. Example: csv. Default: json.",
    },
    "stock_perps": {
        "symbol": "Optional comma-separated equity tickers. Format: uppercase tickers separated by commas, no spaces required. Example: TSLA,NVDA. Omit for the full equity book.",
        "dex": "Optional HIP-3 builder DEX slug. Format: a short lowercase slug. The tool text already lists xyz, flx, cash, km, mkts, io, para, and vntl. Example: xyz. Omit for every builder DEX.",
        "limit": "Maximum number of markets in the response. Format: positive integer. Example: 20. Omit for the server's current cap.",
        "min_usd": "Minimum notional in USD for the large-trade list. Format: number of US dollars. Example: 1000. Default: 1000, as the existing tool text states.",
    },
    "hl_twaps": {
        "coin": "Optional coin or HIP-3 market filter. Format: a Hyperliquid coin name. Example: BTC. Another example already in the tool text: xyz:GOLD. Omit for every coin in the sample.",
        "limit": "Maximum fills to return. Format: integer. Example: 50. Default: 50. Maximum: 200, as the existing tool text states.",
    },
    "hl_twap": {
        "wallet": "Hyperliquid wallet. Format: 0x followed by 40 hex characters (42 characters total). Example: 0x85ecf584f25db6f146718b86d493e33c5af72052. Required.",
    },
    "hl_spot": {
        "coin": "Optional spot pair or token filter. Format: a token symbol or PAIR/USDC. Example: HYPE. Another example already in the tool text: PURR/USDC. Omit for the whole book.",
        "limit": "Maximum markets, ordered by 24h volume. Format: positive integer. Example: 20. Default: all markets, as the existing tool text states.",
    },
    "hl_perps": {
        "coin": "Optional comma-separated coin filter. Format: Hyperliquid coin symbols. Example: BTC,ETH. Omit for the whole canonical book.",
        "limit": "Maximum markets, ordered by 24h volume. Format: positive integer. Example: 20. Default: all markets, as the existing tool text states.",
    },
    "hl_hip3": {
        "dex": "Optional builder DEX slug. Format: lowercase slug. Example: xyz. The tool text also names flx, vntl, hyna, km, abcd, cash, para, mkts, and io. Omit for every builder DEX.",
        "coin": "Optional market filter. Format: a ticker or dex:ticker. Example: TSLA. Another example already in the tool text: xyz:TSLA. Omit for every market.",
        "limit": "Maximum markets, ordered by 24h volume. Format: positive integer. Example: 20. Default: all markets, as the existing tool text states.",
    },
    "hl_launches": {
        "days": "Only deploys in the last N days. Format: positive integer count of days. Example: 30. Omit for the server's current window.",
        "limit": "Maximum launches to return. Format: integer. Example: 50. Default: 50. Maximum: 200, as the existing tool text states.",
    },
    "staking_yields": {
        "token": "Optional symbol filter. Format: one of stETH, rETH, cbETH, weETH, sfrxETH, sUSDe, sUSDS, sDAI. Example: rETH. Omit for all eight.",
    },
    "pendle_fixed": {
        "chain": "Optional chain name for Pendle markets. Format: a chain name. The tool text names Ethereum, Arbitrum, and Base. Example: Base. Omit for all three.",
        "q": "Optional symbol search inside the market name. Format: a short token symbol. Example: ETH. Omit for every active market.",
        "min_days": "Optional minimum days until expiry. Format: a non-negative number of days. Example: 30. Omit to include every expiry.",
    },
    "perp_funding": {
        "coin": "Optional Hyperliquid coin filter. Format: a coin symbol. Example: BTC. Omit for every coin.",
        "limit": "Maximum coins to return. Format: positive integer. Example: 20. Omit for the server's current cap.",
    },
    "tokenized_stocks": {
        "ticker": "Optional underlying ticker filter. Format: uppercase ticker, or several separated by commas. Example: TSLA. Omit for every token.",
        "chain": "Optional chain filter. Format: a chain slug. The tool text names ethereum, bsc, and solana. Example: solana. Omit for all of those chains.",
        "issuer": "Optional issuer filter. Format: a lowercase issuer slug. The tool text names ondo and xstocks. Example: ondo. Omit for both issuers.",
        "limit": "Maximum tokens to return. Format: positive integer. Example: 20. Omit for the server's current cap.",
    },
    "prices": {
        "tokens": "Comma-separated token symbols, at most 50. Format: lowercase symbols separated by commas. Example: btc,eth,sol,link. Required.",
        "vs": "Quote currency. Format: a lowercase currency code. The existing schema description is usd. Example: usd.",
    },
    "address_poisoning": {
        "address": "Base address to inspect. Format: 0x followed by 40 hex characters. Example: 0xc9d9a973d9817d482a47dfc118cf78436e5cffcb. Required.",
        "hours": "Look-back window in hours. Format: a number from 0.25 to 24. Example: 6. Default: 6, as the existing tool text states.",
    },
    "binance_flows_report": {
        "limit": "How many of the biggest withdrawals to list. Format: integer from 1 to 100. Example: 25. Default: 25, as the existing tool text states.",
    },
    "pass_day": {},
    "pass_month": {},
    "alerts_subscribe_week": {
        "webhook_url": "HTTPS URL that receives the signed alert POSTs. Format: https:// host and path. Example: https://example.com/hook. Required.",
        "topics": "Comma-separated topic list. Format: hack, binance_flows, whale, in any combination. Example: hack,whale. Default: all three, as the existing tool text states.",
        "min_usd": "Minimum USD size of one alert. Format: a number of US dollars. Example: 1000000. Defaults already stated on the tool: hack 100000, whale 1000000, binance_flows 50000000.",
        "chains": "Optional comma-separated chains. Format: the slugs the tool text names: ethereum, bsc, base, tron, bitcoin. Example: ethereum,base. Omit for every chain.",
    },
    "alerts_subscribe_month": {
        "webhook_url": "HTTPS URL that receives the signed alert POSTs. Format: https:// host and path. Example: https://example.com/hook. Required.",
        "topics": "Comma-separated topic list. Format: hack, binance_flows, whale, in any combination. Example: hack,whale. Default: all three, as the existing tool text states.",
        "min_usd": "Minimum USD size of one alert. Format: a number of US dollars. Example: 1000000. Defaults already stated on the tool: hack 100000, whale 1000000, binance_flows 50000000.",
        "chains": "Optional comma-separated chains. Format: the slugs the tool text names: ethereum, bsc, base, tron, bitcoin. Example: ethereum,base. Omit for every chain.",
    },
    "crypto_treasuries": {
        "ticker": "Optional comma-separated stock tickers. Format: uppercase tickers. Example: MSTR,TSLA. Omit for every company.",
        "asset": "Optional crypto asset filter. Format: an uppercase ticker. The tool text names BTC, ETH, and SOL. Example: BTC. Omit for every asset.",
        "limit": "Maximum company rows. Format: positive integer. Example: 50. Omit for the server's current cap.",
    },
    "etf_holdings": {
        "asset": "Optional underlying asset. Format: BTC, ETH, or LTC, as the tool text states. Example: BTC. Omit for all three.",
        "ticker": "Optional comma-separated fund tickers. Format: uppercase tickers. Example: IBIT,ETHA,LTCC. Omit for every fund.",
    },
    "etf_flows": {
        "asset": "Optional underlying asset. Format: BTC, ETH, or LTC, as the tool text states. Example: BTC. Omit for all three.",
        "ticker": "Optional comma-separated fund tickers. Format: uppercase tickers. Example: IBIT,ETHA,LTCC. Omit for every fund.",
    },
    "tokenized_real_estate": {
        "platform": "Optional platform name. Format: the platform string the tool text names. Example: RealT. Omit for every platform in the feed.",
        "chain": "Optional chain name. Format: the chain string the tool text names. Example: Gnosis. Omit for every chain in the feed.",
        "city": "Optional city filter. Format: city name. Example: Detroit. Omit for every city.",
        "state": "Optional state or region code. Format: a short code. Example: MI. Omit for every state.",
        "country": "Optional country code. Format: a short code. Example: US. Omit for every country.",
        "limit": "Maximum property rows. Format: positive integer. Example: 20. Omit for the server's current cap.",
    },
    "hack_tracker": {
        "incident": "Incident id. Format: a lowercase slug. The existing description is binance-2026. Example: binance-2026. Omit only if you want the server's current default incident.",
        "limit": "Maximum wallet rows. Format: positive integer. Example: 50. Omit for the server's current cap.",
    },
    "top_traders": {
        "venue": "Venue slug. Format: a lowercase venue id. Example: hyperliquid. The tool text describes the Hyperliquid leaderboard. Omit for the server's current venue.",
        "window": "Ranking window. Format: one of the values already in the tool text: day, week, month, allTime. Example: week. Omit for the server's current window.",
        "sort": "Sort key. Format: one of the values already in the tool text: pnl, roi, volume, worst. Example: pnl. Omit for the server's current sort.",
        "limit": "Maximum wallets. Format: positive integer. Example: 10. The tool text caps the list at 200.",
        "min_account": "Optional minimum account value in USD. Format: a number. Example: 10000. Omit for no extra minimum.",
    },
    "trader_wallet": {
        "address": "Hyperliquid wallet. Format: 0x followed by 40 hex characters. Example: 0x85ecf584f25db6f146718b86d493e33c5af72052. Required.",
    },
    "trader_profile": {
        "address": "Wallet to profile. Format: 0x plus 40 hex characters, or a dYdX v4 address that starts with dydx1. Example: 0x85ecf584f25db6f146718b86d493e33c5af72052. Required.",
    },
    "trader_positions": {
        "address": "Wallet whose open perps to read. Format: 0x followed by 40 hex characters. Example: 0x85ecf584f25db6f146718b86d493e33c5af72052. Required.",
    },
    "trader_history": {
        "address": "Wallet whose fills to classify. Format: 0x followed by 40 hex characters. Example: 0x85ecf584f25db6f146718b86d493e33c5af72052. Required.",
        "limit": "Maximum classified fills. Format: positive integer. Example: 50. Omit for the server's current cap.",
    },
    "bot_sheet": {
        "id": "Hyperliquid wallet or vault, or a Thirdfy agent slug. Format: 0x plus 40 hex characters, or a slug. Example: 0x85ecf584f25db6f146718b86d493e33c5af72052. Required.",
    },
    "bots_top": {
        "window": "Ranking window. Format: a window name such as the week value used by top_traders. Example: week. Omit for the server's current window.",
        "limit": "Maximum rows. Format: positive integer. Example: 10. Omit for the server's current cap.",
        "include_thirdfy": "Whether to join Thirdfy agents into the ranking. Format: boolean true or false. Example: true. Omit for the server's current choice.",
    },
    "odds_pulse": {
        "q": "Search text matched against Polymarket questions. Format: plain words. Example: bitcoin. Required.",
    },
    "odds_movers": {
        "limit": "Maximum markets. Format: positive integer. Example: 20. Omit for the server's current cap.",
    },
    "odds_hypothesis": {
        "q": "Event or question text for the research card. Format: plain words, spaces allowed. Example: fed rate. Required. This does not place a trade.",
    },
    "postmortems_search": {
        "q": "Optional text search. Format: plain words. Example: alpha arena. Omit to list the newest curated rows.",
        "model": "Optional model-name filter. Format: a model id string. Example: gpt-4. Omit for every model.",
        "limit": "Maximum rows. Format: positive integer. Example: 10. Omit for the server's current cap.",
    },
    "bot_history": {
        "id": "Hyperliquid wallet or vault, or a Thirdfy slug, same id as bot_sheet. Format: 0x plus 40 hex characters, or a slug. Example: 0x85ecf584f25db6f146718b86d493e33c5af72052. Required.",
        "days": "How many days of stored snapshots to include. Format: positive integer. Example: 7. Default: 7.",
    },
    "odds_history": {
        "market": "Market key to look up in the stored odds history. Format: a short name or question fragment. Example: bitcoin. Required.",
        "days": "How many days of stored snapshots to include. Format: positive integer. Example: 7. Default: 7.",
    },
    "stock_fundamentals": {
        "ticker": "US stock ticker or SEC CIK. Format: uppercase ticker, with a hyphen for share classes, or a numeric CIK. Example: AAPL. Another example already in the tool text: BRK-B. Required.",
    },
    "stock_filings": {
        "ticker": "US stock ticker or SEC CIK. Format: uppercase ticker or numeric CIK. Example: MSFT. Required.",
        "form": "Optional SEC form filter. Format: one form or a comma-separated list. Example: 8-K. The tool text also names 10-K and 10-Q. Omit for every recent form.",
        "limit": "Maximum filings. Format: positive integer. Example: 10. Omit for the server's current cap.",
    },
    "stock_insiders": {
        "ticker": "US stock ticker. Format: uppercase ticker. Example: NVDA. Required.",
        "limit": "Maximum Form 4 rows. Format: positive integer. Example: 20. Omit for the server's current cap.",
        "open_market": "When true, keep open-market buys and sells and drop other Form 4 codes. Format: boolean true or false. Example: true. Omit for every Form 4 code.",
    },
    "stock_price": {
        "ticker": "US stock or ETF ticker that has a Chainlink on-chain feed. Format: uppercase ticker. Example: TSLA. The tool text also names NVDA, SPY, and QQQ. Required.",
    },
    "meme_trending": {
        "chain": "Chain to rank. Format: one of the allowed values. Example: base.",
        "window": "Volume window. Format: one of the allowed values. Example: 1h.",
        "limit": "Maximum coins. Format: positive integer. Example: 20. Omit for the server's current cap.",
        "min_liquidity": "Minimum pool liquidity in USD. Format: a number. Example: 10000. Omit for no extra liquidity floor.",
    },
    "pumpfun_feed": {
        "kind": "Which pump.fun slice to return. Format: one of the allowed values. Example: launches.",
        "limit": "Maximum tokens. Format: positive integer. Example: 20. Omit for the server's current cap.",
        "min_progress": "Minimum bonding-curve progress. Format: a number, used with near-graduation. Example: 80. Omit for every progress level.",
    },
    "meme_new_pools": {
        "chain": "Chain whose new DEX pools to list. Format: one of the allowed values. Example: bsc.",
        "hours": "How many hours back to look. Format: a positive number. Example: 3. Omit for the server's current window.",
        "limit": "Maximum pools. Format: positive integer. Example: 50. Omit for the server's current cap.",
    },
    "meme_history": {
        "chain": "Chain of the token. Format: one of the allowed values. Example: bsc. Send it with token. The schema does not mark it required.",
        "token": "Token to chart. Format: 0x plus 40 hex characters on Base or BNB Chain, or a Solana mint. Example: 0x97d62f3d00d3134c0f8f3e73775c580aa3707777. Required.",
        "hours": "How many hours of snapshots. Format: a positive number, within the 30 days the tool keeps. Example: 24. Omit for the server's current span.",
    },
    "meme_trader": {
        "address": "Wallet seen in the meme prints. Format: 0x plus 40 hex characters, or a Solana public key. Example: 0x0000000000000000000000000000000000000000. Required.",
        "limit": "Maximum tokens in the profile. Format: positive integer. Example: 20. Omit for the server's current cap.",
    },
    "ecom_product": {
        "q": "Product name, keyword, or SKU. Format: plain text. Example: wool runner. Required.",
        "domain": "Optional Shopify store domain. Format: a hostname, no scheme. Example: allbirds.com. Omit to search every indexed store.",
        "limit": "Maximum variants. Format: integer from 1 to 50, as the existing tool text states. Example: 10. Omit for the server's current page size.",
    },
    "ecom_changes": {
        "type": "Comma-separated change types. Format: the values the tool text names: price_drop, price_rise, restock, sellout, new_product, removed. Example: price_drop,restock. Omit for every type.",
        "domain": "Optional store domain. Format: a hostname. Example: allbirds.com. Omit for every store.",
        "category": "Optional store category. Format: one of apparel, beauty, electronics, supplements, home, outdoor, jewelry, pets, toys, the list on ecom_snapshot. Example: beauty. Omit for every category.",
        "brand": "Optional brand name. Format: the brand string used by the store. Example: Allbirds. Omit for every brand.",
        "days": "How many days of crawl diffs. Format: integer from 1 to 30, as the tool text states. Example: 7. Omit for the server's current span.",
        "limit": "Maximum change rows. Format: positive integer. Example: 50. Omit for the server's current cap.",
    },
    "ecom_snapshot": {
        "domain": "Optional store domain. Format: a hostname. Example: allbirds.com. Provide a domain, a brand, or a category.",
        "brand": "Optional brand name. Format: the brand string. Example: Allbirds. Omit when filtering by domain or category instead.",
        "category": "Optional category. Format: one of the values already in the tool text: apparel, beauty, electronics, supplements, home, outdoor, jewelry, pets, toys. Example: beauty.",
    },
    "ecom_catalog": {
        "domain": "Shopify store domain. Format: a hostname, no scheme. Example: allbirds.com. Required. The free store list is /v1/free/ecom/stores.",
    },
    "lookup_coin": {
        "q": "Coin to price. Format: a symbol, coin id, or name. Example: btc. Another example already in the tool text: ethereum. Required.",
    },
    "lookup_protocol": {
        "q": "DeFi protocol slug or name. Format: a lowercase slug or a name. Example: aave. Another example already in the tool text: aave-v3. Required.",
    },
    "lookup_yields": {
        "chain": "Optional chain name. Format: a chain name. Example: Base. The tool text also names Ethereum and Arbitrum. Omit to search every chain.",
        "token": "Optional token symbol. Format: an uppercase symbol. Example: USDC. The tool text also names ETH. Omit to search every token.",
    },
    "lookup_pool": {
        "pool_id": "Pool id. Format: protocol:chain:token_address. Example: aave-v3:base:0x833589fcd6edb6e08f4c7c32d4f71b54bda02913. Required.",
    },
    "lookup_polymarket": {
        "q": "Keyword in the market question. Format: plain words. Example: bitcoin. The tool text also uses election. Required.",
    },
    "lookup_token_risk": {
        "address": "Base token contract. Format: 0x followed by 40 hex characters. Example: 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913. Required.",
    },
    "lookup_tx": {
        "hash": "Transaction hash. Format: 0x followed by 64 hex characters. Example: 0x5c504ed432cb51138bcf09aa5e8a410dd4a1e204ef84bfed1be16dfba1b22060. Required.",
        "chain": "Chain to read, or all 7. Format: the slugs already in the tool text: ethereum, base, arbitrum, polygon, optimism, bsc, avalanche, or all. Example: ethereum. Omit for the server's current chain.",
    },
    "lookup_token_info": {
        "address": "Token or contract address. Format: 0x followed by 40 hex characters. Example: 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48. Required.",
        "chain": "Single chain. Format: a chain slug such as ethereum, base, arbitrum, polygon, optimism, bsc, or avalanche. Example: ethereum. Default: ethereum, as the existing tool text states.",
    },
    "lookup_evm_gas": {
        "chain": "One chain or every tracked chain. Format: all, or a chain name. The tool text names Base, Ethereum, Arbitrum, BSC, Polygon, Optimism, and Avalanche. Example: all. Omit for the server's current choice.",
    },
    "lookup_allowance": {
        "contract": "ERC-20 contract. Format: 0x followed by 40 hex characters. Example: 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48. Required.",
        "owner": "Wallet that granted the allowance. Format: 0x followed by 40 hex characters. Example: 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045. Required.",
        "spender": "Address allowed to spend. Format: 0x followed by 40 hex characters. Example: 0x000000000022D473030F116dDEE9F6B43aC78BA3. Required.",
        "chain": "Single chain. Format: a chain slug such as ethereum or base. Example: ethereum. Default: ethereum, as the existing tool text states.",
    },
    "lookup_nft": {
        "contract": "ERC-721 or ERC-1155 contract. Format: 0x followed by 40 hex characters. Example: 0xBC4CA0EdA7647A8aB7C2061c2E118A18a936f13D. Required.",
        "token_id": "Token id inside that contract. Format: a decimal integer as a string. Example: 0. Required.",
        "chain": "Single chain. Format: a chain slug such as ethereum or base. Example: ethereum. Default: ethereum, as the existing tool text states.",
    },
    "get_dataset": {
        "dataset": "Which listing to read. Format: one of the allowed values. Example: coingecko-markets. Required.",
        "q": "Optional keyword matched against the rows. Format: plain text. Example: bitcoin. Omit for every row.",
        "limit": "Page size. Format: integer from 1 to 1000. Example: 100. Omit for the server's current page size.",
        "offset": "How many rows to skip. Format: integer, minimum 0. Example: 0. Omit to start at the first row.",
        "format": "Response body format. Format: one of the allowed values. Example: json.",
    },
    "price_moves": {
        "threshold": "Minimum absolute percent move. Format: a number of percent points. Example: 5. Default: 5, as the existing tool text states.",
        "window": "Which move window to include. Format: one of the allowed values. Example: 24h.",
        "include_defi": "Also list DeFi protocols whose TVL moved more than the threshold in 1d. Format: boolean true or false. Example: true. Omit for the server's current choice.",
        "direction": "Which direction to keep. Format: one of the allowed values. Example: both.",
    },
    "ad_cost_benchmarks": {
        "platform": "Optional ad platform. Format: a platform slug. The tool text names meta, google_search, google_display, tiktok, and linkedin. Example: meta. Omit for every platform.",
        "industry": "Optional industry name. Format: plain words. Example: real estate. Omit for every industry.",
        "metric": "Optional single metric. Format: one of the allowed values. Example: cpm. Omit for CPM, CPC, and CTR.",
    },
    "whale_moves": {
        "min_usd": "Minimum taker-order notional in USD. Format: a number. Example: 100000. Default: 100000, which is the $100k floor the tool text states.",
        "window": "Look-back window. Format: one of the allowed values. Example: 1h.",
        "coin": "Optional Hyperliquid coin. Format: a coin symbol. Example: SOL. Omit for the top 40 markets.",
        "side": "Optional taker side. Format: one of the allowed values. Example: buy. Omit for both sides.",
        "limit": "Maximum orders. Format: positive integer. Example: 50. Omit for the server's current cap.",
    },
    "whale_netflow": {
        "chain": "Chain to scan. Format: one of the allowed values. Example: all.",
        "token": "Optional token symbol. Format: a ticker. Example: USDC. Omit for every token.",
        "window": "Look-back window. Format: one of the allowed values. Example: 24h.",
        "limit": "Maximum transfers. Format: positive integer. Example: 50. Omit for the server's current cap.",
    },
    "whale_token": {
        "coin": "Hyperliquid coin. Format: a coin symbol. Example: SOL. The tool text also names BTC and HYPE. Required.",
        "min_usd": "Minimum order notional in USD. Format: a number. Example: 100000. Omit to use the server's current floor.",
        "window": "Look-back window. Format: one of the allowed values. Example: 1h.",
    },
    "token_verdict": {
        "chain": "Chain of the token. Format: a lowercase slug. The tool text names base, ethereum, bsc, arbitrum, polygon, optimism, avalanche, and solana. Example: base. Omit only if the server can infer the chain.",
        "address": "Token contract or Solana mint. Format: 0x plus 40 hex characters, or a Solana mint. Example: 0x4ed4e862860bed51a9570b96d89af5e1b0efefed. Required.",
    },
    "wallet_profile": {
        "chain": "Chain of the wallet. Format: one of the allowed values. Example: ethereum.",
        "address": "Wallet address on that chain. Format: 0x plus 40 hex characters, or a Solana public key when chain is solana. Example: 0x28C6c06298d514Db089934071355E5743bf21d60. Required.",
    },
    "dd_report": {
        "chain": "Chain of the token or wallet. Format: one of the allowed values. Example: base.",
        "address": "Token contract, Solana mint, or wallet. Format: 0x plus 40 hex characters, or a Solana mint. Example: 0x4ed4E862860beD51a9570b96d89aF5E1B0Efefed. Required.",
    },
    "dd_quick": {
        "chain": "Chain of the token or wallet. Format: one of the allowed values. Example: base.",
        "address": "Token contract, Solana mint, or wallet. Format: 0x plus 40 hex characters, or a Solana mint. Example: 0x4ed4E862860beD51a9570b96d89aF5E1B0Efefed. Required.",
    },
    "trust_agent": {
        "chain": "Chain of the agent wallet or token. Format: one of the allowed values. Example: base.",
        "address": "Agent wallet, token contract, or Solana mint. Format: 0x plus 40 hex characters, or a Solana mint. Example: 0x72C80C01fD89Bac74f47cf604b61ACd119c9BCdb. Required.",
    },
    "counterparty_check": {
        "chain": "Chain of the wallet or token. Format: one of the allowed values. Example: ethereum.",
        "address": "Wallet, token contract, or Solana mint. Format: 0x plus 40 hex characters, or a Solana mint. Example: 0x28C6c06298d514Db089934071355E5743bf21d60. Required.",
    },
    "intel_risk_check": {
        "address": "Wallet or contract to score. Format: 0x followed by 40 hex characters. Example: 0x5227f603c1ee34ce3c06efe6477003bdfccb2f8c. Required.",
    },
    "intel_risk_batch": {
        "addresses": "Addresses to score, at most 50. Format: 0x addresses separated by commas. Example: 0x5227f603c1ee34ce3c06efe6477003bdfccb2f8c,0x59a919e8d0582ce62c8de9431b3ebc6fa121731e. Required.",
    },
    "free_intel_risk_check": {
        "address": "Wallet or contract to score on the free daily teaser. Format: 0x followed by 40 hex characters. Example: 0x5227f603c1ee34ce3c06efe6477003bdfccb2f8c. Required.",
    },
    "token_holders": {
        "chain": "Chain of the mint. Format: one of the allowed values. Example: solana.",
        "address": "Solana token mint. Format: a base58 Solana mint address. Example: DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263. Required.",
    },
    "scrape_text": {
        "url": "Absolute page URL. Format: https:// host and path. Example: https://example.com. Required.",
    },
}
