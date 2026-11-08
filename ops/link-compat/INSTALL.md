# Link and MCP compatibility

Do not install this yet. PC `O:` writes are blocked. Do not work around that by copying through another drive, share, or container. Deployment stays held until a supported PC write path exists. This package does not restart anything and does not change prices. Counterparty guidance stays `$0.004`.

The zip does not contain `gateway.py` or `mcp_server.py`. It adds two new modules and patches anchors in the files that are already on the host.

## What it changes

- `GET` or `POST` `/v1/market-pulse` and `/v1/defi-intel` return 307 to `/v1/bundle/market-pulse` and `/v1/bundle/defi-intel`. The query string is kept. The method is kept (307). The paid route still answers 402. Nothing in this package returns the paid body.
- A single trailing `.` redirects only for the paths listed in `files/link_compat.py`. `stock-perps.json` is not stripped. `..`, a second slash, or a percent-encoded path is not redirected.
- `OPTIONS /mcp` returns 204 with `Allow: POST, OPTIONS`. `GET`, `HEAD`, and `DELETE` stay 405 with that same `Allow`. There is no SSE stream and no session, and those methods do not return 200.
- `POST /mcp` with an `MCP-Protocol-Version` header outside `2025-11-25`, `2025-06-18`, and `2025-03-26` returns 400 and a JSON-RPC error. A missing header is still accepted. `initialize` already negotiates the body `protocolVersion`.

OAuth discovery is unchanged. `mcp_server.py` already answers 404 for the OAuth well-known paths and does not publish an issuer or token endpoint. That 404 is the current auth model: paid tools use x402, not OAuth.

## Apply, when a supported write exists

App root is the directory that contains `scrapling/`. Preflight reads every anchor before any backup or replace. A missing or repeated anchor writes nothing.

```bash
python3 apply_link_compat.py /path/to/app
```

Backups go to `/path/to/app/.link-compat-backups/<stamp>/`. Move that directory outside the tree that becomes `code.tgz`.

Rollback of a finished apply is the backup copies of `gateway.py` and `mcp_server.py`, then delete `scrapling/monetize/link_compat.py` and `scrapling/monetize/mcp_method.py` if this apply added them. A failed replace restores the original bytes itself.

`HASHES.txt` gives the preimage this was proved against. If the live file does not contain each anchor once, the script aborts. Do not copy a whole gateway from this repo over the host.
