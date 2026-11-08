"""
Binance outflows after the withdrawal restart (upd38, 2026-10-31).
Tracks net flows of Binance's publicly identified wallets on Ethereum, BSC, Base, Tron and Bitcoin, read by us from public
chain data. Wallet identification ONLY from: Binance's published wallet list ("Our Commitment To Transparency", 2022-11-10,
BTC/ETH/BEP20/TRX), Binance's official Proof-of-Reserves address list, and Etherscan-style public name tags from the
MIT-licensed dawsbot/eth-labels dataset (identification only). No Arkham/Nansen/Chainalysis/GoPlus/Blockscout/CoinGecko data.
USD = amount x Chainlink on-chain price (inputs listed with their own timestamp + block). Stablecoins via Chainlink feeds.
"""
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, Request

log = logging.getLogger("scrapling.binance_flows")
TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
LISTS = json.loads(r"""{"BTC": ["34xp4vRoCGJym3xR7yCVPFHoCNxv4Twseo", "3LYJfcfHPXYJreMsASk2jkn69LWEYKzexb", "3M219KR5vEneNb47ewrPfWyb5jQ2DjxRP6", "bc1qm34lsc65zpw79lxes69zkqmk6ee3ewf0j77s3h"], "ETH": ["0x21a31ee1afc51d94c2efccaa2092ad1028285549", "0x28c6c06298d514db089934071355e5743bf21d60", "0x47ac0fb4f2d84898e4d9e7b4dab3c24507a6d503", "0x4976a4a02f38326660d17bf34b431dc6e2eb2327", "0x56eddb7aa87536c09ccc2793473599fd21a8b17f", "0x5a52e96bacdabb82fd05763e25335261b270efcb", "0x9696f59e4d72e237be84ffd425dcad154bf96976", "0xa344c7ada83113b3b56941f6e85bf2eb425949f3", "0xbe0eb53f46cd790cd13851d5eff43d12404d33e8", "0xdfd5293d8e347dfe59e90efd55b2956a1343963d", "0xf977814e90da44bfa03b6295a0616a897441acec"], "TRX": ["TAzsQ9Gx8eqFNFSKbeXrbi45CuVPHzA8wr", "TJDENsfBJs4RFETt1X1W8wMDc8M5XnJhCe", "TMuA6YqfCeX8EhbfYEg5y7S4DqzSJireY9", "TNXoiAJ3dct8Fjg4M9fkLFh9S2v9TXc32G", "TQrY8tryqsYVCYS3MFbtffiPp2ccyn4STm", "TV6MuMXfmLbBqPZvBHdwFsDnQeVfnmiuSi", "TWd4WrZ9wn84f5x1hZhL4DHvk738ns5jwb", "TYASr5UV6HEcXatwdFQfmLVUqQQQMUxHLS"], "BEP20": ["0x01c952174c24e1210d26961d456a77a39e1f0bb0", "0x161ba15a5f335c9f06bb5bbb0a9ce14076fbb645", "0x1fbe2acee135d991592f167ac371f3dd893a508b", "0x29bdfbf7d27462a2d115748ace2bd71a2646946c", "0x3c783c21a0383057d128bae431894a5c19f9cf06", "0x515b72ed8a97f42c568d6a143232775018f133c8", "0x5a52e96bacdabb82fd05763e25335261b270efcb", "0x73f5ebe90f27b46ea12e5795d16c4b408b19cc6f", "0x8894e0a0c962cb723c1976a4421c95949be2d4e3", "0xa180fe01b906a1be37be6c534a3300785b20d947", "0xbd612a3f30dca67bf60a39fd0d35e39b7ab80774", "0xbe0eb53f46cd790cd13851d5eff43d12404d33e8", "0xdccf3b77da55107280bd850ea519df3705d1a75a", "0xe2fc31f816a9b94326492132018c3aecc4a93ae1", "0xeb2d2f1b8c558a40207669291fda468e50c8a0bb", "0xf977814e90da44bfa03b6295a0616a897441acec"]}
""")
SRC_2022 = ("Binance official wallet list, 'Our Commitment To Transparency' (2022-11-10)",
            "https://www.binance.com/en/blog/community/our-commitment-to-transparency-2895840147147652626")
LABEL_FILE = "/collected_data/manual/exchange_wallets.json"
CACHE_DIR = "/collected_data/cache" if os.path.isdir("/collected_data") else "/tmp"
SNAP_FILE = os.path.join(CACHE_DIR, "binance_flows_native.json")
PRICE_SNAP, PRICE_REPORT = 0.02, 0.25
EVM = {  # chain -> (rpc id, seconds per block (estimate for search only), logs chunk, tokens{addr: (symbol, decimals, price_sym)}, native)
    "ethereum": ("1", 12, 800, {"0xdac17f958d2ee523a2206206994597c13d831ec7": ("USDT", 6, "USDT"),
                               "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": ("USDC", 6, "USDC"),
                               "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": ("WBTC", 8, "BTC"),
                               "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": ("WETH", 18, "ETH")}, "ETH"),
    "bsc": ("56", 0.75, 200, {"0x55d398326f99059ff775485246999027b3197955": ("USDT", 18, "USDT"),
                               "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": ("USDC", 18, "USDC"),
                               "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c": ("BTCB", 18, "BTC"),
                               "0x2170ed0880ac9a755fd29b2688956bd959f933f8": ("ETH", 18, "ETH")}, "BNB"),
    "base": ("8453", 2, 3000, {"0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": ("USDC", 6, "USDC")}, "ETH"),
}
TRON_USDT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
CHAINS = ("ethereum", "bsc", "base", "tron", "bitcoin")
_lk = threading.Lock()
S: Dict[str, Any] = {"wallets": {}, "labels": {}, "events": {c: [] for c in CHAINS}, "cursor": {}, "head": {}, "status": {},
                     "native": [], "hours": {}, "btime": {}, "prices": {}, "inputs": [], "started": int(time.time())}


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(t))) if t else None


BSC_RPCS = ["https://bsc.publicnode.com", "https://bsc-rpc.publicnode.com", "https://bsc.drpc.org", "https://1rpc.io/bnb", "https://binance.llamarpc.com",
            "https://bsc.meowrpc.com", "https://bsc-mainnet.public.blastapi.io", "https://bsc.blockpi.network/v1/rpc/public",
            "https://bsc-dataseed.bnbchain.org", "https://bsc-dataseed1.defibit.io"]
_cool: Dict[str, float] = {}
_fails: Dict[str, int] = {}


def _bsc_rpc(method: str, params: list) -> Any:
    """Rotate free BSC RPCs; an endpoint that errors or rate-limits is cooled down with exponential backoff."""
    import urllib.request
    now = time.time()
    order = sorted(BSC_RPCS, key=lambda u: (_cool.get(u, 0) > now, _fails.get(u, 0)))
    last = None
    for u in order:
        if _cool.get(u, 0) > now and last is not None:
            continue
        try:
            req = urllib.request.Request(u, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                                         headers={"Content-Type": "application/json", "User-Agent": "scraplingdata/1"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                d = json.loads(resp.read())
            if "error" in d:
                raise RuntimeError(str(d["error"])[:120])
            _fails[u] = 0
            return d["result"]
        except Exception as e:
            last = e
            _fails[u] = _fails.get(u, 0) + 1
            _cool[u] = time.time() + min(600, 5 * 2 ** min(_fails[u], 7))
    S["bsc_rpc_note"] = f"all tried BSC RPCs failed: {type(last).__name__}"
    raise RuntimeError(f"bsc rpc {method} failed on all endpoints: {type(last).__name__}")


def _rpc(chain: str, method: str, params: list) -> Any:
    if chain == "bsc":
        return _bsc_rpc(method, params)
    from . import onchain_risk as _o
    return _o._rpc(EVM[chain][0], method, params)


# ---------------- wallets + labels ----------------
def _load_wallets() -> None:
    w: Dict[str, Dict[str, Dict[str, str]]] = {c: {} for c in CHAINS}
    lab: Dict[str, Dict[str, str]] = {}
    def add(chain, a, src, url, kind):
        a = a.lower() if a.startswith("0x") else a
        w[chain].setdefault(a, {"address": a, "kind": kind, "source": src, "source_url": url})
    for a in LISTS.get("ETH", {}):
        add("ethereum", a, *SRC_2022, "hot_or_cold")
    for a in LISTS.get("BEP20", {}):
        add("bsc", a, *SRC_2022, "hot_or_cold")
    for a in LISTS.get("TRX", {}):
        add("tron", a, *SRC_2022, "hot_or_cold")
    for a in LISTS.get("BTC", {}):
        add("bitcoin", a, *SRC_2022, "hot_or_cold")
    try:
        d = json.load(open(LABEL_FILE, encoding="utf-8"))
        for r in d.get("labels", []):
            ch, a = r.get("chain"), (r.get("address") or "").lower()
            if ch not in ("ethereum", "base") or not a:
                continue
            if r.get("exchange") == "Binance" and r.get("kind") in ("proof_of_reserves", "hot_or_cold"):
                add(ch, a, r.get("source"), r.get("source_url") or "", r.get("kind"))
            else:
                lab[f"{ch}:{a}"] = {"label": f"{r.get('exchange')} ({r.get('kind')})", "kind": "cex" if r.get("exchange") != "Binance" else "binance_deposit",
                                    "source": r.get("source")}
    except Exception as e:
        log.warning("binance_flows labels: %s", e)
    try:
        from . import hack_tracker as _ht
        for a, v in _ht.LABELS.items():
            lab.setdefault(f"ethereum:{a.lower()}", {"label": v["label"], "kind": v["kind"], "source": v.get("source")})
    except Exception:
        pass
    S["wallets"], S["labels"] = w, lab


def _dest(chain: str, a: str) -> Dict[str, Any]:
    l = S["labels"].get(f"{chain}:{a.lower() if a.startswith('0x') else a}")
    if not l:
        return {"type": "unlabeled", "label": None, "label_source": None}
    t = {"cex": "other_exchange", "bridge": "bridge", "mixer": "mixer", "dex": "dex", "binance_deposit": "binance_user_deposit_address"}.get(l["kind"], l["kind"])
    return {"type": t, "label": l["label"], "label_source": l["source"]}


# ---------------- prices ----------------
def _prices() -> None:
    from . import price_meta as _pm
    px, inp = {}, []
    for s in ("ETH", "BTC", "BNB", "TRX", "USDT", "USDC"):
        try:
            i = _pm.chainlink_input(s)
        except Exception:
            i = None
        if i and i.get("price"):
            px[s] = i["price"]
            inp.append(i)
    S["prices"], S["inputs"] = px, inp


def _usd(sym: str, amt: float) -> Optional[float]:
    p = S["prices"].get(sym)
    return round(amt * p, 2) if p is not None else None


# ---------------- EVM ----------------
def _btime(chain: str, b: int) -> int:
    k = (chain, b)
    if k not in S["btime"]:
        S["btime"][k] = int(_rpc(chain, "eth_getBlockByNumber", [hex(b), False])["timestamp"], 16)
        if len(S["btime"]) > 20000:
            for kk in list(S["btime"])[:10000]:
                S["btime"].pop(kk, None)
    return S["btime"][k]


def _block_at(chain: str, ts: int) -> int:
    """first block with timestamp >= ts (read from chain; estimate only picks where to look)."""
    hb, ht = S["head"][chain]
    if ts >= ht:
        return hb
    spb = EVM[chain][1]
    lo, hi = None, None
    b = max(1, hb - int((ht - ts) / spb))
    for _ in range(40):
        t = _btime(chain, b)
        if t >= ts:
            hi = b
        else:
            lo = b
        if lo is not None and hi is not None and hi - lo <= 1:
            return hi
        if lo is None:
            b = max(1, b - max(1, int((t - ts) / spb) + 5))
        elif hi is None:
            b = min(hb, b + max(1, int((ts - t) / spb) + 1))
        else:
            b = (lo + hi) // 2
    return hi or b


def _topic(a: str) -> str:
    return "0x" + a[2:].rjust(64, "0")


def _evm_refresh(chain: str) -> None:
    cid, spb, chunk, toks, native = EVM[chain]
    head = int(_rpc(chain, "eth_blockNumber", []), 16)
    S["head"][chain] = (head, _btime(chain, head))
    W = S["wallets"][chain]
    if not W:
        S["status"][chain] = "no publicly identified wallets"
        return
    start = S["cursor"].get(chain)
    if start is None:
        # upd13b: free BSC RPCs answer only ~50-250 blocks per getLogs at 6-16s each, so a 25h (~120k block) backfill cannot finish;
        # BSC scans from 70 min back so the 1h window is fully covered, and its 24h total stays null (not_checked) rather than understated.
        start = _block_at(chain, S["head"][chain][1] - (70 * 60 if chain == "bsc" else 25 * 3600))
        S["coverage_from"] = S.get("coverage_from") or {}
        S["coverage_from"][chain] = start
    wt = [_topic(a) for a in W]
    a = start
    t0 = time.time()
    size = chunk
    while a <= head and time.time() - t0 < 50:
        b = min(head, a + size - 1)
        try:
            out = []
            for pos in (1, 2):
                tp: List[Any] = [TOPIC, None, None]
                tp[pos] = wt
                out += _rpc(chain, "eth_getLogs", [{"address": list(toks), "fromBlock": hex(a), "toBlock": hex(b), "topics": tp}]) or []
        except Exception as e:
            if size > 20:
                size //= 2
                time.sleep(1)
                continue
            S["status"][chain] = f"error: eth_getLogs {type(e).__name__}"
            S.setdefault("last_error", {})[chain] = f"eth_getLogs {type(e).__name__}: {str(e)[:140]}"
            return
        seen = set()
        new = []
        for l in out:
            k = (l["transactionHash"], l["logIndex"])
            if k in seen:
                continue
            seen.add(k)
            f, t = "0x" + l["topics"][1][-40:], "0x" + l["topics"][2][-40:]
            fi, ti = f in W, t in W
            if fi == ti:
                continue  # internal Binance move (or neither)
            sym, dec, psym = toks[l["address"].lower()]
            amt = int(l["data"], 16) / 10 ** dec if l.get("data") not in (None, "0x") else 0.0
            if amt <= 0:
                continue
            new.append({"tx": l["transactionHash"], "block": int(l["blockNumber"], 16), "asset": sym, "psym": psym,
                        "amount": amt, "dir": "out" if fi else "in", "wallet": f if fi else t, "cp": t if fi else f})
        with _lk:
            S["events"][chain].extend(new)
        a = b + 1
        S["cursor"][chain] = a
        size = min(chunk, size * 2)
        time.sleep(0.15)
    S["status"][chain] = "ok" if S["cursor"].get(chain, 0) > head else f"backfilling (at block {S['cursor'].get(chain)} of {head})"
    cut = _block_at(chain, S["head"][chain][1] - 25 * 3600)
    with _lk:
        S["events"][chain] = [e for e in S["events"][chain] if e["block"] >= cut]


# ---------------- Tron (USDT TRC20) ----------------
def _tron_refresh() -> None:
    from . import hack_multichain as _h
    nb = _h._tnode("/wallet/getnowblock", {})
    hd = nb["block_header"]["raw_data"]
    S["head"]["tron"] = (int(hd["number"]), int(hd["timestamp"]) // 1000)
    W = S["wallets"]["tron"]
    since = S["cursor"].get("tron") or (int(time.time()) - 25 * 3600) * 1000
    newest = since
    new = []
    for w in W:
        fp, pages = None, 0
        while pages < 15:
            q = f"/v1/accounts/{w}/transactions/trc20?limit=200&only_confirmed=true&contract_address={TRON_USDT}&min_timestamp={since + 1}"
            if fp:
                q += "&fingerprint=" + fp
            for _try in range(4):  # fix45: back off on TronGrid 429
                try:
                    d = _h._tg(q)
                    break
                except Exception:
                    if _try == 3:
                        raise
                    time.sleep(3 * (_try + 1))
            time.sleep(1.0)
            for r in d.get("data") or []:
                f, t = r.get("from"), r.get("to")
                if (f in W) == (t in W):
                    continue
                amt = int(r.get("value") or 0) / 10 ** int((r.get("token_info") or {}).get("decimals") or 6)
                ts = int(r["block_timestamp"]) // 1000
                newest = max(newest, int(r["block_timestamp"]))
                new.append({"tx": r["transaction_id"], "block": None, "time": ts, "asset": "USDT", "psym": "USDT", "amount": amt,
                            "dir": "out" if f in W else "in", "wallet": f if f in W else t, "cp": t if f in W else f})
            fp = (d.get("meta") or {}).get("fingerprint")
            pages += 1
            if not fp:
                break
            time.sleep(0.3)
        time.sleep(0.3)
    with _lk:
        have = {(e["tx"], e["dir"], e["wallet"]) for e in S["events"]["tron"]}
        S["events"]["tron"].extend(e for e in new if (e["tx"], e["dir"], e["wallet"]) not in have)
        S["events"]["tron"] = [e for e in S["events"]["tron"] if e["time"] >= time.time() - 25 * 3600]
    S["cursor"]["tron"] = newest
    S["status"]["tron"] = "ok"


def _tron_block(tx: str) -> Optional[int]:
    from . import hack_multichain as _h
    try:
        return int(_h._tnode("/wallet/gettransactioninfobyid", {"value": tx}).get("blockNumber"))
    except Exception:
        return None


# ---------------- Bitcoin ----------------
def _btc_refresh() -> None:
    from . import hack_multichain as _h
    tip = int(_h._btc("/blocks/tip/height"))
    S["head"]["bitcoin"] = (tip, int(time.time()))
    try:
        S["head"]["bitcoin"] = (tip, int(_h._btc(f"/block/{_h._btc('/blocks/tip/hash')}")["timestamp"]))
    except Exception:
        pass
    W = S["wallets"]["bitcoin"]
    cut = int(time.time()) - 25 * 3600
    evs = []
    for w in W:
        txs, last = [], None
        for _ in range(8 if not S['events']['bitcoin'] and not S.get('btc_init') else 1):
            page = _h._btc(f"/address/{w}/txs" + (f"/chain/{last}" if last else ""))
            if not page:
                break
            txs += [t for t in page if (t.get("status") or {}).get("confirmed")]
            last = page[-1]["txid"]
            if (page[-1].get("status") or {}).get("block_time", 0) < cut or len(page) < 25:
                break
        for t in txs:
            st = t["status"]
            if st.get("block_time", 0) < cut:
                continue
            ins = [v.get("prevout") or {} for v in t.get("vin", [])]
            from_us = any(p.get("scriptpubkey_address") in W for p in ins)
            outs = t.get("vout", [])
            if from_us:
                ext = [o for o in outs if o.get("scriptpubkey_address") not in W]
                amt = sum(o.get("value", 0) for o in ext) / 1e8
                if amt <= 0:
                    continue
                big = max(ext, key=lambda o: o.get("value", 0))
                evs.append({"tx": t["txid"], "block": st["block_height"], "time": st["block_time"], "asset": "BTC", "psym": "BTC",
                            "amount": amt, "dir": "out", "wallet": w, "cp": big.get("scriptpubkey_address"), "outputs_external": len(ext)})
            else:
                amt = sum(o.get("value", 0) for o in outs if o.get("scriptpubkey_address") == w) / 1e8
                if amt > 0:
                    evs.append({"tx": t["txid"], "block": st["block_height"], "time": st["block_time"], "asset": "BTC", "psym": "BTC",
                                "amount": amt, "dir": "in", "wallet": w, "cp": (ins[0] or {}).get("scriptpubkey_address") if ins else None})
        time.sleep(0.5)
    seen, uniq = set(), []
    for e in evs:
        if (e["tx"], e["dir"]) not in seen:
            seen.add((e["tx"], e["dir"]))
            uniq.append(e)
    with _lk:
        keep = [e for e in S["events"]["bitcoin"] if (e["tx"], e["dir"]) not in seen and e["time"] >= cut]
        S["events"]["bitcoin"] = keep + uniq
    S["btc_init"] = True
    S["status"]["bitcoin"] = "ok"


# ---------------- native balance snapshots (ETH / BNB / Base ETH / TRX) ----------------
def _native_snapshot() -> None:
    snap = {"t": int(time.time()), "chains": {}}
    for chain in ("ethereum", "bsc", "base"):
        W = list(S["wallets"][chain])
        try:
            hb = S["head"][chain][0]
            with ThreadPoolExecutor(6) as ex:
                bals = list(ex.map(lambda a: int(_rpc(chain, "eth_getBalance", [a, hex(hb)]), 16) / 1e18, W))
            snap["chains"][chain] = {"asset": EVM[chain][4], "amount": sum(bals), "block": hb, "time": S["head"][chain][1],
                                     "per_wallet": {a.lower(): v for a, v in zip(W, bals)}}  # fix47
        except Exception as e:
            log.info("native %s: %s", chain, e)
    try:
        from . import hack_multichain as _h
        tot = 0.0
        for a in S["wallets"]["tron"]:
            for _try in range(4):  # fix44: back off on TronGrid 429
                try:
                    d = _h._tg(f"/v1/accounts/{a}")
                    break
                except Exception:
                    if _try == 3:
                        raise
                    time.sleep(3 * (_try + 1))
            tot += ((d.get("data") or [{}])[0].get("balance") or 0) / 1e6
            time.sleep(1.0)
        snap["chains"]["tron"] = {"asset": "TRX", "amount": tot, "block": S["head"].get("tron", (None,))[0], "time": S["head"].get("tron", (0, 0))[1]}
    except Exception as e:
        log.info("native tron: %s", e)
    with _lk:
        S["native"].append(snap)
        S["native"] = [s for s in S["native"] if s["t"] >= time.time() - 26 * 3600]
    try:
        json.dump(S["native"], open(SNAP_FILE, "w"))
    except Exception:
        pass


def _native_delta(chain: str, secs: int) -> Dict[str, Any]:
    sn = [s for s in S["native"] if chain in s["chains"]]
    if not sn:
        return {"net_amount": None, "net_usd": None, "reason": "no balance snapshot yet"}
    now = sn[-1]
    target = now["t"] - secs
    old = [s for s in sn if abs(s["t"] - target) <= 900]
    if not old:
        first = _iso(sn[0]["t"])
        return {"net_amount": None, "net_usd": None, "reason": f"native balances snapshotted since {first}; need history covering this window "
                "(public RPCs do not serve historical balances)"}
    o = min(old, key=lambda s: abs(s["t"] - target))
    a, b = o["chains"][chain], now["chains"][chain]
    pa, pb = a.get("per_wallet"), b.get("per_wallet")
    if chain != "tron" and (pa is None or pb is None):  # fix47: summed totals across differing wallet lists are not comparable
        return {"net_amount": None, "net_usd": None, "reason": "older balance snapshot has no per-wallet breakdown; the tracked wallet list "
                "changed between snapshots, so summed balances are not comparable. Withheld until per-wallet snapshots cover the window",
                "from": {"time": _iso(a["time"]), "block": a["block"]}, "to": {"time": _iso(b["time"]), "block": b["block"]}}
    if pa is not None and pb is not None:
        common = set(pa) & set(pb)
        d = sum(pb[k] for k in common) - sum(pa[k] for k in common)
    else:
        d = b["amount"] - a["amount"]  # balance change: negative = net outflow
    psym = {"ETH": "ETH", "BNB": "BNB", "TRX": "TRX"}[b["asset"]]
    return {"asset": b["asset"], "net_amount": round(-d, 6), "net_usd": _usd(psym, -d), "sign": "positive = net outflow",
            "from": {"time": _iso(a["time"]), "block": a["block"]}, "to": {"time": _iso(b["time"]), "block": b["block"]},
            "method": "change in summed native balances of the identified wallets (includes gas spent)"}


# ---------------- aggregation ----------------
def _bounds(chain: str, secs: int) -> Tuple[Any, Any]:
    hb, ht = S["head"].get(chain, (None, None))
    if hb is None:
        return None, None
    if chain in EVM:
        b = _block_at(chain, ht - secs)
        return b, {"time": _iso(_btime(chain, b)), "block": b}
    return ht - secs, {"time": _iso(ht - secs), "block": None}


def _evkey(chain: str, e: Dict[str, Any]) -> int:
    return e["block"] if chain in EVM else e["time"]


def _agg(chain: str, secs: int) -> Dict[str, Any]:
    lo, start = _bounds(chain, secs)
    if lo is None:
        return {"status": S["status"].get(chain, "warming_up")}
    cov = (S.get("coverage_from") or {}).get(chain)
    st = S["status"].get(chain)
    complete = (chain not in EVM and st == "ok") or (cov is not None and cov <= lo and st == "ok")
    per: Dict[str, Dict[str, float]] = {}
    with _lk:
        evs = [e for e in S["events"][chain] if _evkey(chain, e) >= lo]
    for e in evs:
        p = per.setdefault(e["asset"], {"out": 0.0, "in": 0.0, "out_n": 0, "in_n": 0, "psym": e["psym"]})
        p[e["dir"]] += e["amount"]
        p[e["dir"] + "_n"] += 1
    assets = {}
    for s, p in per.items():
        net = p["out"] - p["in"]
        assets[s] = {"outflow": round(p["out"], 6), "inflow": round(p["in"], 6), "net_outflow": round(net, 6),
                     "outflow_usd": _usd(p["psym"], p["out"]), "inflow_usd": _usd(p["psym"], p["in"]), "net_outflow_usd": _usd(p["psym"], net),
                     "withdrawals": p["out_n"], "deposits": p["in_n"], "price_symbol": p["psym"]}
    tot = lambda k: round(sum((a[k] or 0) for a in assets.values()), 2) if complete else None
    hb, ht = S["head"][chain]
    nat = _native_delta(chain, secs) if chain != "bitcoin" else None
    return {"window_start": start, "window_end": {"time": _iso(ht), "block": hb},
            "token_net_outflow_usd": tot("net_outflow_usd"), "token_outflow_usd": tot("outflow_usd"), "token_inflow_usd": tot("inflow_usd"),
            "by_asset": assets, "native": nat,
            "net_outflow_usd_incl_native": round(tot("net_outflow_usd") + ((nat or {}).get("net_usd") or 0), 2) if complete else None,
            "note": None if complete else "window not yet fully scanned since tracker start; totals withheld (null) rather than understated",
            "window_fully_covered": complete, "status": S["status"].get(chain, "warming_up")}


def _hourly(chain: str) -> List[Dict[str, Any]]:
    hb, ht = S["head"].get(chain, (None, None))
    if hb is None:
        return []
    h0 = ht - ht % 3600
    edges = [h0 - 3600 * i for i in range(24, -1, -1)]
    keys = []
    for t in edges:
        if chain in EVM:
            k = (chain, t)
            if k not in S["hours"]:
                S["hours"][k] = _block_at(chain, t)
            keys.append(S["hours"][k])
        else:
            keys.append(t)
    keys.append(float("inf"))
    with _lk:
        evs = list(S["events"][chain])
    out = []
    for i in range(len(edges)):
        lo, hi = keys[i], keys[i + 1]
        sel = [e for e in evs if lo <= _evkey(chain, e) < hi]
        net = sum((_usd(e["psym"], e["amount"]) or 0) * (1 if e["dir"] == "out" else -1) for e in sel)
        out.append({"hour_start_utc": _iso(edges[i]), "start_block": keys[i] if chain in EVM else None,
                    "token_net_outflow_usd": round(net, 2), "transfers": len(sel), "partial_hour": i == len(edges) - 1})
    return out



# upd14: measured loop cycles (start-to-start), used for spread limits
_CYC14 = {}
def _cyc14(name, t0=None):
    import time as _t
    t0 = t0 or _t.time()
    d = _CYC14.setdefault(name, {})
    if d.get("last_start"):
        d.setdefault("hist", []).append(round(t0 - d["last_start"], 1))
        d["hist"] = d["hist"][-10:]
    d["last_start"] = t0
def _ivs14(names, sched):
    out = {}
    for n in names:
        h = (_CYC14.get(n) or {}).get("hist") or []
        out[n] = int(max(h)) if h else int(sched[n])
    return out
def _sc14(pm, sp, names, sched):
    iv = _ivs14(names, sched)
    x = pm.spread_check(sp, list(iv.values()) or [max(sched.values())],
                        "measured start-to-start loop cycle, max of last 10, slowest loop feeding this figure")
    x["measured_loop_cycles_seconds"] = {n: {"limit_basis_s": iv[n], "recent": ((_CYC14.get(n) or {}).get("hist") or [])[-5:],
                                             "measured": bool((_CYC14.get(n) or {}).get("hist"))} for n in iv}
    return x
_SCHED14 = {'evm': 60, 'slow': 120}
_LOOP14 = {'ethereum': 'evm', 'bsc': 'evm', 'base': 'evm', 'tron': 'slow', 'bitcoin': 'slow'}
def _ln14(cs):
    return sorted({_LOOP14.get(c, 'evm') for c in cs}) or ['evm', 'slow']


DOC14 = ("Per chain: check_1h / check_24h with state (checked_ok = scanned, transfers found; checked_ok_empty = fully scanned, a real zero; "
         "not_checked = window not fully scanned, total null; error = latest refresh failed), last_success_at, last_error_at, last_error. "
         "all_chains carries spread_seconds, max_spread_seconds and spread_ok (limit = slowest feeding loop's measured cycle + grace, see "
         "measured_loop_cycles_seconds), plus figure_spreads for 1h and 24h. BSC covers the 1h window only (free BSC RPC limits); its 24h "
         "stays null/not_checked. When any chain's freshness.state is provider_lag (also listed in all_chains.provider_lag_chains), the "
         "response includes top-level action_hint=\"pause\" (and all_chains.action_hint=\"pause\"); the field is omitted when no chain "
         "is in provider_lag. window_state_24h is unchanged (incomplete windows, e.g. BSC 24h, stay partial independently of lag). "
         "partial (bool) and chains_partial (list) are always present at the top level and under all_chains and mirror window_state_24h; "
         "they are false and [] when that window is complete. action_hint is still omitted when no chain is in provider_lag.")


def visible_partial(snap: Dict[str, Any]) -> Dict[str, Any]:
    """Top-level partial flags for responses that rebuild a subset of the snapshot.

    Prefers the fields the snapshot wrapper published. Falls back to window_state_24h
    so an older cached snapshot still shows the same 24h aggregate. Does not add action_hint.
    """
    if isinstance(snap, dict) and "partial" in snap and "chains_partial" in snap:
        return {"partial": bool(snap.get("partial")), "chains_partial": list(snap.get("chains_partial") or [])}
    ac = (snap or {}).get("all_chains") or {}
    ws = ac.get("window_state_24h") if isinstance(ac.get("window_state_24h"), dict) else {}
    chains = list((ws or {}).get("chains_partial") or [])
    return {"partial": (ws or {}).get("state") == "partial", "chains_partial": chains}


def _snapshot() -> Dict[str, Any]:
    from . import price_meta as _pm10  # upd10
    ch = {}
    for c in CHAINS:
        ch[c] = {"1h": _agg(c, 3600), "24h": _agg(c, 86400), "wallets_tracked": len(S["wallets"].get(c) or {}),
                 "head": {"block": (S["head"].get(c) or (None,))[0], "time": _iso((S["head"].get(c) or (0, 0))[1]),
                          "basis": "chain head at last refresh" if c != "tron" else "Tron head block at last refresh"}}
        ch[c]["check_1h"], ch[c]["check_24h"] = _check13(c, ch[c]["1h"]), _check13(c, ch[c]["24h"])
    def s(w, k):
        return round(sum((v[w].get(k) or 0) for v in ch.values()), 2)
    def inc(w):
        return [c for c, v in ch.items() if v[w].get("window_fully_covered")]
    return {"as_of": _iso(S.get("updated_at")), "chains": ch,
            "all_chains": {"net_outflow_usd_1h": s("1h", "net_outflow_usd_incl_native"), "net_outflow_usd_24h": s("24h", "net_outflow_usd_incl_native"),
                           "token_net_outflow_usd_1h": s("1h", "token_net_outflow_usd"), "token_net_outflow_usd_24h": s("24h", "token_net_outflow_usd"),
                           "chains_included_1h": inc("1h"), "chains_included_24h": inc("24h"),
                           **_sc14(_pm10, _pm10.as_of_spread(
                               [_seen13(c, v) for c, v in ch.items() if c in set(inc("1h")) | set(inc("24h"))],
                               "time each chain was last successfully checked (tip re-read then), chains included in either window"),
                               _ln14(set(inc("1h")) | set(inc("24h"))), _SCHED14),
                           "figure_spreads": {"1h": dict(_sc14(_pm10, _pm10.as_of_spread([_seen13(c, v) for c, v in ch.items() if c in inc("1h")],
                                                         "last successful check time, chains in the 1h total"), _ln14(inc("1h")), _SCHED14), chains=inc("1h")),
                                              "24h": (lambda x: dict(x, max_spread_seconds=max(900, _sc14(_pm10, dict(x), _ln14(inc("24h")), _SCHED14)["max_spread_seconds"]), spread_ok=(x["spread_seconds"] <= max(900, _sc14(_pm10, dict(x), _ln14(inc("24h")), _SCHED14)["max_spread_seconds"])) if x.get("spread_seconds") is not None else None,
                                                                     spread_rule="spread_ok when spread_seconds <= max(900s, measured loop cycle + grace) (a few minutes of lag shifts a 24h window by <1%)", chains=inc("24h")))(
                                                  _pm10.as_of_spread([_seen13(c, v) for c, v in ch.items() if c in inc("24h")], "last successful check time, chains in the 24h total"))},
                           "chain_checks": {c: {"1h": v["check_1h"]["state"], "24h": v["check_24h"]["state"]} for c, v in ch.items()},
                           "note": "positive = more left Binance than arrived. Native coins (ETH/BNB/TRX) counted only where balance history exists."},
            "inputs": S["inputs"]}


def _top(n: int = 25) -> List[Dict[str, Any]]:
    rows = []
    with _lk:
        for c in CHAINS:
            lo, _ = _bounds(c, 86400)
            if lo is None:
                continue
            for e in S["events"][c]:
                if e["dir"] == "out" and _evkey(c, e) >= lo:
                    rows.append((c, e, _usd(e["psym"], e["amount"]) or 0))
    rows.sort(key=lambda r: -r[2])
    out = []
    for c, e, u in rows[:n]:
        blk = e.get("block")
        if c == "tron" and blk is None:
            blk = e["block"] = _tron_block(e["tx"])
        t = e.get("time") or (_btime(c, e["block"]) if c in EVM else None)
        out.append({"chain": c, "tx": e["tx"], "block": blk, "time": _iso(t), "asset": e["asset"], "amount": round(e["amount"], 6),
                    "usd": u, "from_binance_wallet": e["wallet"], "to": e["cp"], "destination": _dest(c, e["cp"] or "")})
    return out


def _dest_breakdown() -> Dict[str, Any]:
    agg: Dict[str, Dict[str, Any]] = {}
    with _lk:
        for c in CHAINS:
            lo, _ = _bounds(c, 86400)
            if lo is None:
                continue
            for e in S["events"][c]:
                if e["dir"] != "out" or _evkey(c, e) < lo:
                    continue
                d = _dest(c, e["cp"] or "")
                k = d["type"] if d["type"] == "unlabeled" else f"{d['type']}: {d['label']}"
                r = agg.setdefault(k, {"usd": 0.0, "transfers": 0, "chains": set()})
                r["usd"] += _usd(e["psym"], e["amount"]) or 0
                r["transfers"] += 1
                r["chains"].add(c)
    return dict(sorted(((k, {"usd": round(v["usd"], 2), "transfers": v["transfers"], "chains": sorted(v["chains"])}) for k, v in agg.items()),
                       key=lambda kv: -kv[1]["usd"]))


# ---------------- loop ----------------
def _run13(c: str, fn: Any) -> None:
    """upd13: one refresh with per-chain last_success_at / last_error_at and the error text (no secrets in these URLs)."""
    S.setdefault("last_ok", {}); S.setdefault("last_err", {}); S.setdefault("last_error", {})
    try:
        fn()
    except Exception as e:
        S["status"][c] = f"error: {type(e).__name__}"
        S["last_error"][c] = f"{type(e).__name__}: {str(e)[:140]}"
    if str(S["status"].get(c, "")).startswith("error"):
        S["last_err"][c] = int(time.time())
    elif S["status"].get(c) == "ok":
        S["last_ok"][c] = int(time.time())


def _seen13(c: str, v: Dict[str, Any]) -> Any:
    """upd13b: Bitcoin blocks are ~10 min apart, so tip *block* time can lag 20+ min even when the tip was just re-checked.
    Spread uses when each chain was last successfully checked; head.block/head.time still show the tip itself."""
    t = (S.get("last_ok") or {}).get(c)
    return _iso(t) if t else v["head"]["time"]


def _check13(c: str, w: Dict[str, Any]) -> Dict[str, Any]:
    st = str(S["status"].get(c) or "warming_up")
    if st.startswith("error"):
        state = "error"
    elif not w.get("window_fully_covered"):
        state = "not_checked"
    else:
        state = "checked_ok" if w.get("by_asset") else "checked_ok_empty"
    return {"state": state, "last_success_at": _iso(S.get("last_ok", {}).get(c)) if S.get("last_ok", {}).get(c) else None,
            "last_error_at": _iso(S.get("last_err", {}).get(c)) if S.get("last_err", {}).get(c) else None,
            "last_error": S.get("last_error", {}).get(c) if state == "error" else None,
            "meaning": {"checked_ok": "window scanned, transfers found", "checked_ok_empty": "window fully scanned, no transfers to/from tracked wallets (a real zero)",
                        "not_checked": "window not (fully) scanned yet; no total given", "error": "latest refresh failed; no total given"}[state]}


def _slow_loop() -> None:
    time.sleep(5)
    while True:
        t0 = time.time()
        _cyc14("slow", t0)
        for c, fn in (("tron", _tron_refresh), ("bitcoin", _btc_refresh)):
            _run13(c, fn)
        time.sleep(max(10, 120 - (time.time() - t0)))


def _loop() -> None:
    try:
        S["native"] = [s for s in json.load(open(SNAP_FILE)) if s["t"] >= time.time() - 26 * 3600]
    except Exception:
        pass
    _load_wallets()
    threading.Thread(target=_slow_loop, daemon=True, name="binance_flows_slow").start()
    last_native = 0
    while True:
        t0 = time.time()
        _cyc14("evm", t0)
        try:
            _prices()
        except Exception as e:
            log.info("prices: %s", e)
        for c in ("ethereum", "bsc", "base"):
            _run13(c, lambda c=c: _evm_refresh(c))
        if time.time() - last_native > 300:
            try:
                _native_snapshot()
                last_native = time.time()
            except Exception as e:
                log.info("native: %s", e)
        S["updated_at"] = int(time.time())
        try:
            S["cache"] = {"snapshot": _snapshot()}
        except Exception as e:
            log.warning("binance_flows snapshot: %s", e)
        time.sleep(max(5, 60 - (time.time() - t0)))


def _sources() -> Dict[str, Any]:
    return {"wallet_identification": [SRC_2022[0] + " " + SRC_2022[1], "Binance official Proof of Reserves address list (https://www.binance.com/en/proof-of-reserves)",
                                      "dawsbot/eth-labels (MIT) public explorer name tags, identification only"],
            "chain_data": "Ethereum/BSC/Base public RPC eth_getLogs + eth_getBalance; Tron full node + TronGrid TRC20 index; Bitcoin public Esplora APIs (mempool.space, blockstream.info, mempool.emzy.de, mempool.bitaroo.net)",
            "prices": "Chainlink on-chain feeds (see inputs: price_as_of + price_block per input)",
            "destination_labels": "other exchanges from exchanges' own PoR lists + MIT label dataset; bridges from protocol docs",
            "limits": ["Only publicly identified Binance wallets are tracked; Binance uses many more (true flows are larger).",
                       "Tron: USDT-TRC20 only. Ethereum: USDT, USDC, WBTC, WETH. BSC: USDT, USDC, BTCB, ETH. Base: USDC.",
                       "Native ETH/BNB/TRX flows from balance snapshots taken since the tracker started (no historical-balance RPC).",
                       "Bitcoin wallets are from Binance's 2022 list and are mostly reserve wallets."]}


LOOKUPS_BF = {
    "binance-flows": {"path": "/v1/lookup/binance-flows", "price": PRICE_SNAP, "example_path": "/v1/lookup/binance-flows",
                      "desc": "Binance withdrawal-restart flow snapshot: 1h and 24h net outflow per chain (Ethereum, BSC, Base, Tron, Bitcoin) and per asset "
                              "from Binance's publicly identified wallets, USD via Chainlink with input timestamps, as-of time and block per chain.",
                      "query": {}, "props": {}, "required": [],
                      "out": {"as_of": "2026-11-01T12:00:00Z", "action_hint": "pause",
                              "partial": True, "chains_partial": ["bsc"],
                              "all_chains": {"net_outflow_usd_1h": 0, "net_outflow_usd_24h": 0,
                                             "provider_lag_chains": ["ethereum"], "action_hint": "pause",
                                             "partial": True, "chains_partial": ["bsc"],
                                             "window_state_24h": {"state": "partial", "chains_partial": ["bsc"]}},
                              "chains": {"ethereum": {"1h": {"token_net_outflow_usd": 0, "by_asset": {"USDT": {"net_outflow": 0}}}}}}},
    "binance-flows-report": {"path": "/v1/lookup/binance-flows/report", "price": PRICE_REPORT, "example_path": "/v1/lookup/binance-flows/report?limit=25",
                             "desc": "Full Binance outflow report: everything in the snapshot plus the biggest single withdrawals (tx, block, time, USD), "
                                     "where they went (other exchanges, bridges, unlabeled), destination breakdown, hourly net-flow series for 24h, "
                                     "and the wallet list with public sources.",
                             "query": {"limit": 25}, "props": {"limit": {"type": "integer", "description": "biggest withdrawals to list (1-100, default 25)"}},
                             "required": [],
                             "out": {"biggest_withdrawals": [{"chain": "ethereum", "tx": "0x...", "block": 0, "time": "2026-11-01T12:00:00Z",
                                                              "asset": "USDT", "usd": 0, "destination": {"type": "other_exchange", "label": "OKX (proof_of_reserves)"}}],
                                     "destinations_24h": {"unlabeled": {"usd": 0}}, "hourly": {"ethereum": [{"hour_start_utc": "...", "token_net_outflow_usd": 0}]}}}}
FREE_BF = {"/v1/free/binance-flows": "Free Binance withdrawal-restart headline: all-chain and per-chain 1h/24h net outflow USD. "
           "Per-asset detail $0.02 (/v1/lookup/binance-flows); biggest withdrawals + destinations $0.25 (/v1/lookup/binance-flows/report)."}


def register(app: FastAPI, *, paid: Callable, rate_limit: Callable, lookups: Dict[str, Any], free_endpoints: Dict[str, str]) -> None:
    lookups.update(LOOKUPS_BF)
    free_endpoints.update(FREE_BF)
    threading.Thread(target=_loop, daemon=True, name="binance_flows").start()

    def _ready():
        if not (S.get("cache") or {}).get("snapshot"):
            raise HTTPException(503, detail="Binance flow tracker warming up (first refresh takes a few minutes); payment NOT settled (no charge).")
        return S["cache"]["snapshot"]

    @app.get("/v1/lookup/binance-flows", tags=["onchain"], summary="Binance outflow snapshot per chain/asset, 1h + 24h ($0.02)", description=DOC14)
    async def bf_snap(request: Request):
        async def live():
            return {**_ready(), "sources": _sources()}
        return await paid(request, "binance-flows", live)

    @app.get("/v1/lookup/binance-flows/report", tags=["onchain"], summary="Binance outflow report: biggest withdrawals, destinations, hourly ($0.25)")
    async def bf_report(request: Request, limit: int = 25):
        async def live():
            snap = _ready()
            from starlette.concurrency import run_in_threadpool
            n = max(1, min(int(limit), 100))
            top = await run_in_threadpool(_top, n)
            dest = await run_in_threadpool(_dest_breakdown)
            hourly = {}
            for c in CHAINS:
                try:
                    hourly[c] = await run_in_threadpool(_hourly, c)
                except Exception as e:
                    hourly[c] = {"error": type(e).__name__}
            return {**snap, "biggest_withdrawals_24h": top, "destinations_24h": dest, "hourly": hourly,
                    "wallets": {c: list((S["wallets"].get(c) or {}).values()) for c in CHAINS}, "sources": _sources()}
        return await paid(request, "binance-flows-report", live)

    @app.get("/v1/free/binance-flows", tags=["free"], summary="Free: Binance withdrawal-restart net-flow headline", description=DOC14)
    async def bf_free(request: Request):
        rate_limit(request)
        snap = (S.get("cache") or {}).get("snapshot")
        base = str(request.base_url).rstrip("/").replace("http://", "https://")
        if not snap:
            return {"status": "warming_up", "detail": "first refresh takes a few minutes", "free": True}
        out = {"as_of": snap["as_of"], "all_chains": snap["all_chains"],
               "per_chain": {c: {"net_outflow_usd_1h": v["1h"].get("net_outflow_usd_incl_native"),
                                 "net_outflow_usd_24h": v["24h"].get("net_outflow_usd_incl_native"),
                                 "window_fully_covered_24h": v["24h"].get("window_fully_covered"),
                                 "status": v["24h"].get("status"), "head": v["head"],
                                 "check_1h": v.get("check_1h"), "check_24h": v.get("check_24h"),
                                 "stalled": v.get("stalled"), "stall_state": v.get("stall_state"),
                                 "label_fingerprint_sha256": v.get("label_fingerprint_sha256")} for c, v in snap["chains"].items()},
               "label_list": snap.get("label_list"),
               "inputs": snap["inputs"], "free": True,
               "upgrade": {"snapshot": {"endpoint": base + "/v1/lookup/binance-flows", "price_usd": PRICE_SNAP, "adds": "per-asset in/out/net, native coin flows"},
                           "report": {"endpoint": base + "/v1/lookup/binance-flows/report", "price_usd": PRICE_REPORT,
                                      "adds": "biggest withdrawals with tx/block/time, destinations (other exchanges, bridges), hourly series, wallet sources"},
                           "payment": "x402 v2 USDC (Base, Polygon, Arbitrum, Solana)"},
               "sources": _sources()}
        out.update(visible_partial(snap))
        # Optional advisory: present only when snapshot flagged provider_lag (see bfx._snapshot).
        if snap.get("action_hint"):
            out["action_hint"] = snap["action_hint"]
        return out

# upd14b: /docs mention new fields
for _k in FREE_BF:
    FREE_BF[_k] = FREE_BF[_k].rstrip(". ") + ". " + DOC14.rstrip(".")
for _k in ("binance-flows", "binance-flows-report"):
    if _k in LOOKUPS_BF:
        LOOKUPS_BF[_k]["desc"] = LOOKUPS_BF[_k]["desc"].rstrip(". ") + ". " + DOC14.rstrip(".")
