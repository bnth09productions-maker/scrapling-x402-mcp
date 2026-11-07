# Counterparty check price: $0.004 to $0.002

Route: `GET /v1/check/counterparty`, lookup key `counterparty-check`.
There is no `/v1/wallet/counterparty` handler. Do not change wallet-label or intel risk-check (`$0.0045`).

The charged amount is `LOOKUPS["counterparty-check"]["price"]` in `agent_api.py`. x402 atomic units for `$0.002` are `2000` (`round(0.002 * 10**6)` on 6-decimal USDC). The MPP amount string is `0.002` (`f"{usd:.6f}".rstrip("0").rstrip(".")`). llms.txt, `/docs`, the x402 manifest, and the OpenAPI per-route amount are generated from that lookup after a process reload. These hardcoded copies are separate anchors:

- `mcp_server.py`: `Counterparty Check ($0.004)` and `PAID $0.004 USDC. Before paying or trading:`
- `upd25.py`: `before you pay or trade ($0.004)`
- `gateway.py`: `/v1/check/counterparty?chain=&address= $0.004 `
- `skill_scrapling_data.md`: the counterparty table row ending `$0.004`

Six anchors, five files. `$0.0045`, `$0.04`, and `$0.005` are not anchors and must stay.

This package does not contain a gateway file to copy. `patches/gateway.py.diff` is a one-line reference for a gateway whose sha256 is `1d360fc0224d016b01ab54c7400d90fcf34b4d72d822038ca03c5418ce91dba9`. If that hash does not match, do not apply the diff. Use the script. Do not apply the diffs one file at a time: a partial tree is a mixed state, and the script will then refuse to finish it.

The previous zip `19407c0f10e9d75438b2e04a8a6964088f80e5f0675a4ff552f34389c4b3871c` wrote `agent_api.py` and `mcp_server.py` before a later missing anchor aborted. Do not run that script.

## What the script does

`python3 apply_counterparty_price.py --preflight ROOT` reads all five files and checks all six anchors. It writes nothing.

A preflight failure writes nothing. That includes a missing file, an anchor that is not present exactly once, and a mixed tree (some files already say `$0.002`, others still say `$0.004`). Exit code `1`. The five files keep the bytes they had.

When every anchor still has the old text, the script then:

1. Writes a backup of each original file under `--backup-dir` (a new directory) or, if omitted, `ROOT/.counterparty-price-backups/<stamp>/`.
2. Writes each replacement to a temp file in the same directory, as bytes, with the original permission bits. CRLF stays CRLF. The script does not open the files in text mode.
3. `os.replace`s each temp onto its target.

If backup or temp creation fails, no target is replaced. A backup directory created for that attempt is removed.

If a replace fails, files already replaced are restored from the original bytes. The backup directory is left in place. After that restore, the five files match the bytes from the start of the run. If the restore itself fails, the command says so; use the backup directory and do not continue to the next host.

A second run, when every anchor is already the new text, prints `already` and does not write. `--rollback` is the same procedure with the six anchors reversed. It also refuses a mixed tree.

Exit `0` means preflight succeeded and the tree is either unchanged (`already`) or fully patched. Exit `2` is a usage error.

Put `--backup-dir` outside the tree that becomes `code.tgz`. The default directory is inside the app root; move or exclude it before the feeder runs.

## Apply (operator, one host at a time)

No host was changed by the build of this package. Reload is still the operator's step, after the files are patched. Patch the PC host tree and the container in the same window so `code.tgz` does not restore `$0.004`. Then box, then VPS.

```bash
python3 apply_counterparty_price.py --preflight /path/to/app
python3 apply_counterparty_price.py --backup-dir /path/outside/the/tree /path/to/app
python3 apply_counterparty_price.py --rollback --backup-dir /path/outside/rollback /path/to/app
```

`HASHES.txt` lists the 2026-11-06 box standby hashes as a reference only. Live files may differ. The script does not require those hashes and does not copy those files.

After the operator reloads, an unpaid `GET /v1/check/counterparty` should be HTTP 402 with `priceUsd` 0.002 and x402 amount `2000`. This package does not claim that the live route already shows 0.002. A `PAYMENT-SIGNATURE: not-a-valid-payment` header must stay 402, not 200. Other SKUs stay on their old prices.

The health checker config stays at counterparty `usd` `0.004` until that live 402 shows `0.002`. Then change only that one row. Do not reinstall the checker as part of this price change.
