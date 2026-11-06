"""upd43: stall check by observer read time, BTC flow dedup/paging, partial-window states, response receipts,
label-cleanup count reconciliation, per-store ecom placeholder counts, Mercado Bitcoin incident."""
import hashlib, json, logging, os, sqlite3, threading, time
from typing import Any, Dict, List, Optional

log = logging.getLogger("upd43")
NA = "not applicable"
DATA = os.environ.get("COLLECTED_DATA_DIR", "/collected_data")


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)) if t else None


# ---------------- 2a: stall check ----------------
def _freshness(c: str) -> Dict[str, Any]:
    from . import bfx as X, binance_flows as BF
    now = time.time()
    f = X.F.get(c) or {}
    obs = [dict(o) for o in (f.get("obs") or [])]
    ok = [o for o in obs if o.get("ok")]
    thr = X.STALL_S[c]
    read_t = f.get("t") or now
    hb, ht = (BF.S.get("head") or {}).get(c, (None, None))
    last_ok = (BF.S.get("last_ok") or {}).get(c)
    for o in ok:
        o["head_age_at_read_s"] = int(read_t - o["_bt"])  # measured when the observer was read, not when this response is built
    top = max([o["block"] for o in ok], default=None)
    coll_ahead = hb is not None and top is not None and hb >= top
    coll_recent = bool(ht) and (read_t - ht) <= thr
    res = {"stall_rule": f"chain_stall only when BOTH observers, at the moment they were read, show no new block for > {thr}s AND our "
                         f"collector is not current (collector head below the observers' head and older than {thr}s). If our collector "
                         f"is current or ahead, it is never a stall. Collector trailing by > {thr + X.LOOP_S[c]}s = provider_lag.",
           "observers": [{k: v for k, v in o.items() if k != "_bt"} for o in obs],
           "collector_head": {"block": hb, "block_time": _iso(ht), "last_success_at": _iso(last_ok),
                              "current_or_ahead": bool(coll_ahead or coll_recent)},
           "observers_read_at": _iso(int(read_t)), "as_of": _iso(int(read_t)), "block": top}
    if len(ok) < 2:
        res.update(state="ok" if coll_recent else "unverified",
                   detail=(f"only {len(ok)} of 2 observers answered; our collector head is current" if coll_recent
                           else f"only {len(ok)} of 2 observers answered; no stall verdict"))
        return res
    best = max(ok, key=lambda o: o["block"])
    stale = [o["head_age_at_read_s"] > thr for o in ok]
    if coll_ahead or coll_recent:
        res.update(state="ok", detail="our collector is current or ahead of the observers; chain live")
    elif all(stale):
        res.update(state="chain_stall", detail=f"both observers showed no new block for > {thr}s when read and our collector is not newer")
    elif any(stale):
        res.update(state="provider_lag", detail="observers disagree (one was behind when read); chain is producing blocks")
    elif ht and best["_bt"] - ht > thr + X.LOOP_S[c]:
        res.update(state="provider_lag", detail=f"our collector head trails the chain by {int(best['_bt'] - ht)}s (RPC lag, chain live)")
    else:
        res.update(state="ok", detail="chain live per both observers; collector head current")
    res["collector_lag_s"] = max(0, int(best["_bt"] - ht)) if ht else None
    return res


# ---------------- 2b: Bitcoin flows (dedup by tx, full paging to the 25h cut) ----------------
BTC_MAX_PAGES = 40


def _btc_refresh() -> None:
    from . import binance_flows as BF, hack_multichain as _h
    S = BF.S
    tip = int(_h._btc("/blocks/tip/height"))
    try:
        S["head"]["bitcoin"] = (tip, int(_h._btc(f"/block/{_h._btc('/blocks/tip/hash')}")["timestamp"]))
    except Exception:
        S["head"]["bitcoin"] = (tip, int(time.time()))
    W = S["wallets"]["bitcoin"]
    cut = int(time.time()) - 25 * 3600
    txs: Dict[str, Any] = {}
    capped = []
    for w in W:
        last, done = None, False
        for _ in range(BTC_MAX_PAGES):
            page = _h._btc(f"/address/{w}/txs" + (f"/chain/{last}" if last else ""))
            conf = [t for t in page or [] if (t.get("status") or {}).get("confirmed")]
            for t in conf:
                txs[t["txid"]] = t
            if not page or len(page) < 25 or (page[-1].get("status") or {}).get("block_time", 0) < cut:
                done = True
                break
            last = page[-1]["txid"]
        if not done:
            capped.append(w)
        time.sleep(0.3)
    evs = []
    for t in txs.values():
        st = t["status"]
        if st.get("block_time", 0) < cut:
            continue
        ins = [v.get("prevout") or {} for v in t.get("vin", [])]
        outs = t.get("vout", [])
        ours_in = [p.get("scriptpubkey_address") for p in ins if p.get("scriptpubkey_address") in W]
        if ours_in:  # spends from a tracked wallet: outflow = everything paid to non-tracked addresses
            ext = [o for o in outs if o.get("scriptpubkey_address") not in W]
            amt = sum(o.get("value", 0) for o in ext) / 1e8
            if amt > 0:
                big = max(ext, key=lambda o: o.get("value", 0))
                evs.append({"tx": t["txid"], "block": st["block_height"], "time": st["block_time"], "asset": "BTC", "psym": "BTC",
                            "amount": amt, "dir": "out", "wallet": ours_in[0], "cp": big.get("scriptpubkey_address"),
                            "outputs_external": len(ext)})
        else:  # inflow = sum of outputs to ANY tracked wallet (one event per tx)
            mine = [o for o in outs if o.get("scriptpubkey_address") in W]
            amt = sum(o.get("value", 0) for o in mine) / 1e8
            if amt > 0:
                evs.append({"tx": t["txid"], "block": st["block_height"], "time": st["block_time"], "asset": "BTC", "psym": "BTC",
                            "amount": amt, "dir": "in", "wallet": mine[0].get("scriptpubkey_address"),
                            "wallets_credited": len({o.get("scriptpubkey_address") for o in mine}),
                            "cp": (ins[0] or {}).get("scriptpubkey_address") if ins else None})
    with BF._lk:
        S["events"]["bitcoin"] = evs  # rebuilt from a full re-read every cycle; nothing carried over
    S["btc_complete"] = not capped
    S["btc_capped_wallets"] = capped
    S["btc_init"] = True
    S["status"]["bitcoin"] = "ok"


def _wrap_agg(orig):
    def _agg(chain, secs):
        from . import binance_flows as BF
        r = orig(chain, secs)
        if chain == "bitcoin" and r.get("window_fully_covered") and not BF.S.get("btc_complete", False):
            for k in ("token_net_outflow_usd", "token_outflow_usd", "token_inflow_usd", "net_outflow_usd_incl_native"):
                r[k] = None
            r["window_fully_covered"] = False
            r["note"] = f"Bitcoin history paging hit the {BTC_MAX_PAGES}-page cap for {len(BF.S.get('btc_capped_wallets') or [])} wallet(s); total withheld"
        return r
    return _agg


# ---------------- 3b: partial windows + transparency ----------------
_COV: Dict[str, List] = {}
_LAST: Dict[str, Dict[str, Any]] = {}


def _cov_eta(BF, c, lo):
    cov = (BF.S.get("coverage_from") or {}).get(c)
    h = _COV.setdefault(c, [])
    now = time.time()
    if cov is not None and (not h or h[-1][1] != cov):
        h.append((now, cov))
        del h[:-20]
    if cov is None or lo is None:
        return None
    if cov <= lo:
        return _iso(int(now))
    if len(h) >= 2 and h[0][1] > h[-1][1] and h[-1][0] > h[0][0]:
        rate = (h[0][1] - h[-1][1]) / (h[-1][0] - h[0][0])  # blocks of history gained per second
        return _iso(int(now + (cov - lo) / rate))
    return None


def _wstate(BF, c, w, secs, v):
    a = v.get(w) or {}
    chk = (v.get("check_" + w) or {})
    st = chk.get("state")
    nat = a.get("native")
    if st == "error":
        return {"state": "partial", "reason": f"latest refresh failed: {chk.get('last_error')}", "will_backfill": True,
                "backfill_eta": _iso(int(time.time() + (60 if c in BF.EVM else 120))), "backfill_basis": "retried every refresh cycle"}
    if not a.get("window_fully_covered"):
        if c == "bsc" and w == "24h":
            return {"state": "partial", "reason": "BSC 24h is not scanned: free BSC RPCs cap log range and rate; only the 1h window is covered",
                    "will_backfill": False, "backfill_eta": None}
        if c == "bitcoin":
            return {"state": "partial", "reason": a.get("note") or "Bitcoin history not fully read", "will_backfill": True,
                    "backfill_eta": _iso(int(time.time() + 120)), "backfill_basis": "full re-read every 2-minute cycle"}
        lo = (a.get("window_start") or {}).get("block")
        eta = _cov_eta(BF, c, lo) if c in BF.EVM else None
        return {"state": "partial", "reason": a.get("note") or a.get("status") or "window not fully scanned since collector start",
                "will_backfill": True, "backfill_eta": eta,
                "backfill_basis": "measured backfill rate of the log scanner" if eta else "rate not measured yet; backfill is running"}
    if c != "bitcoin" and isinstance(nat, dict) and nat.get("net_usd") is None:
        sn = [s for s in BF.S.get("native") or [] if c in s["chains"]]
        _now = time.time()  # fix44: first snapshot after any gap that can serve as the window start
        _nx = [x["t"] for x in sn if x["t"] >= _now - secs - 900]
        due = (min(_nx) + secs - 900) if _nx else None
        if nat.get("net_amount") is not None:
            why, eta = "no USD price for the native coin at this refresh", _iso(int(time.time() + 120))
        else:
            why = nat.get("reason") or "no balance history for this window"
            eta = _iso(due) if due and due > time.time() else _iso(int(time.time() + 120))
        return {"state": "partial", "reason": "token transfers complete; native coin change not included yet: " + why,
                "will_backfill": True, "backfill_eta": eta,
                "backfill_basis": "native balances are snapshotted from collector start; window is included once history covers it"}
    return {"state": "complete", "reason": None, "will_backfill": None, "backfill_eta": None}


def _wrap_snapshot(orig):
    def _snapshot():
        from . import binance_flows as BF
        snap = orig()
        ac = snap["all_chains"]
        wins, comp, changed = {}, {}, {}
        now = int(time.time())
        for c, v in (snap.get("chains") or {}).items():
            wins[c], comp[c] = {}, {}
            for w, secs in (("1h", 3600), ("24h", 86400)):
                try:
                    ws = _wstate(BF, c, w, secs, v)
                except Exception as e:
                    ws = {"state": "partial", "reason": f"state check failed: {type(e).__name__}", "will_backfill": None, "backfill_eta": None}
                ws["as_of"] = snap.get("as_of")
                ws["block"] = (v.get("head") or {}).get("block") if c in BF.EVM or c in ("tron", "bitcoin") else NA
                wins[c][w] = ws
                v.setdefault(w, {})["window_state"] = ws
                a = v.get(w) or {}
                nat = (a.get("native") or {}).get("net_usd") if isinstance(a.get("native"), dict) else None
                comp[c][w] = {"token_net_outflow_usd": a.get("token_net_outflow_usd"), "native_net_outflow_usd": nat,
                              "total": a.get("net_outflow_usd_incl_native"),
                              "transfers": sum((x.get("withdrawals") or 0) + (x.get("deposits") or 0) for x in (a.get("by_asset") or {}).values())}
                key = f"{c}:{w}"
                val = a.get("net_outflow_usd_incl_native")
                if key not in _LAST or _LAST[key]["v"] != val:
                    _LAST[key] = {"v": val, "t": now}
                changed[c] = changed.get(c, {})
                changed[c][w] = _iso(_LAST[key]["t"])
        for w in ("1h", "24h"):
            bad = {c: wins[c][w]["state"] for c in wins if wins[c][w]["state"] != "complete"}
            ac[f"window_state_{w}"] = {"state": "partial" if bad else "complete", "chains_partial": sorted(bad),
                                       "reason": ("total sums only the complete parts; see windows.<chain>" if bad else None),
                                       "as_of": snap.get("as_of"), "block": "per chain, see windows.<chain>.<window>.block"}
        ac["windows"] = wins
        ac["components"] = comp
        ac["value_last_changed_at"] = changed
        ac["flow_math_note"] = ("Each window = tracked token transfers in the window (out minus in, valued at Chainlink prices) plus the "
                                "change in native coin balance where balance history covers the window. A 1h total of 0 on a complete "
                                "window means no tracked transfer happened in that hour (the tracked wallets are public 2022 lists and "
                                "many are dormant; see activity.last_transfer). A 24h total changes only when a transfer enters or leaves "
                                "the window or the native balance moves, so it can stay flat for minutes. 1h and 24h can have opposite "
                                "signs (e.g. a large inflow in the last hour after a larger outflow earlier in the day).")
        return snap
    return _snapshot


# ---------------- 3a: receipts ----------------
_RDB = os.path.join(DATA, "receipts.sqlite")
_rlk = threading.Lock()
SKIP = ("/mcp", "/sse", "/docs", "/openapi.json", "/redoc", "/llms.txt", "/.well-known")


def _rdb():
    c = sqlite3.connect(_RDB, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS receipts(sha256 TEXT PRIMARY KEY, route TEXT, as_of TEXT, block TEXT, issued_at TEXT, paid INT, bytes INT)")
    return c


def canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


def _find(d, keys, depth=0):
    if not isinstance(d, dict) or depth > 3:
        return None
    for k in keys:
        if k in d and d[k] not in (None, "", {}):
            return d[k]
    for k in ("data_as_of", "all_chains", "meta", "data"):
        r = _find(d.get(k), keys, depth + 1)
        if r is not None:
            return r
    return None


def _asof_block(body):
    a = _find(body, ("as_of", "timestamp_utc", "computed_at", "snapshot_at", "updated_at"))
    if isinstance(a, dict):
        a = a.get("timestamp_utc") or a.get("time")
    b = _find(body, ("block", "head_block", "blocks"))
    return (a if isinstance(a, (str, int, float)) else None), (b if b is not None else "not applicable (off-chain or no block in this response)")


def make_receipt(body: Dict[str, Any], route: str, paid: bool) -> Dict[str, Any]:
    h = hashlib.sha256(canon(body)).hexdigest()
    a, b = _asof_block(body)
    issued = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    r = {"sha256": h, "hash_of": "UTF-8 JSON of this response with the 'receipt' key removed, keys sorted, separators (',', ':')",
         "as_of": a or issued, "as_of_basis": "from the response" if a else "response time (no as_of in body)",
         "block": b, "issued_at": issued, "route": route, "paid": paid,
         "verify": "/v1/free/receipts/verify?sha256=" + h}
    try:
        with _rlk:
            c = _rdb()
            c.execute("INSERT OR REPLACE INTO receipts VALUES(?,?,?,?,?,?,?)", (h, route, str(r["as_of"]), json.dumps(b, default=str), issued, int(paid), 0))
            c.commit()
            c.close()
    except Exception as e:
        log.warning("receipt store: %s", e)
    return r


def _prune():
    while True:
        try:
            with _rlk:
                c = _rdb()
                c.execute("DELETE FROM receipts WHERE issued_at < ?", (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 30 * 86400)),))
                c.commit()
                c.close()
        except Exception as e:
            log.warning("receipt prune: %s", e)
        time.sleep(86400)


def verify(sha256: Optional[str] = None, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "block": NA}
    if body is not None:
        claimed = (body.get("receipt") or {}).get("sha256") if isinstance(body, dict) else None
        b = {k: v for k, v in body.items() if k != "receipt"} if isinstance(body, dict) else body
        sha = hashlib.sha256(canon(b)).hexdigest()
        out.update(recomputed_sha256=sha, claimed_sha256=claimed, body_matches_claim=(claimed == sha) if claimed else None)
        sha256 = sha256 or sha
    if not sha256:
        out["error"] = "pass ?sha256=<hash> or POST the full JSON response"
        return out
    with _rlk:
        c = _rdb()
        row = c.execute("SELECT route, as_of, block, issued_at, paid FROM receipts WHERE sha256=?", (sha256.lower(),)).fetchone()
        c.close()
    out["sha256"] = sha256.lower()
    out["issued_by_us"] = bool(row)
    if row:
        out["receipt"] = {"route": row[0], "as_of": row[1], "block": json.loads(row[2]), "issued_at": row[3], "paid": bool(row[4])}
    out["valid"] = bool(row) and (out.get("body_matches_claim") is not False)
    out["retention"] = "receipts kept 30 days"
    return out


def _install_receipts(app):
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import Response

    class Receipts(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            resp = await call_next(request)
            p = request.url.path
            ct = resp.headers.get("content-type", "")
            if not p.startswith("/v1/") or p.startswith(SKIP) or "application/json" not in ct or not (200 <= resp.status_code < 300):
                return resp
            chunks = []
            async for ch in resp.body_iterator:
                chunks.append(ch if isinstance(ch, bytes) else ch.encode())
            raw = b"".join(chunks)
            hdr = {k: v for k, v in resp.headers.items() if k.lower() != "content-length"}
            try:
                body = json.loads(raw) if len(raw) < 8_000_000 else None
            except Exception:
                body = None
            if isinstance(body, dict) and "receipt" not in body:
                from starlette.concurrency import run_in_threadpool
                r = await run_in_threadpool(make_receipt, body, p, not p.startswith("/v1/free/"))
                body["receipt"] = r
                raw = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
                hdr["X-Receipt-SHA256"] = r["sha256"]
            else:
                hdr["X-Receipt-SHA256"] = hashlib.sha256(raw).hexdigest()
                hdr["X-Receipt-Basis"] = "sha256 of raw body bytes (non-object or very large JSON)"
            return Response(content=raw, status_code=resp.status_code, headers=hdr, media_type="application/json")

    app.add_middleware(Receipts)
    threading.Thread(target=_prune, daemon=True, name="receipt_prune").start()


# ---------------- 2c: label cleanup counts ----------------
def _wrap_cov(orig):
    def coverage():
        from . import labels as L, labelsx as LX
        out = orig()
        cats = out.get("labels_by_category") or {}
        n = cats.pop("dropped_defillama_placeholders", None)
        e = LX._load_log().get("entries", []) if hasattr(LX, "_load_log") else []
        rm = [x for x in e if (x.get("removed") or {}).get("count")]
        rows = rm[-1]["removed"]["count"] if rm else None
        llama = L._ix.get("llama") or []
        seen: Dict[str, int] = {}
        for p in llama:
            a = p.get("address")
            if isinstance(a, str) and a:
                k = a.rpartition(":")[2].lower()
                seen[k] = seen.get(k, 0) + 1
        short, rset = 0, set()
        for p in llama:
            a = p.get("address")
            if not a or not isinstance(a, str):
                continue
            ad = a.rpartition(":")[2]
            if L._placeholder(ad) or seen.get(ad.lower(), 0) > 1:
                k = L._key(ad)
                if not k or len(k) < 20:
                    short += 1
                else:
                    rset.add((k, p.get("name")))
        ent_idx = None if n is None else n - short
        addrs = len({k for k, _ in rset})
        out["placeholder_cleanup"] = {
            "defillama_entries_dropped": n,
            "breakdown": {"entries_with_unindexable_address": short, "entries_with_indexable_address": ent_idx,
                          "distinct_address_name_rows": len(rset), "duplicate_entries_collapsed": (ent_idx - len(rset)) if ent_idx is not None else None,
                          "sums": "entries_with_unindexable_address + distinct_address_name_rows + duplicate_entries_collapsed = defillama_entries_dropped"},
            "label_rows_removed": rows, "label_rows_removed_basis": "changelog cleanup entry (rows removed from the index at that rebuild)",
            "unique_addresses_removed": addrs,
            "explanation": ("Three different units. defillama_entries_dropped counts DefiLlama protocol records skipped as placeholders "
                            "(1 per record). label_rows_removed counts unique address|category|name rows that disappeared from our "
                            "index at the cleanup rebuild (the changelog unit). Records that shared the same address and name, or had "
                            "an address we do not index, collapse into fewer rows; the difference is entries_not_matching_a_row. "
                            "unique_addresses_removed counts distinct addresses among those rows (many placeholder rows shared one address)."),
            "changelog": "/v1/free/labels/changelog",
            "as_of": out.get("index_built"), "block": NA + " (off-chain label list)"}
        return out
    return coverage


# ---------------- 1: ecom per-store placeholder counts ----------------
def _wrap_quality(EF):
    orig = EF.quality
    cache = {"t": 0, "v": None}

    def quality(es):
        v = dict(orig(es))
        if time.time() - cache["t"] < 900 and cache["v"] is not None:
            v.update(cache["v"])
            return v
        c = es.db()
        per: Dict[str, Dict[str, Any]] = {}
        for d, p, ca in c.execute("SELECT domain, price, compare_at_price FROM variants"):
            s = per.setdefault(d, {"store": d, "rows": 0, "placeholder_flagged": 0, "by_reason": {}})
            s["rows"] += 1
            rs = EF.reasons(p, ca)
            if rs:
                s["placeholder_flagged"] += 1
                for x in rs:
                    s["by_reason"][x] = s["by_reason"].get(x, 0) + 1
        try:
            lc = dict(c.execute("SELECT domain, last_crawl FROM stores").fetchall())
        except Exception:
            lc = {}
        rows = []
        for s in per.values():
            s["flagged_pct"] = round(100.0 * s["placeholder_flagged"] / s["rows"], 2) if s["rows"] else None
            s["as_of"] = lc.get(s["store"]) or v.get("as_of")
            s["block"] = NA + " (off-chain)"
            rows.append(s)
        rows.sort(key=lambda s: (-s["placeholder_flagged"], s["store"]))
        extra = {"by_store": rows, "stores_checked": len(rows), "stores_with_placeholders": sum(1 for s in rows if s["placeholder_flagged"]),
                 "by_store_note": "Every store in the latest crawl, most placeholder rows first; as_of = that store's last crawl time"}
        cache.update(t=time.time(), v=extra)
        v.update(extra)
        return v
    EF.quality = quality


# ---------------- 4: Mercado Bitcoin incident ----------------
MB = "mercado-bitcoin-2026"
MB_SRC = "Bitquery investigation 'Mercado Bitcoin hack' (read 2026-11-03): https://bitquery.io/investigations/mercado-bitcoin-hack"
MB_ETH = [
    ("0xe485e7a70b1131216cb47ca4555c4bbb2c742478", "Main attacker address (same on every EVM chain)"),
    ("0x8419e7824e785c4ac8d9d94a0093c437ab1ff320", "New attacker wallet, 12:07 UTC Nov 2"),
    ("0x0110b9a13aa40a9186cacd9e45d860cd4ee53b51", "New attacker wallet, 14:57 UTC Nov 2 (AAVE, PEPE)"),
    ("0x3ce76d2d27cc80e44c7d9532423633c7f940c779", "New attacker wallet, 13:12 UTC (USDC frozen by Circle)"),
    ("0x3c6daf929ff36e50ceb66807ebe8abe83653e051", "New attacker wallet, 13:34 UTC (PAXG frozen by Paxos)"),
    ("0x59a919e8d0582ce62c8de9431b3ebc6fa121731e", "Fresh attacker wallet (2,979 ETH gathered overnight per Bitquery)"),
    ("0x071f8366375fdd21423bb23ae19e4b91f03f9270", "Fresh attacker wallet (670 ETH gathered overnight per Bitquery)"),
    ("0x4d5f3758938df94a76bfd19652f118ccd7a72c9d", "Collects ETH from Bridgers (211 ETH by 08:49 UTC Nov 3 per Bitquery)"),
]
MB_BTC = [("19qGpsHvzh1t6pVi526XNNkh6hW6prDZp", "Attacker Bitcoin address (106.07 BTC per Bitquery)")]
MB_TRON = [("TEX3HuDRHRa3tnYpy96M4UfSx62VhDyA5L", "Attacker main Tron address (USDT frozen per Bitquery)")]
MB_START_BLOCK = 26321000  # Ethereum, ~18:00 UTC Nov 1 2026 (before the 06:24 UTC Nov 2 drain); confirmed on-chain at install
MB_META = {
    "name": "Mercado Bitcoin hot-wallet hack (Nov 2026)",
    "date": "2026-11-02",
    "victim": "Mercado Bitcoin (Brazil)",
    "victim_hot_wallet_not_tracked_as_attacker": "0x5cc7787778b4d20a947dcc53b41f593cfd27d548 (emptied 06:24-06:48 UTC Nov 2)",
    "reported_loss": {"value": "$48.6M across 10 chains", "source": MB_SRC, "as_of": "2026-11-03T08:49:00Z",
                      "block": {"chain": "ethereum", "number": 26333183, "basis": "Ethereum block at Bitquery's read time"}},
    "reported_frozen": {"value": "$11.4M frozen (Circle USDC, Paxos PAXG, Tether USDT on Tron/Solana/Ethereum)", "source": MB_SRC,
                        "as_of": "2026-11-03T08:49:00Z", "block": {"chain": "ethereum", "number": 26333183, "basis": "Bitquery read time"}},
    "other_chains_not_tracked": {"solana": "3YX6WHncP1Ra4AotE9YLawFt4nPmxyux288FWXkEW97J", "stellar": "GDXYRI6IONQA5YTGRLQ6O3FGJ2BWXPJTTQPUXFPSEXSRKBZLFKMZSOLP",
                                 "avalanche": "0x71ddbdd9b865d74b3c21068d71b3a11e110db728", "note": "listed by Bitquery; no free collector here yet"},
    "references": ["https://bitquery.io/investigations/mercado-bitcoin-hack",
                   "https://www.theblock.co/post/mercado-bitcoin-hack (news context only; its loss figure cites a source we do not use)"],
    "as_of": "2026-11-03T08:49:00Z", "block": {"chain": "ethereum", "number": 26333183, "basis": "facts as read by Bitquery"},
}


def _register_mb(HT, inc=None):  # fix46: idempotent; also called at hack_tracker import so the startup label build always sees it
    if inc is None:
        if MB in HT.INCIDENTS:
            return
        inc = dict(MB_META)
        inc.update({"reported_loss_usd": "$48.6M across 10 chains (Bitquery, as of 2026-11-03 08:49 UTC, Ethereum block 26,333,183)",
                    "attribution": "not attributed", "start_block": MB_START_BLOCK, "chain": "ethereum",
                    "addresses": [{"address": a, "label": l, "confidence": "high", "source": MB_SRC} for a, l in MB_ETH]})
    HT.INCIDENTS[MB] = inc
    if MB not in tuple(HT._MC_INCIDENTS):
        HT._MC_INCIDENTS = tuple(HT._MC_INCIDENTS) + (MB,)


def _install_mercado():
    from . import hack_tracker as HT, hack_multichain as HM
    inc = dict(MB_META)
    inc.update({"reported_loss_usd": "$48.6M across 10 chains (Bitquery, as of 2026-11-03 08:49 UTC, Ethereum block 26,333,183)",
                "attribution": "not attributed", "start_block": MB_START_BLOCK, "chain": "ethereum",
                "addresses": [{"address": a, "label": l, "confidence": "high", "source": MB_SRC} for a, l in MB_ETH]})
    _register_mb(HT, inc)
    mb_chains = {"bitcoin": {"addresses": [{"address": a, "label": l, "confidence": "high", "source": MB_SRC} for a, l in MB_BTC]},
                 "tron": {"addresses": [{"address": a, "label": l, "confidence": "high", "source": MB_SRC} for a, l in MB_TRON]}}
    if not hasattr(HM, 'CHAINS_BASE'):
        HM.CHAINS_BASE = HM.CHAINS  # fix46: labels read this, never the temporary Mercado swap
    orig = HM.refresh
    lk = threading.Lock()

    def refresh(mc, btc_usd):
        if mc.get("_incident") == MB:
            with lk:
                keep = HM.CHAINS
                HM.CHAINS = mb_chains
                try:
                    return orig(mc, btc_usd)
                finally:
                    HM.CHAINS = keep
        with lk:
            return orig(mc, btc_usd)
    HM.refresh = refresh

    m = HT._mc.setdefault(MB, {})  # seeded before the multichain loop's first run; a saved state (with _incident) replaces it on load
    m["_incident"] = MB
    m.setdefault("tron", {}).setdefault("since_ms", 1793556000000)  # 2026-11-01 18:00 UTC


_TRX = {"t": 0, "p": None, "blk": None}


def _wrap_prices(BF):
    orig = BF._prices

    def _prices():
        orig()
        if BF.S["prices"].get("TRX") is not None:
            return
        from . import hack_multichain as HM
        if time.time() - _TRX["t"] > 110:
            p = HM._trx_price()
            if p:
                _TRX.update(t=time.time(), p=p, blk=(BF.S.get("head") or {}).get("tron", (None,))[0])
        if _TRX["p"]:
            BF.S["prices"]["TRX"] = _TRX["p"]
            BF.S["inputs"].append({"symbol": "TRX", "price": _TRX["p"], "price_as_of": _iso(int(_TRX["t"])),
                                   "price_block": {"chain": "tron", "number": _TRX["blk"], "basis": "Tron head at quote time (quote read at latest)"},
                                   "source": "SunSwap V2 router getAmountsOut 1 USDT->WTRX on Tron (on-chain quote; Chainlink TRX/USD feed unavailable)"})
    BF._prices = _prices


def install(app, rate_limit, free_endpoints=None):
    from . import bfx as X, binance_flows as BF, labels as L, ecomfix as EF
    from fastapi import Request
    from starlette.concurrency import run_in_threadpool
    X.freshness = _freshness
    BF._btc_refresh = _btc_refresh
    _wrap_prices(BF)
    BF._agg = _wrap_agg(BF._agg)
    BF._snapshot = _wrap_snapshot(BF._snapshot)
    L.coverage = _wrap_cov(L.coverage)
    _wrap_quality(EF)
    try:
        _install_mercado()
    except Exception as e:
        log.warning("mercado incident: %s", e)
    _install_receipts(app)
    try:
        from . import fix46 as _f46
        _f46.install()
    except Exception as e:
        log.warning("fix46: %s", e)

    @app.get("/v1/free/receipts/verify", tags=["free"], summary="Free: verify a response receipt (sha256)")
    async def rc_get(request: Request, sha256: str = ""):
        rate_limit(request)
        return await run_in_threadpool(verify, sha256 or None, None)

    @app.post("/v1/free/receipts/verify", tags=["free"], summary="Free: POST a full JSON response to recompute and verify its receipt")
    async def rc_post(request: Request):
        rate_limit(request)
        try:
            body = await request.json()
        except Exception:
            body = None
        if not isinstance(body, dict):
            return {"error": "POST the full JSON response object", "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "block": NA}
        return await run_in_threadpool(verify, None, body)
    if free_endpoints is not None:
        free_endpoints["/v1/free/receipts/verify"] = ("Verify a response receipt: GET ?sha256= or POST the full JSON response. Every JSON "
                                                      "response carries receipt {sha256, as_of, block, issued_at, route}.")
