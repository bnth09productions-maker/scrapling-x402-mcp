# Deploy notes — MPP scope hotfix, then four buyer fixes

These notes are for operators. This change set does not deploy, restart, pay, or edit env, keys, or prices.

## What this is, and what it is not

The preimage is the sanitized **box standby** tree exported 2026-11-06 about 12:04 PM America/Chicago from `/workspace/standby/app/scrapling/monetize`. It is **not** a dump of the PC primary container. Live PC files can differ. If a live file's SHA-256 is not the preimage below, stop. Do not apply the patch and do not invent a merge.

Primary: PC Docker service `scrapling-x402-gateway`. The host tree `O:\scrapling-secured\Scrapling-main` is packed to `code.tgz` about every 20 minutes and feeds the container. Patch **both** the host tree and the container in the same window, or the next pack will put the old file back.

Standbys: box `/workspace/standby/app` and the VPS. Patch one host at a time, after the primary is checked. MPP session mode stays as the host already has it (primary sessions on, standbys charge-only). Do not change `SCRAPLING_MPP_SESSION`, operator keys, vault DBs, or prices.

Do **not** copy `scrapling/__init__.py` or `scrapling/monetize/__init__.py` from the git branch. Those files are an import shim for the tests. The live package init is not in this patch.

## Hashes

Preimage (must match before a patch). SHA-256 of the file bytes:

| File | Preimage SHA-256 |
|---|---|
| `scrapling/monetize/mpp_bridge.py` | `522dde54513a48a5e4b7895ae657f6fc72c188792605bc743c3f5f7b2b226a8b` |
| `scrapling/monetize/gateway.py` | `92d1ff4e2a6bd7fdbb63d5aeaf84f0b6591ddcb4b3e4be21bcb55201bfdf5e24` |
| `scrapling/monetize/labels.py` | `e00cbe87b054dd6e04bc9e59f4dd7119dea886670881520c751da62e46948970` |
| `scrapling/monetize/binance_flows.py` | `ad98cb18e23490c3501158b8262ea76da46bba44d2e4fa3328eed4e99a76636a` |
| `scrapling/monetize/upd43.py` | `1db4f943485175754e067596cab4190fbf966986bc1ae4c40ba48cda1eb71b64` |
| `scrapling/monetize/quality.py` | `5a00686fecfceca4bce302a2774e09032aac0c46831ad32508ca1415e3a5ccbc` |

`scrapling/monetize/batch_scrape_openapi.py` is **new**. There is no preimage. If a file is already at that path, stop and compare it. Its shipped SHA-256 is `b051975aca7545991931659e0f70455b2e1049bfdfb6efaef9220420930bd4d6`.

Postimage after each slice:

| Slice | File | Postimage SHA-256 |
|---|---|---|
| URGENT | `mpp_bridge.py` | `f0758ab9ed847de32ff5cc6cf4ccb4f96f2d7daa8ceee6511d8732fe3958c660` |
| URGENT only | `gateway.py` | `397d6596a392432eeee30d0288b56fc1731647c6d31bf8e63329ef57494ec56a` |
| Follow-up 1 | `labels.py` | `a60a4684fc56c1d31c5538a5e36ebb8999a5b5fd1405254d4ea2557d9ebefdcf` |
| Follow-up 2 | `binance_flows.py` | `a4bbdad5204152bfb56813b29406f0ceb2c68154a66a0c9e56ea5bb37dd0d849` |
| Follow-up 2 | `upd43.py` | `6b6dd8d270366ba94b89503ebbb6274824d1147e3e9e446c8ad37075459fee98` |
| Follow-up 3 only | `gateway.py` (OpenAPI, no MPP patch) | `b856ff2ac2f4e92b14de9384110c4bb12fa6eb02082cce1bb739df8ee1eff10a` |
| Follow-up 3 | `batch_scrape_openapi.py` | `b051975aca7545991931659e0f70455b2e1049bfdfb6efaef9220420930bd4d6` |
| Follow-up 4 | `quality.py` | `baa243745ad9d67a8ca41b40330928f018a192e333a0175a01cac5971762b181` |
| URGENT then follow-up 3 | `gateway.py` | `7be20569132964f2b153a5ba0a8abb501902a4dfa1649efd6a00f87c2ae05a8d` |

`gateway.py` is the only file in two slices. The two diffs do not overlap. Each applies to the preimage by itself. Applying urgent first and then the OpenAPI diff produces `7be20569…`, which is the file in git.

## Find the file

Relative path from the app root is `scrapling/monetize/<name>.py`. On the box standby that root is `/workspace/standby/app`. On the PC host tree the root is `O:\scrapling-secured\Scrapling-main`. The container path is whatever directory that host tree is unpacked into. If you are not sure, hash the candidate:

```bash
sha256sum scrapling/monetize/mpp_bridge.py
```

A mismatch with the preimage means stop.

## Backup

For each file, before writing:

```bash
stamp=$(date -u +%Y%m%dT%H%M%SZ)
cp -p scrapling/monetize/mpp_bridge.py scrapling/monetize/mpp_bridge.py.bak-$stamp
sha256sum scrapling/monetize/mpp_bridge.py.bak-$stamp
```

Keep the backup outside the next `code.tgz` pack if the pack would ship `*.bak` files. The standby export omitted `*.bak*`, but confirm the packer on the PC.

## Install — urgent first

From the directory that contains `scrapling/` (the app root):

```bash
# hashes must match the preimage table
sha256sum scrapling/monetize/mpp_bridge.py scrapling/monetize/gateway.py
patch -p1 --forward --dry-run < mpp_bridge.py.diff
patch -p1 --forward --dry-run < gateway.py.diff
patch -p1 --forward < mpp_bridge.py.diff
patch -p1 --forward < gateway.py.diff
sha256sum scrapling/monetize/mpp_bridge.py scrapling/monetize/gateway.py
```

Urgent diffs are in `/opt/cursor/artifacts/URGENT-mpp-scope/`. Full urgent files are in that folder's `files/` (`gateway.py` there is urgent-only).

Expected after this slice only: `mpp_bridge.py` `f0758ab9…`, `gateway.py` `397d6596…`.

Do this on the PC host tree and in the PC container before the next `code.tgz` cycle. Then the operator restarts that one gateway. This package does not restart it.

### Check, no payment

```bash
curl -sI -H 'X-Internal-Monitor: 1' \
  'https://scraplingdata.xyz/v1/company?q=aave'
```

Decode each `WWW-Authenticate` `request=` value (base64url JSON). `_mppx_scope.resource` must be `/v1/company`, not `/`. Query `q=aave` is a separate scope field. Repeat for:

- `/v1/wallet-label?address=0x0000000000000000000000000000000000000000` → resource `/v1/wallet-label`
- `/v1/lookup/binance-flows` → resource `/v1/lookup/binance-flows`, no query field

On the primary, both `intent=charge` and `intent=session` should carry that resource. On a charge-only standby, only `charge` is expected. x402 `resource.url` and the atomic price (`3000`, `2000`, `20000`) stay as they were. Do not pay the challenge.

Credentials minted before this patch are signed for resource `/`. They will not be accepted on the real path. Clients take a fresh challenge. Do not map a `/` credential onto every route.

## Install — four follow-ups, each reversible

Apply any subset. Each diff was applied to the standby preimage on its own and restored with `patch -R`.

1. Wallet label — `labels.py.diff` in `FOLLOWUP-01-wallet-label/`. Postimage `a60a4684…`.
2. Binance partial — `binance_flows.py.diff` and `upd43.py.diff` in `FOLLOWUP-02-binance-partial/`. Postimage `a4bbdad5…` and `6b6dd8d2…`.
3. Batch-scrape OpenAPI — copy new `files/batch_scrape_openapi.py` (hash `b051975a…`) and apply `gateway.py.diff`. If urgent is already on `gateway.py`, the postimage is `7be20569…`. If urgent is not on yet, the postimage is `b856ff2a…`.
4. Stock-perps quality — `quality.py.diff` in `FOLLOWUP-04-stock-perps-quality/`. Postimage `baa24374…`.

Same backup and hash check as the urgent slice. Restart is the operator's step, one host at a time.

## Rollback

From the app root, with the forward diff that was applied:

```bash
patch -R -p1 --dry-run < mpp_bridge.py.diff
patch -R -p1 < mpp_bridge.py.diff
sha256sum scrapling/monetize/mpp_bridge.py
```

A successful rollback of a file that had no later edit returns the preimage hash. If the hash is neither the preimage nor the postimage, stop and restore the `.bak` file you saved. For the new OpenAPI module, rollback is deleting `scrapling/monetize/batch_scrape_openapi.py` only after `gateway.py` no longer imports it (reverse the gateway diff first).

`gateway.py` rollback of one slice: `patch -R` only that slice's diff. Reversing the urgent diff after the OpenAPI diff is applied works because the hunks do not overlap. Confirm with `--dry-run`.

## Tests already run here

Offline, no network payment, no Tempo broadcast:

- `PYTHONPATH=/workspace python3 -m unittest discover -s tests/monetize` — 15 tests, OK.
- `PYTHONPATH=/workspace python3 tests/monetize/test_mpp_scope.py` — OK. Charge and session scopes for `/v1/company?q=aave`, wallet-label, binance-flows, POST `/v1/batch-scrape`, and a percent-encoded path. Cross-route, cross-query, and cross-method credentials are not selected. A same-route fake proof is rejected locally (`Hash credential source is invalid` / invalid session payload) and is not broadcast.

The MPP script calls `os._exit` at the end so a non-daemon client thread created by pympp session setup does not hang the runner. That thread is from constructing a session-enabled door in the test, not a new production loop.

## Caveats

- PC primary bytes were not read. Hashes are the box standby preimage.
- In-flight MPP credentials bound to `/` stop matching. That is the binding fix. No refunds or replays are performed here.
- `last_confirmed` is null unless the source row published a confirmation date. Index rebuild time stays in `as_of` / `corpus_as_of`.
- Binance `partial` / `chains_partial` mirror `window_state_24h`. `action_hint=pause` is still only present on provider lag.
- GET `/v1/batch-scrape` still always returns 402 and does not scrape. OpenAPI now says so. POST is the paid call.
- Stock-perps freshness uses the free teaser `as_of` against a 900 second promise (the published 15 minute delay). A stale teaser is not graded A. A rubric with no path is `ungraded`, not F and not A.
- No Yahoo, CoinGecko, GeckoTerminal, GoPlus, Blockscout, Arkham, Nansen, Whale Alert, Cielo, or Chainalysis calls were added.
