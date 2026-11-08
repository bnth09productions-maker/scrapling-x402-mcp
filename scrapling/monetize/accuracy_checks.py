"""Bounded free-source sample checks for four products.

This is not a correctness guarantee and it does not change rubric grades.
Comparisons against the same upstream we already use are reported as
self-consistency, not as independent accuracy. Unknown, blocked, skipped,
and failed fetches are never counted as matches. An empty comparable sample
has a null match rate.

The quality loop calls maybe_run(). Work runs at most once per four hours.
"""
from __future__ import annotations

import ipaddress
import json
import os
import socket
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

INTERVAL_SEC = 4 * 3600
STALE_AFTER_SEC = 8 * 3600
MAX_BYTES = 262144
TIMEOUT_SEC = 8
MAX_REDIRECTS = 2
PRICE_TOLERANCE = 0.02  # relative, same asset, same public info read
PERPS_FRESH_SEC = 900

BANNED_MARKERS = (
    "yahoo", "coingecko", "geckoterminal", "goplus", "blockscout", "arkham",
    "nansen", "whale-alert", "whalealert", "cielo", "chainalysis",
)
ALLOWED_API_HOSTS = {
    "api.hyperliquid.xyz",
    "etherscan.io",
    "www.etherscan.io",
    "mempool.space",
    "blockstream.info",
    "ethereum.publicnode.com",
}

COMPANY_SAMPLE = (
    {"id": "aave", "q": "aave"},
    {"id": "uniswap", "q": "uniswap"},
    {"id": "lido", "q": "lido"},
)
WALLET_SAMPLE = (
    {"id": "binance-hot", "address": "0x28c6c06298d514db089934071355e5743bf21d60"},
    {"id": "zero", "address": "0x" + "0" * 40,
     "skip": "system address; explorer balances and transfers are not an ownership check"},
)
PERPS_SYMBOLS = ("TSLA", "NVDA", "AAPL")

class _StopRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
        return None


_OPENER = urllib.request.build_opener(_StopRedirect)

def _store_path() -> str:
    directory = os.environ.get("SCRAPLING_ACCURACY_DIR") or (
        "/collected_data/quality" if os.path.isdir("/collected_data") else "/tmp/scrapling_quality"
    )
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, "accuracy_samples.json")


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _esc(value: Any) -> str:
    text = "—" if value is None else str(value)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def host_blocked(host: str) -> Optional[str]:
    name = (host or "").lower().strip(".").split(":")[0]
    if not name:
        return "missing host"
    if any(marker in name for marker in BANNED_MARKERS):
        return "banned provider"
    return None


def _bad_ip(raw: str) -> bool:
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return True
    return bool(
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified or str(ip) == "169.254.169.254"
    )


def validate_url(url: str, *, resolve: Optional[Callable[[str], List[str]]] = None, allow_homepage: bool = False) -> Optional[str]:
    """Return a rejection reason, or None when the URL is safe to fetch."""
    try:
        parts = urlparse(url)
    except Exception:
        return "unparseable url"
    if parts.scheme != "https" or parts.username or parts.password:
        return "only https URLs without userinfo"
    host = (parts.hostname or "").lower().rstrip(".")
    blocked = host_blocked(host)
    if blocked:
        return blocked
    if parts.port not in (None, 443):
        return "non-443 port"
    if host in ("localhost",) or host.endswith(".local") or host.endswith(".internal"):
        return "local hostname"
    try:
        ipaddress.ip_address(host)
        return "literal IP host"
    except ValueError:
        pass
    if not allow_homepage and host not in ALLOWED_API_HOSTS:
        return "host is not on the sample allowlist"
    lookup = resolve or _resolve
    try:
        ips = lookup(host)
    except Exception as exc:
        return f"dns failed: {type(exc).__name__}"
    if not ips:
        return "dns returned no addresses"
    if any(_bad_ip(ip) for ip in ips):
        return "private, loopback, or metadata address"
    return None


def _resolve(host: str) -> List[str]:
    found = set()
    for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM):
        found.add(item[4][0])
    return sorted(found)


def fetch_public(url: str, *, opener: Optional[Callable[..., Dict[str, Any]]] = None,
                 resolve: Optional[Callable[[str], List[str]]] = None, allow_homepage: bool = False,
                 redirects: int = 0, method: str = "GET", json_body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Fetch one allowlisted HTTPS URL, or refuse it before any socket to a banned host."""
    reason = validate_url(url, resolve=resolve, allow_homepage=allow_homepage)
    if reason:
        return {"ok": False, "status": 0, "body": "", "final_url": url, "error": reason}
    if method != "GET":
        if method != "POST" or urlparse(url).hostname != "api.hyperliquid.xyz":
            return {"ok": False, "status": 0, "body": "", "final_url": url, "error": "method not allowed"}
    if opener is not None:
        opened = opener(url, allow_homepage=allow_homepage, method=method, json_body=json_body)
        opened.setdefault("final_url", url)
        return opened
    data = None if json_body is None else json.dumps(json_body).encode()
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"User-Agent": "ScraplingAccuracySample/1.0", "Accept": "application/json,text/html",
                 **({"Content-Type": "application/json"} if data else {})},
    )
    try:
        with _OPENER.open(req, timeout=TIMEOUT_SEC) as resp:
            status = getattr(resp, "status", 200)
            body = resp.read(MAX_BYTES + 1)
            final = resp.geturl()
    except urllib.error.HTTPError as exc:
        status = exc.code
        location = exc.headers.get("Location") if exc.headers else None
        if status in (301, 302, 303, 307, 308) and location:
            if redirects >= MAX_REDIRECTS:
                return {"ok": False, "status": status, "body": "", "final_url": url, "error": "too many redirects"}
            nxt = _join(url, location)
            hop = validate_url(nxt, resolve=resolve, allow_homepage=allow_homepage)
            if hop:
                return {"ok": False, "status": status, "body": "", "final_url": nxt, "error": "redirect refused: " + hop}
            return fetch_public(nxt, resolve=resolve, allow_homepage=allow_homepage, redirects=redirects + 1, method="GET")
        snippet = exc.read(MAX_BYTES) if exc.fp else b""
        return {"ok": False, "status": status, "body": snippet.decode("utf-8", "replace"), "final_url": url,
                "error": f"http {status}"}
    except Exception as exc:
        return {"ok": False, "status": 0, "body": "", "final_url": url, "error": type(exc).__name__}
    if validate_url(final, resolve=resolve, allow_homepage=allow_homepage):
        return {"ok": False, "status": status, "body": "", "final_url": final, "error": "final url refused"}
    truncated = len(body) > MAX_BYTES
    text = body[:MAX_BYTES].decode("utf-8", "replace")
    return {"ok": status == 200, "status": status, "body": text, "final_url": final, "error": None, "truncated": truncated}


def _join(base: str, location: str) -> str:
    from urllib.parse import urljoin
    return urljoin(base, location)


def _row(**kwargs: Any) -> Dict[str, Any]:
    kwargs.setdefault("ours", {})
    kwargs.setdefault("evidence", {})
    kwargs.setdefault("tolerance", "")
    kwargs.setdefault("reason", "")
    return kwargs


def _company_default(q: str) -> Optional[Dict[str, Any]]:
    try:
        from . import labels
        return labels.company(q, live_site=False)
    except Exception:
        return None


def _label_default(address: str) -> Optional[Dict[str, Any]]:
    try:
        from . import labels
        return labels.lookup(address)
    except Exception:
        return None


def _perps_default() -> Optional[Dict[str, Any]]:
    try:
        from . import stock_perps
        return stock_perps.markets_snapshot()
    except Exception:
        return None


def _flows_default() -> Optional[Dict[str, Any]]:
    try:
        from . import binance_flows as flows
        return (flows.S.get("cache") or {}).get("snapshot")
    except Exception:
        return None


def _blocked_status(status: int) -> bool:
    return status in (401, 403, 429, 451)


def check_company(now: float, fetch: Callable[..., Dict[str, Any]], ours_for: Callable[[str], Optional[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    rows = []
    for sample in COMPANY_SAMPLE:
        ours = ours_for(sample["q"])
        if not ours:
            rows.append(_row(product="company", sample_id=sample["id"], claim="homepage_mentions_project_name",
                             source_relation="homepage_corroboration", independent=True, result="unknown",
                             reason="no local company record to compare", evidence_at=_iso(now), source_url=None))
            continue
        name = str(ours.get("name") or "")
        website = str(ours.get("website") or "")
        base = {"name": name, "website": website, "as_of": ours.get("as_of")}
        if not name or not website:
            rows.append(_row(product="company", sample_id=sample["id"], claim="homepage_mentions_project_name",
                             source_relation="homepage_corroboration", independent=True, result="skipped",
                             reason="our record has no name or website", ours=base, evidence_at=_iso(now), source_url=website or None))
            continue
        got = fetch(website, allow_homepage=True)
        if not got.get("ok"):
            result = "unknown" if _blocked_status(int(got.get("status") or 0)) else "error"
            rows.append(_row(product="company", sample_id=sample["id"], claim="homepage_mentions_project_name",
                             source_relation="homepage_corroboration", independent=True, result=result,
                             reason=got.get("error") or "homepage fetch failed", ours=base,
                             evidence={"status": got.get("status")}, evidence_at=_iso(now), source_url=website,
                             tolerance="HTTP 200 and the project name appears in the first 256KB"))
            continue
        if got.get("truncated") and name.lower() not in got.get("body", "").lower():
            rows.append(_row(product="company", sample_id=sample["id"], claim="homepage_mentions_project_name",
                             source_relation="homepage_corroboration", independent=True, result="unknown",
                             reason="response truncated before the name was seen", ours=base,
                             evidence={"status": got.get("status"), "truncated": True}, evidence_at=_iso(now),
                             source_url=got.get("final_url") or website))
            continue
        mentioned = name.lower() in got.get("body", "").lower()
        rows.append(_row(product="company", sample_id=sample["id"], claim="homepage_mentions_project_name",
                         source_relation="homepage_corroboration", independent=True,
                         result="match" if mentioned else "mismatch",
                         reason="" if mentioned else "project name not found on the homepage we publish",
                         ours=base, evidence={"status": got.get("status"), "mentions_name": mentioned},
                         evidence_at=_iso(now), source_url=got.get("final_url") or website,
                         tolerance="case-insensitive name in the fetched homepage; not a full profile audit"))
    return rows


def check_wallets(now: float, fetch: Callable[..., Dict[str, Any]], ours_for: Callable[[str], Optional[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    rows = []
    for sample in WALLET_SAMPLE:
        if sample.get("skip"):
            rows.append(_row(product="wallet-label", sample_id=sample["id"], claim="explorer_name_mention",
                             source_relation="unsupported", independent=False, result="skipped",
                             reason=sample["skip"], evidence_at=_iso(now), source_url=None,
                             tolerance="not applicable"))
            continue
        ours = ours_for(sample["address"])
        entity = str((ours or {}).get("entity") or "")
        url = "https://etherscan.io/address/" + sample["address"]
        if not ours or not entity:
            rows.append(_row(product="wallet-label", sample_id=sample["id"], claim="explorer_name_mention",
                             source_relation="explorer_name_mention", independent=True, result="unknown",
                             reason="our label has no entity to look for", ours={"address": sample["address"]},
                             evidence_at=_iso(now), source_url=url,
                             tolerance="entity string appears in the explorer HTML; not an ownership proof"))
            continue
        got = fetch(url, allow_homepage=False)
        if not got.get("ok"):
            result = "unknown" if _blocked_status(int(got.get("status") or 0)) else "error"
            rows.append(_row(product="wallet-label", sample_id=sample["id"], claim="explorer_name_mention",
                             source_relation="explorer_name_mention", independent=True, result=result,
                             reason=got.get("error") or "explorer fetch failed",
                             ours={"address": sample["address"], "entity": entity, "as_of": (ours or {}).get("as_of")},
                             evidence={"status": got.get("status")}, evidence_at=_iso(now), source_url=url,
                             tolerance="entity string appears in the explorer HTML; not an ownership proof"))
            continue
        mentioned = entity.lower() in got.get("body", "").lower()
        rows.append(_row(product="wallet-label", sample_id=sample["id"], claim="explorer_name_mention",
                         source_relation="explorer_name_mention", independent=True,
                         result="match" if mentioned else "mismatch",
                         reason="" if mentioned else "entity string not found on the explorer page",
                         ours={"address": sample["address"], "entity": entity, "as_of": ours.get("as_of")},
                         evidence={"status": got.get("status"), "mentions_entity": mentioned},
                         evidence_at=_iso(now), source_url=got.get("final_url") or url,
                         tolerance="case-insensitive entity text on the explorer page; balances are ignored"))
    return rows


def check_perps(now: float, fetch: Callable[..., Dict[str, Any]], ours_snap: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    markets = {str(m.get("symbol") or "").upper(): m for m in (ours_snap or {}).get("markets") or [] if isinstance(m, dict)}
    age = None
    fetched = (ours_snap or {}).get("fetched_at")
    if isinstance(fetched, (int, float)):
        age = now - float(fetched)
    url = "https://api.hyperliquid.xyz/info"
    fresh_payload = None
    fetch_error = None
    comparable = []
    for symbol in PERPS_SYMBOLS:
        ours = markets.get(symbol)
        if ours and ours.get("mark_px") is not None and age is not None and age <= PERPS_FRESH_SEC:
            comparable.append(symbol)
    if comparable:
        got = fetch(url, allow_homepage=False, method="POST", json_body={"type": "metaAndAssetCtxs", "dex": "xyz"})
        if got.get("ok"):
            try:
                fresh_payload = json.loads(got.get("body") or "null")
            except Exception:
                fetch_error = "fresh payload was not JSON"
        else:
            fetch_error = got.get("error") or "hyperliquid fetch failed"
            if _blocked_status(int(got.get("status") or 0)):
                fetch_error = "blocked:" + str(fetch_error)
    for symbol in PERPS_SYMBOLS:
        ours = markets.get(symbol)
        if not ours or ours.get("mark_px") is None:
            rows.append(_row(product="stock-perps", sample_id=symbol, claim="mark_px_vs_hyperliquid",
                             source_relation="same_upstream", independent=False, result="unknown",
                             reason="no local mark for this symbol", evidence_at=_iso(now), source_url=url,
                             tolerance=f"relative {PRICE_TOLERANCE} within {PERPS_FRESH_SEC}s; same Hyperliquid info API"))
            continue
        if age is None or age > PERPS_FRESH_SEC:
            rows.append(_row(product="stock-perps", sample_id=symbol, claim="mark_px_vs_hyperliquid",
                             source_relation="same_upstream", independent=False, result="skipped",
                             reason="local snapshot is stale or undated; not scored as a mismatch",
                             ours={"symbol": symbol, "mark_px": ours.get("mark_px"), "as_of": (ours_snap or {}).get("as_of")},
                             evidence_at=_iso(now), source_url=url,
                             tolerance=f"relative {PRICE_TOLERANCE} only when local age <= {PERPS_FRESH_SEC}s"))
            continue
        if fetch_error:
            blocked = str(fetch_error).startswith("blocked:")
            rows.append(_row(product="stock-perps", sample_id=symbol, claim="mark_px_vs_hyperliquid",
                             source_relation="same_upstream", independent=False,
                             result="unknown" if blocked else "error", reason=fetch_error,
                             ours={"symbol": symbol, "mark_px": ours.get("mark_px")}, evidence_at=_iso(now), source_url=url))
            continue
        fresh = _hyperliquid_mark(fresh_payload, symbol)
        if fresh is None:
            rows.append(_row(product="stock-perps", sample_id=symbol, claim="mark_px_vs_hyperliquid",
                             source_relation="same_upstream", independent=False, result="unknown",
                             reason="fresh Hyperliquid payload had no mark for this symbol",
                             ours={"symbol": symbol, "mark_px": ours.get("mark_px")}, evidence_at=_iso(now), source_url=url))
            continue
        local = float(ours["mark_px"])
        rel = abs(fresh - local) / local if local else 1.0
        ok = rel <= PRICE_TOLERANCE
        rows.append(_row(product="stock-perps", sample_id=symbol, claim="mark_px_vs_hyperliquid",
                         source_relation="same_upstream", independent=False,
                         result="match" if ok else "mismatch",
                         reason="" if ok else f"relative delta {rel:.4f} exceeds {PRICE_TOLERANCE}",
                         ours={"symbol": symbol, "mark_px": local, "as_of": (ours_snap or {}).get("as_of")},
                         evidence={"mark_px": fresh, "relative_delta": round(rel, 6)},
                         evidence_at=_iso(now), source_url=url,
                         tolerance=f"same symbol, relative mark within {PRICE_TOLERANCE}, same Hyperliquid upstream"))
    return rows


def _symbol_name(coin: str) -> str:
    text = str(coin or "")
    return text.split(":")[-1].upper()


def _hyperliquid_mark(payload: Any, symbol: str) -> Optional[float]:
    """Read a mark for one symbol from a test payload or metaAndAssetCtxs."""
    if isinstance(payload, dict) and payload.get("mark_px") is not None and _symbol_name(payload.get("symbol") or symbol) == symbol:
        return float(payload["mark_px"])
    if isinstance(payload, dict):
        for row in payload.get("markets") or []:
            if isinstance(row, dict) and _symbol_name(row.get("symbol") or row.get("coin") or "") == symbol and row.get("mark_px") is not None:
                return float(row["mark_px"])
        return None
    if isinstance(payload, list) and len(payload) >= 2 and isinstance(payload[0], dict):
        universe = (payload[0].get("universe") or [])
        ctxs = payload[1] if isinstance(payload[1], list) else []
        for meta, ctx in zip(universe, ctxs):
            if not isinstance(meta, dict) or not isinstance(ctx, dict):
                continue
            if _symbol_name(meta.get("name") or "") == symbol and ctx.get("markPx") is not None:
                return float(ctx["markPx"])
        return None
    return None


def check_flows(now: float, ours_snap: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Announcement text is not a flow total. A missing transfer stays unknown."""
    rows = [_row(
        product="binance-flows", sample_id="announcement", claim="announcement_equals_net_outflow",
        source_relation="unsupported", independent=False, result="skipped",
        reason="A Binance announcement does not state our 1h or 24h net outflow and is not used as flow truth.",
        evidence_at=_iso(now), source_url="https://www.binance.com/en/support/announcement",
        tolerance="not compared",
    )]
    tx = None
    top = (ours_snap or {}).get("biggest_withdrawals_24h") or []
    if isinstance(top, list):
        for item in top:
            if isinstance(item, dict) and item.get("tx"):
                tx = item
                break
    if not tx:
        rows.append(_row(
            product="binance-flows", sample_id="sampled-transfer", claim="public_explorer_sees_transfer",
            source_relation="tx_existence", independent=True, result="unknown",
            reason="no transfer is in the current snapshot; the aggregate is not inferred from an announcement",
            evidence_at=_iso(now), source_url=None,
            tolerance="one sampled tx id would be looked up; USD totals are not",
        ))
        return rows
    rows.append(_row(
        product="binance-flows", sample_id="sampled-transfer", claim="public_explorer_sees_transfer",
        source_relation="same_upstream", independent=False, result="skipped",
        reason="A tx we already read from public chain data is the same upstream, so seeing it again is not independent flow accuracy.",
        ours={"tx": tx.get("tx"), "chain": tx.get("chain"), "usd": tx.get("usd")},
        evidence_at=_iso(now), source_url=None,
        tolerance="not counted as an independent match",
    ))
    return rows


def _tally(rows: List[Dict[str, Any]], *, independent: bool) -> Dict[str, Any]:
    chosen = [r for r in rows if bool(r.get("independent")) is independent]
    counts = {"matched": 0, "mismatched": 0, "skipped": 0, "errors": 0, "unknown": 0}
    for row in chosen:
        result = row.get("result")
        if result == "match":
            counts["matched"] += 1
        elif result == "mismatch":
            counts["mismatched"] += 1
        elif result == "skipped":
            counts["skipped"] += 1
        elif result == "error":
            counts["errors"] += 1
        else:
            counts["unknown"] += 1
    sample_count = len(chosen)
    checked = counts["matched"] + counts["mismatched"]
    if sample_count == 0 or checked == 0:
        rate = None
        status = "ungraded"
    else:
        rate = round(counts["matched"] / sample_count, 4)
        if rate < 0 or rate > 1:
            rate = None
            status = "ungraded"
        else:
            status = "sampled"
    return {
        "status": status,
        "match_rate": rate,
        "sample_count": sample_count,
        "checked": checked,
        **counts,
    }


def _product_block(product: str, rows: List[Dict[str, Any]], limitation: str) -> Dict[str, Any]:
    independent = _tally(rows, independent=True)
    consistency = _tally(rows, independent=False)
    return {
        "product": product,
        "status": independent["status"],
        "match_rate": independent["match_rate"],
        "sample_count": independent["sample_count"],
        "checked": independent["checked"],
        "matched": independent["matched"],
        "mismatched": independent["mismatched"],
        "skipped": independent["skipped"],
        "errors": independent["errors"],
        "unknown": independent["unknown"],
        "self_consistency": {
            "note": "Same upstream or unsupported claim. Not independent accuracy.",
            **consistency,
        },
        "limitation": limitation,
        "rows": rows,
    }


def run_once(*, now: Optional[float] = None, fetch: Optional[Callable[..., Dict[str, Any]]] = None,
             company_ours: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
             label_ours: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
             perps_ours: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
             flows_ours: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
             resolve: Optional[Callable[[str], List[str]]] = None) -> Dict[str, Any]:
    started = now if now is not None else time.time()
    def _default_fetch(url: str, allow_homepage: bool = False, method: str = "GET", json_body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return fetch_public(url, resolve=resolve, allow_homepage=allow_homepage, method=method, json_body=json_body)

    transport = fetch or _default_fetch
    company_rows = check_company(started, transport, company_ours or _company_default)
    wallet_rows = check_wallets(started, transport, label_ours or _label_default)
    perps_rows = check_perps(started, transport, (perps_ours or _perps_default)())
    flow_rows = check_flows(started, (flows_ours or _flows_default)())
    products = {
        "company": _product_block(
            "company", company_rows,
            "Homepage text is only a name mention on the site we already publish. It is not a full company audit. DefiLlama is our upstream for the website field and is not re-counted as an independent source.",
        ),
        "wallet-label": _product_block(
            "wallet-label", wallet_rows,
            "An explorer page that contains an entity string is not proof of who controls the wallet. Balances and transfers are not used.",
        ),
        "binance-flows": _product_block(
            "binance-flows", flow_rows,
            "Announcements are not the net-outflow figure. A transfer we already collected is the same chain data, not an independent check of the aggregate.",
        ),
        "stock-perps": _product_block(
            "stock-perps", perps_rows,
            "Hyperliquid public info is the same upstream as the stock-perps feed. A close mark is self-consistency within 2 percent, not an outside price source.",
        ),
    }
    state = {
        "kind": "sample_checks",
        "disclaimer": "Sample checks, not guaranteed correctness. Match rate is matched/sample_count for this small free-source sample only. Same-upstream rows are not independent accuracy.",
        "started_at": _iso(started),
        "started_at_unix": started,
        "interval_seconds": INTERVAL_SEC,
        "stale": False,
        "products": products,
    }
    _save(state)
    return state


def _save(state: Dict[str, Any]) -> None:
    path = _store_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, default=str)
    os.replace(tmp, path)


def load() -> Optional[Dict[str, Any]]:
    try:
        with open(_store_path(), encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def current(now: Optional[float] = None) -> Dict[str, Any]:
    """Persisted sample, marked stale when older than two intervals. Does not fetch."""
    now = time.time() if now is None else now
    state = load()
    if not state:
        return {
            "kind": "sample_checks",
            "disclaimer": "Sample checks have not run. Match rate is null, not 100%.",
            "started_at": None,
            "stale": False,
            "products": {},
            "status": "ungraded",
            "match_rate": None,
            "sample_count": 0,
        }
    started = float(state.get("started_at_unix") or 0)
    state["stale"] = bool(started and now - started > STALE_AFTER_SEC)
    return state


def maybe_run(now: Optional[float] = None, **kwargs: Any) -> Dict[str, Any]:
    """Run when the last persisted sample is older than four hours."""
    now = time.time() if now is None else now
    state = load()
    started = float((state or {}).get("started_at_unix") or 0)
    if state and now - started < INTERVAL_SEC and not kwargs.pop("force", False):
        return current(now)
    kwargs.pop("force", None)
    return run_once(now=now, **kwargs)


def html_section(checks: Optional[Dict[str, Any]]) -> str:
    if not checks:
        return ""
    products = checks.get("products") or {}
    rows = []
    for name in ("company", "wallet-label", "binance-flows", "stock-perps"):
        block = products.get(name) or {}
        rate = block.get("match_rate")
        rate_s = "null" if rate is None else f"{float(rate):.0%}"
        rows.append(
            "<tr><td>{p}</td><td>{st}</td><td>{rate}</td><td>{n}</td><td>{m}</td><td>{mm}</td>"
            "<td>{sk}</td><td>{er}</td><td>{un}</td><td>{lim}</td></tr>".format(
                p=_esc(name), st=_esc(block.get("status") or "ungraded"), rate=_esc(rate_s),
                n=_esc(block.get("sample_count")), m=_esc(block.get("matched")),
                mm=_esc(block.get("mismatched")), sk=_esc(block.get("skipped")),
                er=_esc(block.get("errors")), un=_esc(block.get("unknown")),
                lim=_esc(block.get("limitation")),
            )
        )
    stale = " This sample is stale." if checks.get("stale") else ""
    return (
        "<section><h2>Sample checks</h2><div class=\"card\">"
        "<p><strong>Sample checks, not guaranteed correctness.</strong> "
        "These are a few free public reads every four hours. They are not the letter grade. "
        "A null match rate means nothing comparable was checked, not a perfect score. "
        "Same-upstream rows are consistency with our own source, not an outside audit."
        + _esc(stale) + "</p>"
        "<p class=\"mono\">as_of " + _esc(checks.get("started_at")) + "</p></div>"
        "<div class=\"scroll\"><table><thead><tr><th>Product</th><th>Status</th><th>Match rate</th>"
        "<th>Sample</th><th>Matched</th><th>Mismatched</th><th>Skipped</th><th>Errors</th>"
        "<th>Unknown</th><th>Limitation</th></tr></thead><tbody>"
        + "".join(rows) + "</tbody></table></div></section>"
    )
