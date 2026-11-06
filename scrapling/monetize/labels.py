"""upd37: Wallet-to-entity labels + crypto company lookup. Free/public sources only.

Wallet labels: store's own lists (exchange_wallets.json from exchange PoR lists + MIT label sets, Binance flow wallets,
hack tracker attacker/bridge/mixer/dex labels, RealT and tokenized-stock token contracts) plus OFAC SDN digital-currency
addresses (0xB10C/ofac-sanctioned-digital-currency-addresses, US Treasury public data), MyEtherWallet ethereum-lists
darklist (MIT) and DefiLlama protocol token addresses. No Arkham/Nansen/Etherscan scraping/Whale Alert/Cielo/Chainalysis.
Company lookup: DefiLlama protocol metadata + the project's own homepage (docs/team links, role emails on its own domain).
Labels are off-chain facts: block = not_applicable.
"""
import logging
log = logging.getLogger("scrapling.labels")

import json, os, re, threading, time, urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urljoin, urlparse

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool

_CD = "/collected_data/labels" if os.path.isdir("/collected_data") else "/tmp/labels"
os.makedirs(_CD, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (compatible; ScraplingDataStore/1.0; +https://scraplingdata.xyz)"}
NA = {"chain": None, "number": None, "basis": "not_applicable (off-chain label)"}
DAO = lambda ts: {"timestamp_utc": ts, "time_source": "label_source", "onchain": False, "block": NA}
# upd38: reserved addresses -> category 'special' (checked before any source; never a protocol/exchange label)
SPECIAL = {"0x0000000000000000000000000000000000000000": "Null / zero address (mint & burn sink, not owned by anyone)",
           "0x000000000000000000000000000000000000dead": "Burn address 0x...dEaD (tokens sent here are unrecoverable)",
           "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee": "Native-coin placeholder 0xEeee... (used by DEX aggregators for ETH/native)",
           "0xdeaddeaddeaddeaddeaddeaddeaddeaddead0000": "OP-stack system depositor (L1->L2 deposits)",
           "0x4200000000000000000000000000000000000006": "OP-stack predeploy: WETH",
           "0x4200000000000000000000000000000000000016": "OP-stack predeploy: L2ToL1MessagePasser",
           "0x4200000000000000000000000000000000000010": "OP-stack predeploy: L2StandardBridge",
           "0x4200000000000000000000000000000000000007": "OP-stack predeploy: L2CrossDomainMessenger",
           "0x00000000219ab540356cbb839cbe05303d7705fa": "Ethereum beacon deposit contract (system)",
           "0x0000000000000000000000000000000000001000": "BSC system contract: ValidatorSet",
           "0x0000000000000000000000000000000000001004": "BSC system contract: TokenHub",
           "0x0000000000000000000000000000000000001010": "Polygon PoS system contract: native POL/MATIC token",
           "11111111111111111111111111111111": "Solana System Program",
           "t9yd14ngpzx6g5e3g3cczzhnzmkp4c": "Tron black-hole / burn address (T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb)"}
for _i in range(1, 11):
    SPECIAL["0x%040x" % _i] = "EVM precompile 0x%02x (%s)" % (_i, ["ecrecover", "sha256", "ripemd160", "identity", "modexp", "ecAdd",
                                                                  "ecMul", "ecPairing", "blake2f", "point evaluation"][_i - 1])
SPECIAL["0x0000000000000000000000000000000000000100"] = "EVM precompile 0x100 (P256VERIFY, RIP-7212)"


def _special(a: str):
    k = a.lower()
    if k.startswith("t9yd14n"):
        k = "t9yd14ngpzx6g5e3g3cczzhnzmkp4c"
    return SPECIAL.get(k)


def _placeholder(a: str) -> bool:
    b = a.lower()[2:] if a.lower().startswith("0x") else a.lower()
    return bool(_special(a)) or len(set(b)) <= 2 or b.strip("0") == "" or len(b) < 20
P_LABEL, P_BULK, P_CO = 0.002, 0.05, 0.003
OFAC_BASE = "https://raw.githubusercontent.com/0xB10C/ofac-sanctioned-digital-currency-addresses/lists/sanctioned_addresses_%s.txt"
OFAC_ASSETS = {"ETH": "ethereum", "XBT": "bitcoin", "TRX": "tron", "USDT": "ethereum/tron", "USDC": "ethereum", "ARB": "arbitrum",
               "BSC": "bsc", "SOL": "solana", "LTC": "litecoin", "BCH": "bitcoin-cash", "XMR": "monero", "DASH": "dash", "ZEC": "zcash",
               "ETC": "ethereum-classic", "BSV": "bitcoin-sv", "XRP": "xrp"}
MEW = "https://raw.githubusercontent.com/MyEtherWallet/ethereum-lists/master/src/addresses/addresses-darklist.json"
LLAMA = "https://api.llama.fi/protocols"
ETH_LABELS = "https://raw.githubusercontent.com/dawsbot/eth-labels/v1/data/json/accounts.json"
BYBIT_POR = "https://raw.githubusercontent.com/Bybit-Exchange/bybit-por/main/por.csv"
BITFINEX_WALLETS = "https://raw.githubusercontent.com/bitfinexcom/pub/main/wallets.txt"
TTL = 24 * 3600
REBUILD_EVERY_SEC = int(os.environ.get("SCRAPLING_LABELS_REBUILD_SEC", str(6 * 3600)))
CHAIN_BY_ID = {1: "ethereum", 8453: "base", 42161: "arbitrum", 137: "polygon", 10: "optimism", 56: "bsc"}
_lk = threading.Lock()
_ix: Dict[str, Any] = {"idx": {}, "built": 0.0, "counts": {}, "as_of": None, "llama": [], "llama_as_of": None}
_site_cache: Dict[str, Any] = {}
SITE_FILE = os.path.join(_CD, "company_sites.json")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _key(a: str) -> str:
    a = (a or "").strip()
    return a.lower() if a.lower().startswith("0x") else a


def _get(url: str, timeout: int = 25, limit: int = 30_000_000) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read(limit)


def _cached_fetch(name: str, url: str) -> (Any, str):
    """Fetch public file once per TTL; keep last good copy on disk."""
    p = os.path.join(_CD, name)
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < TTL:
        pass
    else:
        try:
            raw = _get(url)
            tmp = p + ".tmp"
            open(tmp, "wb").write(raw)
            os.replace(tmp, p)
        except Exception:
            if not os.path.exists(p):
                return None, None
    ts = datetime.fromtimestamp(os.path.getmtime(p), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return open(p, "rb").read(), ts


def _add(idx, counts, addr, chains, label, entity, category, source, url, conf, as_of):
    k = _key(addr)
    if not k or len(k) < 20:
        return
    lst = idx.setdefault(k, [])
    for e in lst:
        if e["entity"] == entity and e["category"] == category:
            for c in chains:
                if c and c not in e["chains"]:
                    e["chains"].append(c)
            return
    lst.append({"label": label, "entity": entity, "category": category, "chains": [c for c in chains if c], "source": source,
                "source_url": url, "confidence": conf, "as_of": as_of, "block": NA})
    counts[category] = counts.get(category, 0) + 1


def build() -> None:
    idx: Dict[str, List[Dict[str, Any]]] = {}
    counts: Dict[str, int] = {}
    # 1. exchange wallets (PoR lists + MIT label datasets) already in the store
    try:
        d = json.load(open("/collected_data/manual/exchange_wallets.json"))
        ts = d.get("generated_at")
        for r in d.get("labels") or []:
            src = r.get("source") or ""
            conf = "high" if "official" in src.lower() or "disclosed" in src.lower() else "medium"
            kind = r.get("kind") or "wallet"
            cat = "exchange_" + ("deposit" if "deposit" in kind else "cold_wallet" if "cold" in kind else "hot_wallet" if "hot" in kind
                                 else "reserve_wallet" if "reserve" in kind else "wallet")
            _add(idx, counts, r.get("address"), [r.get("chain")], r.get("name") or r.get("exchange"), r.get("exchange"), cat, src,
                 r.get("source_url") or "https://github.com/dawsbot/eth-labels", conf, ts)
    except Exception:
        pass
    # 2. Binance flow wallets
    try:
        from . import binance_flows as bf
        cmap = {"BTC": "bitcoin", "ETH": "ethereum", "TRX": "tron", "BEP20": "bsc"}
        for k, v in bf.LISTS.items():
            for a in v:
                _add(idx, counts, a, [cmap.get(k, k.lower())], "Binance wallet (" + k + ")", "Binance", "exchange_hot_wallet",
                     "Binance official Proof of Reserves address list", "https://www.binance.com/en/proof-of-reserves", "high", _now())
    except Exception:
        pass
    # 3. hack tracker: exploit wallets + infra labels
    try:
        from . import hack_tracker as ht
        for iid, inc in ht.INCIDENTS.items():
            for r in inc.get("addresses") or []:
                _add(idx, counts, r["address"], [inc.get("chain", "ethereum")], r["label"], inc["name"], "hacker_exploit_wallet",
                     r.get("source", ""), (inc.get("references") or [None])[0], r.get("confidence", "medium"), inc.get("date"))
        kc = {"mixer": "mixer", "bridge": "bridge", "dex": "dex_router", "defi": "defi_protocol", "cex": "exchange_hot_wallet"}
        for a, r in ht.LABELS.items():
            _add(idx, counts, a, ["ethereum"], r["label"], r.get("exchange") or r["label"].split(" (")[0], kc.get(r.get("kind"), r.get("kind")),
                 r.get("source", ""), None, "high" if r.get("kind") != "cex" else "medium", _now())
    except Exception:
        pass
    try:
        from . import hack_multichain as hm
        for ch, c in getattr(hm, "CHAINS_BASE", hm.CHAINS).items():
            for r in c.get("addresses") or []:
                _add(idx, counts, r["address"], [ch], r["label"], "Binance hot wallet exploit (2026-10-29)", "hacker_exploit_wallet",
                     r.get("source", ""), hm.REFS[0] if hm.REFS else None, r.get("confidence", "medium"), "2026-10-30")
        for a, r in hm.TRON_LABELS.items():
            _add(idx, counts, a, ["tron"], r["label"], r["label"], r.get("kind", "protocol"), r.get("source", ""), None, "high", _now())
    except Exception:
        pass
    # 4. tokenized stocks + RealT token contracts
    try:
        from . import tokenized_stocks as ts_
        for t in ts_.TOKENS:
            _add(idx, counts, t[2], [str(t[1]).lower()], t[5] + " token (" + t[3] + ")", t[0], "tokenized_stock_contract",
                 "Issuer-published token list (" + t[0] + ")", None, "high", _now())
    except Exception:
        pass
    try:
        from . import tokenized_real_estate as tre
        c = json.load(open(tre.CACHE_FILE))
        rows = c[1] if isinstance(c, list) else (c.get("rows") or [])
        for r in rows:
            _add(idx, counts, r.get("contract"), ["gnosis"], r.get("token"), "RealT", "tokenized_real_estate_contract",
                 "RealT RealTokenWrapper registry (on-chain)", r.get("explorer"), "high", r.get("as_of"))
    except Exception:
        pass
    # 5. OFAC SDN sanctioned addresses
    for asset, chain in OFAC_ASSETS.items():
        raw, ts = _cached_fetch("ofac_" + asset + ".txt", OFAC_BASE % asset)
        if not raw:
            continue
        for line in raw.decode("utf-8", "ignore").split():
            _add(idx, counts, line, [chain], "OFAC SDN-listed address (" + asset + ")", "OFAC-sanctioned entity", "sanctioned",
                 "US Treasury OFAC SDN list via github.com/0xB10C/ofac-sanctioned-digital-currency-addresses",
                 "https://sanctionssearch.ofac.treas.gov/", "high", ts)
    # 6. MEW darklist (phishing/scam)
    raw, ts = _cached_fetch("mew_darklist.json", MEW)
    try:
        for r in json.loads(raw or b"[]"):
            _add(idx, counts, r.get("address"), ["ethereum"], (r.get("comment") or "Reported scam/phishing address")[:160],
                 "Scam / phishing (reported)", "scam_phishing", "MyEtherWallet ethereum-lists darklist (MIT)",
                 "https://github.com/MyEtherWallet/ethereum-lists", "medium", r.get("date") or ts)
    except Exception:
        pass
    # 7. DefiLlama protocol token addresses
    raw, lts = _cached_fetch("llama_protocols.json", LLAMA)
    llama = []
    try:
        llama = json.loads(raw or b"[]")
        _seen: Dict[str, int] = {}
        for p in llama:
            a = p.get("address")
            if isinstance(a, str) and a:
                k = a.rpartition(":")[2].lower()
                _seen[k] = _seen.get(k, 0) + 1
        for p in llama:
            a = p.get("address")
            if not a or not isinstance(a, str):
                continue
            ch, _, ad = a.rpartition(":")
            if _placeholder(ad) or _seen.get(ad.lower(), 0) > 1:
                counts["dropped_defillama_placeholders"] = counts.get("dropped_defillama_placeholders", 0) + 1
                continue
            _add(idx, counts, ad, [ch.lower() if ch else ((p.get("chains") or [""])[0]).lower()], (p.get("name") or "") + " token (" +
                 str(p.get("symbol") or "") + ")", p.get("name"), "protocol_token_contract", "DefiLlama protocols metadata",
                 "https://defillama.com/protocol/" + str(p.get("slug") or ""), "high", lts)
    except Exception:
        pass
    # 8. dawsbot/eth-labels (MIT) — refreshed on TTL; exchange-tagged rows preferred
    try:
        raw, ts = _cached_fetch("eth_labels_accounts.json", ETH_LABELS)
        if raw:
            rows = json.loads(raw)
            exch_kw = ("binance", "coinbase", "kraken", "okx", "okex", "bybit", "bitfinex", "kucoin", "huobi", "htx",
                       "gate.io", "gateio", "bitget", "mexc", "gemini", "bitstamp", "poloniex", "crypto.com", "bitmex",
                       "deribit", "ftx", "bithumb", "upbit")
            n_add = 0
            for r in rows:
                if not isinstance(r, dict):
                    continue
                addr = r.get("address")
                label = (r.get("label") or "")
                tag = (r.get("nameTag") or label or "")[:160]
                low = (label + " " + tag).lower()
                is_ex = any(k in low for k in exch_kw)
                if not is_ex and n_add > 50000:
                    continue
                try:
                    cid = int(r.get("chainId")) if r.get("chainId") is not None else 1
                except Exception:
                    cid = 1
                ch = CHAIN_BY_ID.get(cid, "ethereum")
                cat = "exchange_hot_wallet" if is_ex else "public_nametag"
                ent = tag.split(":")[0].strip() if tag else (label or "eth-labels")
                _add(idx, counts, addr, [ch], tag or label or "eth-labels", ent, cat,
                     "dawsbot/eth-labels (MIT License)", "https://github.com/dawsbot/eth-labels",
                     "high" if is_ex else "medium", ts)
                n_add += 1
            counts["eth_labels_rows_loaded"] = n_add
    except Exception:
        pass
    # 9. Bybit official PoR CSV (best-effort; skip if URL moves)
    try:
        raw, ts = _cached_fetch("bybit_por.csv", BYBIT_POR)
        if raw:
            n = 0
            for line in raw.decode("utf-8", "ignore").splitlines()[1:]:
                parts = [p.strip().strip('"') for p in line.split(",")]
                if not parts:
                    continue
                addr = next((p for p in parts if p.startswith("0x") or (len(p) >= 26 and p.isalnum())), "")
                if not addr or len(addr) < 20:
                    continue
                chain = "ethereum"
                blob = " ".join(parts).lower()
                if "btc" in blob or "bitcoin" in blob: chain = "bitcoin"
                elif "sol" in blob: chain = "solana"
                _add(idx, counts, addr, [chain], "Bybit PoR wallet", "Bybit", "exchange_reserve_wallet",
                     "Bybit official Proof of Reserves CSV", "https://github.com/Bybit-Exchange/bybit-por", "high", ts)
                n += 1
            counts["bybit_por_rows"] = n
    except Exception:
        pass
    # 10. Bitfinex published wallets.txt
    try:
        raw, ts = _cached_fetch("bitfinex_wallets.txt", BITFINEX_WALLETS)
        if raw:
            n = 0
            for line in raw.decode("utf-8", "ignore").split():
                a = line.strip()
                if len(a) < 20:
                    continue
                chain = "ethereum" if a.startswith("0x") else "bitcoin"
                _add(idx, counts, a, [chain], "Bitfinex published wallet", "Bitfinex", "exchange_reserve_wallet",
                     "Bitfinex pub/wallets.txt", "https://github.com/bitfinexcom/pub", "high", ts)
                n += 1
            counts["bitfinex_wallets"] = n
    except Exception:
        pass
    with _lk:
        _ix.update(idx=idx, counts=counts, built=time.time(), as_of=_now(), llama=llama, llama_as_of=lts)


def _ensure() -> None:
    if time.time() - _ix["built"] > 6 * 3600 or not _ix["idx"]:
        build()


def lookup(address: str) -> Dict[str, Any]:
    _ensure()
    a = (address or "").strip()
    if not a or len(a) < 20 or len(a) > 100 or not re.fullmatch(r"[A-Za-z0-9:]+", a):
        raise ValueError("address must be a wallet/contract address (0x..., bc1..., T..., base58)")
    sp = _special(a)
    if sp:
        return {"address": a, "match": True, "label": sp, "entity": None, "category": "special", "chains": ["all"],
                "confidence": "high", "source": "protocol specification (reserved/system address)", "source_url": None,
                "as_of": _ix["as_of"], "corpus_as_of": _ix["as_of"], "labeled_addresses": len(_ix["idx"]),
                "block": NA, "data_as_of": DAO(_ix["as_of"]),
                "labels": [{"label": sp, "category": "special", "confidence": "high"}],
                "note": "Reserved or system address: nobody controls it as a wallet. Not an exchange, protocol or person."}
    hits = _ix["idx"].get(_key(a)) or []
    if not hits:
        return {"address": a, "match": False, "label": "unknown", "entity": None, "category": "unknown", "chains": [],
                "confidence": None, "labels": [], "as_of": _ix["as_of"], "corpus_as_of": _ix["as_of"],
                "labeled_addresses": len(_ix["idx"]), "block": NA, "data_as_of": DAO(_ix["as_of"]),
                "note": "No public label for this address in our sources. Unknown is not a safety verdict."}
    rank = {"high": 0, "medium": 1, "low": 2}
    hs = sorted(hits, key=lambda e: rank.get(e["confidence"], 3))
    top = hs[0]
    return {"address": a, "match": True, "label": top["label"], "entity": top["entity"], "category": top["category"],
            "chains": top["chains"], "confidence": top["confidence"], "source": top["source"], "source_url": top["source_url"],
            "as_of": _ix["as_of"], "label_as_of": top["as_of"], "corpus_as_of": _ix["as_of"],
            "labeled_addresses": len(_ix["idx"]), "block": NA, "data_as_of": DAO(_ix["as_of"]), "labels": hs}


def coverage() -> Dict[str, Any]:
    _ensure()
    return {"labeled_addresses": len(_ix["idx"]), "labels_by_category": _ix["counts"], "index_built": _ix["as_of"],
            "companies": len(_ix["llama"])}


# ---------------------------------------------------------------- company lookup
ROLE = re.compile(r"^(press|media|pr|business|bd|biz|partners?|partnerships?|contact|info|hello|support|team|marketing|listings?|"
                  r"investors?|ir|legal|security|careers|jobs|sales|enquiries|inquiries|general|admin|help|ecosystem|grants)$", re.I)


def _n(s: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def find_company(q: str) -> Optional[Dict[str, Any]]:
    _ensure()
    L = _ix["llama"]
    qn, qk = _n(q), _key(q)
    if qk.startswith("0x") or len(q) > 30:
        for p in L:
            a = p.get("address")
            if isinstance(a, str) and _key(a.rpartition(":")[2]) == qk:
                return p
        hits = _ix["idx"].get(qk) or []
        for h in hits:
            for p in L:
                if _n(p.get("name")) == _n(h.get("entity")):
                    return p
        return None
    exact = [p for p in L if qn in (_n(p.get("slug")), _n(p.get("name")))]
    if not exact:
        exact = [p for p in L if _n(p.get("symbol")) == qn]
    if not exact:
        exact = [p for p in L if _n(p.get("name")).startswith(qn) and len(qn) >= 3]
    if not exact:
        return None
    return max(exact, key=lambda p: (p.get("parentProtocol") is None, p.get("tvl") or 0))


_site_lk = threading.Lock()


def _site(url: str) -> Dict[str, Any]:
    if not url:
        return {}
    c = _site_cache.get(url)
    if c and time.time() - c["_t"] < 7 * 86400:
        return c
    out: Dict[str, Any] = {"_t": time.time(), "fetched_at": _now(), "docs_url": None, "team_page_url": None, "contact_emails": [],
                           "github_links": [], "x_links": [], "fetch_ok": False}
    try:
        html = _get(url, timeout=10, limit=3_000_000).decode("utf-8", "ignore")
        out["fetch_ok"] = True
        dom = urlparse(url).netloc.lower().removeprefix("www.")
        root = ".".join(dom.split(".")[-2:])
        hrefs = re.findall(r'href\s*=\s*["\']([^"\']+)["\']', html, re.I)
        for h in hrefs:
            hl = h.lower()
            if hl.startswith("mailto:"):
                em = h[7:].split("?")[0].strip()
                if "@" in em:
                    loc, _, edom = em.lower().partition("@")
                    if (edom == root or edom.endswith("." + root)) and ROLE.match(loc) and em.lower() not in out["contact_emails"]:
                        out["contact_emails"].append(em.lower())
                continue
            full = urljoin(url, h)
            fl = full.lower()
            if not out["docs_url"] and (re.search(r"//docs\.|/docs\b|gitbook\.io|/documentation|developers\.", fl)):
                out["docs_url"] = full
            if not out["team_page_url"] and re.search(r"/(team|about|about-us|company|people)(/|$|\?|#)", fl) and root in fl:
                out["team_page_url"] = full
            if "github.com/" in fl and full not in out["github_links"] and len(out["github_links"]) < 3:
                out["github_links"].append(full)
            if re.search(r"//(www\.)?(x|twitter)\.com/", fl) and full not in out["x_links"] and len(out["x_links"]) < 3:
                out["x_links"].append(full)
        for em in re.findall(r"\b([a-z0-9._-]+)@([a-z0-9.-]+\.[a-z]{2,})\b", html, re.I):
            loc, edom = em[0].lower(), em[1].lower()
            e = loc + "@" + edom
            if (edom == root or edom.endswith("." + root)) and ROLE.match(loc) and e not in out["contact_emails"]:
                out["contact_emails"].append(e)
        out["contact_emails"] = out["contact_emails"][:5]
    except Exception as e:
        out["error"] = type(e).__name__
    _site_cache[url] = out
    try:
        with _site_lk:  # upd40: concurrent crawl workers
            tmp = SITE_FILE + ".tmp"
            json.dump(dict(_site_cache), open(tmp, "w"))
            os.replace(tmp, SITE_FILE)
    except Exception:
        pass
    return out


def company(q: str, live_site: bool = True) -> Optional[Dict[str, Any]]:
    p = find_company(q)
    if not p:
        return None
    url = p.get("url")
    gh = p.get("github") or []
    s = _site(url) if live_site else {}
    tw = p.get("twitter")
    src = [{"field": "name/website/x/github/category/chains", "source": "DefiLlama protocols metadata",
            "url": "https://defillama.com/protocol/" + str(p.get("slug")), "as_of": _ix["llama_as_of"]}]
    if s:
        src.append({"field": "docs/team page/contact emails", "source": "project's own homepage", "url": url, "as_of": s.get("fetched_at"),
                    "fetch_ok": s.get("fetch_ok")})
    return {"query": q, "name": p.get("name"), "slug": p.get("slug"), "symbol": p.get("symbol") if p.get("symbol") not in ("-", None) else None,
            "category": p.get("category"), "chains": p.get("chains"), "token_address": p.get("address"),
            "website": url, "x_handle": ("@" + tw) if tw else None, "x_url": ("https://x.com/" + tw) if tw else None,
            "github_orgs": ["https://github.com/" + g for g in gh] or (s.get("github_links") if s else []),
            "docs_url": s.get("docs_url") if s else None, "team_page_url": s.get("team_page_url") if s else None,
            "contact_emails": s.get("contact_emails") if s else [],
            "contact_email_policy": "Only role/business addresses (press@, contact@, partnerships@ ...) published on the project's own domain. No personal emails or phones.",
            "description": (p.get("description") or "")[:500], "sources": src, "as_of": _now(), "block": NA, "data_as_of": DAO(_now())}


# ---------------------------------------------------------------- routes
LOOKUPS_LB = {
    "wallet-label": {"path": "/v1/wallet-label", "price": P_LABEL, "example_path": "/v1/wallet-label?address=0x28c6c06298d514db089934071355e5743bf21d60",
        "desc": "Wallet-to-entity lookup: who an address belongs to (exchange hot/cold/deposit wallet, hacker/exploit wallet, OFAC-sanctioned, "
                "bridge, mixer, DEX router, protocol token, tokenized stock or real estate token, reported scam). Returns label, entity, category, "
                "chains, source + link, confidence, as_of (block not_applicable: off-chain label). Sources: exchange proof-of-reserves lists, "
                "MIT label sets, OFAC SDN, MEW darklist, DefiLlama, our hack tracker. No match returns label 'unknown' (still a valid answer)",
        "query": {"address": "0x28c6c06298d514db089934071355e5743bf21d60"},
        "props": {"address": {"type": "string", "description": "EVM 0x address, BTC, Tron or Solana address"}}, "required": ["address"],
        "out": {"address": "0x28c6...", "match": True, "label": "Binance 14", "entity": "Binance", "category": "exchange_hot_wallet",
                "chains": ["ethereum"], "confidence": "high", "source": "...", "source_url": "...", "as_of": "2026-11-02T22:00:00Z",
                "block": NA, "labels": []}},
    "wallet-label-bulk": {"path": "/v1/wallet-label/bulk", "price": P_BULK, "example_path": "/v1/wallet-label/bulk?addresses=0x28c6c06298d514db089934071355e5743bf21d60,0x7C8601d4645b93cea45F71e118A729332e6B5CA0",
        "desc": "Bulk wallet-to-entity labels: up to 50 comma-separated addresses in one call, same fields as /v1/wallet-label per address, plus matched count",
        "query": {"addresses": "0x28c6c06298d514db089934071355e5743bf21d60,0x7C8601d4645b93cea45F71e118A729332e6B5CA0"},
        "props": {"addresses": {"type": "string", "description": "Comma-separated addresses (max 50)"}}, "required": ["addresses"],
        "out": {"count": 2, "matched": 2, "results": []}},
    "company": {"path": "/v1/company", "price": P_CO, "example_path": "/v1/company?q=aave",
        "desc": "Crypto company / project lookup by token symbol, contract address or name: official website, X handle, GitHub org, docs URL, "
                "team page URL, business/press contact email only when published on the project's own domain (no personal data), category, "
                "chains, description, with source links and as_of. Sources: DefiLlama protocol metadata + the project's own homepage",
        "query": {"q": "aave"}, "props": {"q": {"type": "string", "description": "Symbol (AAVE), contract (0x...) or project name"}},
        "required": ["q"],
        "out": {"name": "Aave", "website": "https://aave.com", "x_handle": "@aave", "github_orgs": ["https://github.com/aave"],
                "docs_url": "https://docs.aave.com", "team_page_url": None, "contact_emails": [], "sources": [], "as_of": "...", "block": NA}},
}
FREE_LB = {"/v1/free/wallet-label": "Free wallet-label sampler: entity and category for one address (no source links, confidence or "
           "multi-label detail). Example: /v1/free/wallet-label?address=0x28c6c06298d514db089934071355e5743bf21d60. Full answer: /v1/wallet-label ($0.002), bulk 50: /v1/wallet-label/bulk ($0.05)",
           "/v1/free/company": "Free company/project sample: name, website, X handle, category, as_of only. Example: /v1/free/company?q=aave. Full profile: "
           "/v1/company ($0.003)"}
RAPID = {"/rapid/v1/wallet-label": ("/v1/wallet-label", "wallet-label"),
         "/rapid/v1/wallet-label/bulk": ("/v1/wallet-label/bulk", "wallet-label-bulk"),
         "/rapid/v1/company": ("/v1/company", "company")}


def register(app: FastAPI, *, paid: Callable, rate_limit: Callable, lookups: Dict[str, Any], free_endpoints: Dict[str, str]) -> None:
    lookups.update(LOOKUPS_LB)
    free_endpoints.update(FREE_LB)
    try:
        from . import rapid as _r
        _r.ROUTES.update(RAPID)
    except Exception:
        pass
    try:
        _site_cache.update(json.load(open(SITE_FILE)))
    except Exception:
        pass
    def _rebuild_loop():
        # Initial build, then refresh remote caches on schedule (TTL + rebuild).
        while True:
            try:
                build()
                log.info("wallet-label corpus rebuilt: %s addresses as_of=%s", len(_ix.get("idx") or {}), _ix.get("as_of"))
            except Exception as e:
                log.warning("wallet-label rebuild failed: %s", e)
            time.sleep(max(3600, REBUILD_EVERY_SEC))

    threading.Thread(target=_rebuild_loop, daemon=True, name="labels-rebuild").start()

    def nocharge(e: Exception, code=400):
        return HTTPException(code, detail=f"{e}; payment NOT settled (no charge).")

    @app.get("/v1/wallet-label", tags=["labels"], summary="Wallet-to-entity label: exchange, exploit, sanctioned, bridge... ($0.002)")
    async def wl(request: Request, address: Optional[str] = Query(None)):
        async def live():
            if not address:
                raise HTTPException(400, detail="address is required; payment NOT settled (no charge).")
            try:
                return await run_in_threadpool(lookup, address)
            except ValueError as e:
                raise nocharge(e)
        return await paid(request, "wallet-label", live)

    @app.get("/v1/wallet-label/bulk", tags=["labels"], summary="Bulk wallet labels, up to 50 addresses ($0.05)")
    async def wlb(request: Request, addresses: Optional[str] = Query(None)):
        async def live():
            al = [a.strip() for a in (addresses or "").split(",") if a.strip()]
            if not al or len(al) > 50:
                raise HTTPException(400, detail="addresses: 1-50 comma-separated; payment NOT settled (no charge).")
            res = []
            for a in al:
                try:
                    res.append(await run_in_threadpool(lookup, a))
                except ValueError as e:
                    res.append({"address": a, "error": str(e)})
            return {"count": len(res), "matched": sum(1 for r in res if r.get("match")), "results": res, "as_of": _now(), "block": NA, "data_as_of": DAO(_now())}
        return await paid(request, "wallet-label-bulk", live)

    @app.get("/v1/company", tags=["labels"], summary="Crypto company lookup: website, X, GitHub, docs, team page, business email ($0.003)")
    async def co(request: Request, q: Optional[str] = Query(None)):
        async def live():
            if not q:
                raise HTTPException(400, detail="q is required; payment NOT settled (no charge).")
            r = await run_in_threadpool(company, q)
            if not r:
                raise HTTPException(404, detail=f"No project matches '{q}' (no charge).")
            return r
        return await paid(request, "company", live)

    @app.get("/v1/free/wallet-label", tags=["free"], summary="Free: entity + category for one address")
    async def wl_free(request: Request, address: Optional[str] = Query(None)):
        rate_limit(request)
        if not address:
            raise HTTPException(400, detail={
                "error": "address is required",
                "example": "/v1/free/wallet-label?address=0x28c6c06298d514db089934071355e5743bf21d60",
                "example_query": {"address": "0x28c6c06298d514db089934071355e5743bf21d60"},
            })
        try:
            r = await run_in_threadpool(lookup, address)
        except ValueError as e:
            raise HTTPException(400, detail=str(e))
        base = str(request.base_url).rstrip("/").replace("http://", "https://")
        out = {"address": r["address"], "match": r["match"], "entity": r["entity"], "category": r["category"],
                "as_of": r.get("corpus_as_of") or r["as_of"], "corpus_as_of": r.get("corpus_as_of") or _ix["as_of"],
                "labeled_addresses": r.get("labeled_addresses") or len(_ix["idx"]),
                "block": NA, "data_as_of": DAO(r.get("corpus_as_of") or r["as_of"]), "free": True,
                "upgrade": {"note": "Paid: label, chains, confidence, source link, every matching label; bulk 50 per call",
                            "endpoint": base + "/v1/wallet-label?address=" + r["address"], "price_usd": P_LABEL,
                            "bulk_endpoint": base + "/v1/wallet-label/bulk", "bulk_price_usd": P_BULK}}
        for k in ("status", "valid_until", "superseded_by", "tombstone_id", "tombstones_available", "tombstones_route", "prior_tombstone_id"):
            if k in r:
                out[k] = r[k]
        return out

    @app.get("/v1/free/company", tags=["free"], summary="Free: company/project name, website, X handle, category")
    async def co_free(request: Request, q: Optional[str] = Query(None)):
        rate_limit(request)
        if not q:
            raise HTTPException(400, detail={
                "error": "q is required (project name, ticker or domain)",
                "example": "/v1/free/company?q=aave",
                "example_query": {"q": "aave"},
            })
        r = await run_in_threadpool(company, q, False)
        if not r:
            raise HTTPException(404, detail=f"No project matches '{q}'.")
        base = str(request.base_url).rstrip("/").replace("http://", "https://")
        return {"name": r.get("name"), "website": r.get("website"), "x_handle": r.get("x_handle") or r.get("twitter"),
                "category": r.get("category"), "as_of": r.get("as_of"), "block": NA, "data_as_of": DAO(r.get("as_of")), "free": True,
                "upgrade": {"note": "Paid: GitHub, docs, team page, contact emails, chains, TVL, token, sources",
                            "endpoint": base + "/v1/company?q=" + q, "price_usd": 0.003}}

    @app.get("/v1/free/labels/coverage", tags=["free"], include_in_schema=False)
    async def wl_cov(request: Request):
        rate_limit(request)
        return await run_in_threadpool(coverage)
