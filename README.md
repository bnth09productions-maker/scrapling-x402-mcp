# Scrapling x402 Data Store (MCP Server)

Pay-per-call crypto, DeFi, stock and e-commerce data for AI agents. Free previews; paid calls settle in USDC via x402. No account or API key.

## Connect
MCP endpoint (Streamable HTTP): https://scraplingdata.xyz/mcp

```
claude mcp add --transport http scrapling-data https://scraplingdata.xyz/mcp
```

## Data
- DeFi TVL for 880+ protocols on 56 chains, yields, perps funding
- Meme coin data, token scam checks, whale flows
- SEC stock fundamentals, filings and insider trades
- Hyperliquid top-trader leaderboard and wallet lookups
- Web scraping to Markdown

## Links
- Website: https://scraplingdata.xyz
- Registry metadata: `server.json` (version 0.6.2) includes the icon at https://scraplingdata.xyz/logo.png (400x400 PNG)

## Local MCP server
The package serves the documented tool list and forwards `tools/call` to https://scraplingdata.xyz/mcp. Prices and payment stay on that server.

```
pip install -e ".[dev]"
python -m scrapling_mcp
python -m unittest tests.test_registry
```
- OpenAPI: https://scraplingdata.xyz/openapi.json
- llms.txt: https://scraplingdata.xyz/llms.txt
- x402scan: https://www.x402scan.com/server/1b76cf53-526e-48df-a7e7-2c777e14354a
- Official MCP Registry: io.github.bnth09productions-maker/scrapling-x402-store
