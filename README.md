# Scrapling Data (MCP Server)

Pay-per-call crypto funding, SEC insider trades, used GPU and RAM prices, plus DeFi, stock and web data for AI agents. Free 3-row previews; paid calls settle in USDC via x402. No account or API key. Dataset prices $0.01–$0.09.

This is the data store at [scraplingdata.xyz](https://scraplingdata.xyz), not the unrelated open-source Scrapling scraper.

## Connect
Remote MCP (Streamable HTTP, POST): https://scraplingdata.xyz/mcp

Official registry name: `io.github.bnth09productions-maker/scrapling-x402-store` (this file is version 0.6.4, matching the live server). Server card: https://scraplingdata.xyz/mcp/server-card · https://scraplingdata.xyz/.well-known/mcp.json

No OAuth or API key is required for free tools. Paid tools are paid per call in USDC with x402: call once, then retry with the signed payload in `params._meta["x402/payment"]`. Or send a prepaid credits key / day-or-month pass as a header (placeholders only; never put a wallet private key in this file):

- `Authorization: Bearer sk_scrapling_YOUR_KEY`
- `X-Pass-Token: sdp_YOUR_PASS_TOKEN`

### Copy-paste (clients that support remote MCP)

```json
{
  "mcpServers": {
    "scrapling-data": {
      "url": "https://scraplingdata.xyz/mcp"
    }
  }
}
```

### Copy-paste fallback (stdio-only clients)

```json
{
  "mcpServers": {
    "scrapling-data": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://scraplingdata.xyz/mcp"]
    }
  }
}
```

### Claude Desktop
Paste the `mcpServers` url block into `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows). Use the `mcp-remote` fallback if the app cannot use a remote url.

### Cursor
Paste the same `mcpServers` url block into `.cursor/mcp.json` (project) or your user MCP config.

### VS Code
`.vscode/mcp.json` uses a `servers` key:

```json
{
  "servers": {
    "scrapling-data": {
      "type": "http",
      "url": "https://scraplingdata.xyz/mcp"
    }
  }
}
```

### Claude Code CLI

```
claude mcp add --transport http scrapling-data https://scraplingdata.xyz/mcp
```

## Data
- Live crypto funding rates and open interest
- SEC insider trades and 13D/13G filings
- Used GPU and RAM sold prices (from [pcprice.watch](https://www.pcprice.watch), credited on the store)
- Company crypto treasuries and tokenized real estate
- DeFi TVL, yields, meme-coin data, token scam checks
- Hyperliquid trader leaderboards and wallet lookups
- Web scraping to Markdown

## Links
- Website: https://scraplingdata.xyz
- Registry metadata: `server.json` (version 0.6.4) includes the icon at https://scraplingdata.xyz/logo.png (400x400 PNG)
- OpenAPI: https://scraplingdata.xyz/openapi.json
- llms.txt: https://scraplingdata.xyz/llms.txt
- Official MCP Registry: io.github.bnth09productions-maker/scrapling-x402-store

## Local MCP server
The package serves the documented tool list and forwards `tools/call` to https://scraplingdata.xyz/mcp. Prices and payment stay on that server.

```
pip install -e ".[dev]"
python -m scrapling_mcp
python -m unittest tests.test_registry
```
