# Counterparty check price: $0.004 to $0.002

Route verified in code: `GET /v1/check/counterparty`, lookup key `counterparty-check`.
There is no `/v1/wallet/counterparty` handler. Do not change wallet-label (`$0.002` already) or intel risk-check (`$0.0045`).

The charged amount is `LOOKUPS["counterparty-check"]["price"]` in `agent_api.py`. `paid()` and `probe()` pass that float to the gateway gate. x402 `amount` is `usdc_atomic_units`: `round(0.002 * 10**6) = 2000` on every configured 6-decimal USDC network. MPP uses `MppDoor._amount(0.002) == "0.002"`, which pympp converts with the same 6 decimals. llms.txt, `/docs`, the x402 manifest, and the OpenAPI per-route price are generated from that lookup price. These hardcoded copies must also change or they drift:

- `mcp_server.py` tool title and the `PAID $0.004 USDC. Before paying` sentence
- `upd25.py` route summary
- `gateway.py` OpenAPI `x-guidance` phrase for this path only
- `skill_scrapling_data.md` table row for this path only

`$0.0045`, `$0.04`, and `$0.005` must stay. The apply script aborts if an anchor is missing, duplicated, or mixed with the new text.

## Apply (operator, one host at a time)

App root is the directory that contains `scrapling/`. On the PC that is `O:\scrapling-secured\Scrapling-main` and the same relative paths inside `scrapling-x402-gateway`. Patch the host tree and the container in the same window so `code.tgz` does not restore the old price. Then box and VPS. This package does not restart anything. The process must be reloaded by the operator or the old price stays in memory.

```bash
python3 apply_counterparty_price.py /path/to/app
# prints already or patched <file> <oldsha> -> <newsha>
# abort leaves the tree unchanged
python3 apply_counterparty_price.py --rollback /path/to/app
```

Backup first: copy each of the five files beside itself with a timestamp. Rollback reverses the same anchors. If a printed hash is neither the preimage nor the postimage, restore the backup instead of rolling back.

Reference only, not a live preimage: the 2026-11-06 box standby snapshot hashes. If the anchor count is not exactly one, stop. Do not overwrite a newer gateway with any full file from this branch.

After reload, unpaid `GET /v1/check/counterparty?chain=ethereum&address=0x0000000000000000000000000000000000000000` should be HTTP 402 with `priceUsd` 0.002 and x402 `amount` `2000`. A `PAYMENT-SIGNATURE: not-a-valid-payment` header must stay 402, not 200. Other SKUs stay on their old prices. MPP still advertises charge and session on the primary, with `_mppx_scope.resource` equal to `/v1/check/counterparty`.

The health checker in `ops/healthcheck` still expects `$0.004` until you edit that one config row after this cutover. Do not reinstall the checker as part of this price change.
