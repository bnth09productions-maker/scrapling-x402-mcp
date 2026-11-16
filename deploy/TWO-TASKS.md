# Two follow-on tasks

Preimage is the five fixes already installed on PC, box, and VPS. Stop if a live file hash differs. Do not restart from this package. Install the alias first. The accuracy slice does not touch `gateway.py`, so each slice can be rolled back alone.

## Hashes

| File | Preimage (installed) | Postimage |
|---|---|---|
| `scrapling/monetize/gateway.py` | `7be20569132964f2b153a5ba0a8abb501902a4dfa1649efd6a00f87c2ae05a8d` | `1d360fc0224d016b01ab54c7400d90fcf34b4d72d822038ca03c5418ce91dba9` |
| `scrapling/monetize/openapi_alias.py` | none (new) | `7554bab1855739485ce66be06b2d8b902fffde1616489ef7a1cb51b04466a72e` |
| `scrapling/monetize/quality.py` | `baa243745ad9d67a8ca41b40330928f018a192e333a0175a01cac5971762b181` | `eacdecfee7acee7fe008768b1b54f8c5a4afbc3f5adac78ae86d8d58fccf8d29` |
| `scrapling/monetize/accuracy_checks.py` | none (new) | `139ff34cfc35d864e77205dde739e2d17e992d4b9a30bca9fc6f0180943a15ec` |

## Backup

```bash
stamp=$(date -u +%Y%m%dT%H%M%SZ)
cp -p scrapling/monetize/gateway.py scrapling/monetize/gateway.py.bak-$stamp
cp -p scrapling/monetize/quality.py scrapling/monetize/quality.py.bak-$stamp
sha256sum scrapling/monetize/gateway.py.bak-$stamp scrapling/monetize/quality.py.bak-$stamp
```

Keep the backup out of the next `code.tgz` if the packer would ship it.

## 1. OpenAPI alias

From the app root (the directory that contains `scrapling/`):

```bash
sha256sum scrapling/monetize/gateway.py
# must be 7be20569132964f2b153a5ba0a8abb501902a4dfa1649efd6a00f87c2ae05a8d
test ! -e scrapling/monetize/openapi_alias.py
patch -p1 --forward --dry-run < 01-openapi-alias/gateway.py.diff
patch -p1 --forward < 01-openapi-alias/gateway.py.diff
cp 01-openapi-alias/files/openapi_alias.py scrapling/monetize/openapi_alias.py
sha256sum scrapling/monetize/gateway.py scrapling/monetize/openapi_alias.py
```

Expected: gateway `1d360fc0…`, alias module `7554bab1…`.

Check, no payment:

```bash
curl -sD- -o /tmp/o1.json https://scraplingdata.xyz/openapi.json
curl -sD- -o /tmp/o2.json https://scraplingdata.xyz/.well-known/openapi.json
python - << 'PY'
import json
a=json.load(open("/tmp/o1.json"))
b=json.load(open("/tmp/o2.json"))
assert a==b, "schemas differ"
assert str(a.get("openapi","")).startswith("3.")
print("openapi", a.get("openapi"), "paths", len(a.get("paths") or {}))
PY
```

Both responses are HTTP 200, `Content-Type: application/json`, and neither is a redirect. `/openapi.json` is still the canonical URL. The well-known path is also listed on the x402 manifest `endpoints` and on the AI plugin as `well_known_url`. It is not added as a path inside the OpenAPI document.

Rollback:

```bash
patch -R -p1 --dry-run < 01-openapi-alias/gateway.py.diff
patch -R -p1 < 01-openapi-alias/gateway.py.diff
rm -f scrapling/monetize/openapi_alias.py
sha256sum scrapling/monetize/gateway.py
# 7be20569132964f2b153a5ba0a8abb501902a4dfa1649efd6a00f87c2ae05a8d
```

Patch the PC host tree and the container before the next `code.tgz` cycle, then the operator restarts that one host.

## 2. Sample checks

Does not modify `gateway.py` or the alias module.

```bash
sha256sum scrapling/monetize/quality.py
# must be baa243745ad9d67a8ca41b40330928f018a192e333a0175a01cac5971762b181
test ! -e scrapling/monetize/accuracy_checks.py
patch -p1 --forward --dry-run < 02-accuracy-checks/quality.py.diff
patch -p1 --forward < 02-accuracy-checks/quality.py.diff
cp 02-accuracy-checks/files/accuracy_checks.py scrapling/monetize/accuracy_checks.py
sha256sum scrapling/monetize/quality.py scrapling/monetize/accuracy_checks.py
```

Expected: quality `eacdecfe…`, accuracy module `139ff34c…`.

The existing quality thread calls the sampler at most once every four hours. It does not call paid store routes. Letter grades stay on the old rubric. `/quality` and `/v1/quality` gain a separate `sample_checks` object.

Rollback:

```bash
patch -R -p1 < 02-accuracy-checks/quality.py.diff
rm -f scrapling/monetize/accuracy_checks.py
sha256sum scrapling/monetize/quality.py
# baa243745ad9d67a8ca41b40330928f018a192e333a0175a01cac5971762b181
```

## Tests already run

`python3 -m unittest discover -s tests/monetize` — 24 tests, OK, including alias equality and sample match/mismatch/skip/error/stale/null rate.

`python3 tests/monetize/test_mpp_scope.py` — exit 0.

No production calls and no payments.

## Limitations

- Company rows only check that the homepage we already store contains the project name. That is not a full profile audit. DefiLlama is not treated as a second independent source.
- Wallet rows look for an entity string on an Etherscan page. That is not ownership. The zero address is skipped on purpose. A blocked explorer is unknown, not a match.
- Binance announcements are not compared to net outflow. A transfer we already collected is the same chain data and is not an independent match. With no transfer in memory the independent rate stays null.
- Stock-perp marks are the same Hyperliquid info API. They are self-consistency within 2 percent only while the local snapshot is under 15 minutes old. The independent match rate stays null.
- A comparable sample of zero, or a sample where nothing was actually checked, has `match_rate: null` and status `ungraded`.
- Banned providers are refused before a socket. Homepage fetches still require public HTTPS DNS and reject private, loopback, and metadata addresses.
