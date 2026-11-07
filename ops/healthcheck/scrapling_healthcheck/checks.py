"""Public and local assertions. Statuses: pass, fail, watch, missing, not_configured."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from .http_client import HttpClient, join, loads, same_site
from .redact import redact

BANNED_MARKERS = (
    "yahoo", "coingecko", "geckoterminal", "goplus", "blockscout", "arkham",
    "nansen", "whale-alert", "whalealert", "cielo", "chainalysis",
)


@dataclass
class Check:
    check_id: str
    status: str
    summary: str
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        code = str(self.details.get("code") or self.summary)
        return f"{self.status}|{code}"[:180]

    def fails_run(self) -> bool:
        return self.status in ("fail", "missing")


def _ok(check_id: str, summary: str, **details: Any) -> Check:
    return Check(check_id, "pass", summary, details)


def _bad(check_id: str, summary: str, code: str, **details: Any) -> Check:
    details["code"] = code
    return Check(check_id, "fail", redact(summary), details)


def _watch(check_id: str, summary: str, code: str, **details: Any) -> Check:
    details["code"] = code
    return Check(check_id, "watch", summary, details)


def _missing(check_id: str, summary: str) -> Check:
    return Check(check_id, "missing", summary, {"code": "missing"})


def _skip(check_id: str, summary: str) -> Check:
    return Check(check_id, "not_configured", summary, {"code": "not_configured"})


def money(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text


def near(left: Any, right: float) -> bool:
    try:
        return abs(float(left) - float(right)) < 1e-9
    except (TypeError, ValueError):
        return False


def parse_time(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and value > 1_000_000_000:
        return float(value)
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def age_of(value: Any, now: float) -> Optional[float]:
    ts = parse_time(value)
    if ts is None:
        return None
    return now - ts


class Runner:
    def __init__(self, config: Dict[str, Any], client: HttpClient, now: Optional[float] = None) -> None:
        self.cfg = config
        self.client = client
        self.now = time.time() if now is None else now
        self.base = str(config["base_url"]).rstrip("/")

    def url(self, path: str) -> str:
        return join(self.base, path)

    def get(self, path: str, headers: Optional[Dict[str, str]] = None):
        resp = self.client.request("GET", self.url(path), headers=headers)
        if resp.status and not same_site(self.base, resp.final_url):
            resp.error = "left configured host"
            resp.status = 0
        return resp

    def run(self) -> List[Check]:
        out: List[Check] = []
        out.append(self.homepage())
        for spec in self.cfg.get("free") or []:
            out.append(self.free_sample(spec))
        for spec in self.cfg.get("paid_402") or []:
            out.append(self.paid_402(spec))
        if self.cfg.get("bad_signature"):
            out.append(self.bad_signature(self.cfg["bad_signature"]))
        out.extend(self.price_consistency())
        out.append(self.x402_manifest())
        out.append(self.mpp_advertisement())
        out.extend(self.mcp())
        out.append(self.robots())
        out.append(self.sitemap())
        out.append(self.hacks())
        out.append(self.sync_evidence())
        out.append(self.demand_radar())
        out.append(self.request_log())
        return out

    def homepage(self) -> Check:
        resp = self.get("/")
        if resp.status != 200:
            return _bad("homepage", f"homepage HTTP {resp.status or resp.error}", "status")
        if "html" not in resp.headers.get("content-type", ""):
            return _bad("homepage", "homepage is not HTML", "content-type")
        text = resp.body.decode("utf-8", "replace").lower()
        if "scrapling" not in text or len(text) < 500:
            return _bad("homepage", "homepage body missing expected store text", "body")
        return _ok("homepage", "homepage 200 HTML")

    def free_sample(self, spec: Dict[str, Any]) -> Check:
        cid = "free:" + spec["id"]
        resp = self.get(spec["path"])
        if resp.status != 200:
            return _bad(cid, f"{spec['path']} HTTP {resp.status or resp.error}", "status")
        try:
            body = loads(resp.body)
        except Exception:
            return _bad(cid, f"{spec['path']} is not JSON", "body")
        if not isinstance(body, dict):
            return _bad(cid, f"{spec['path']} JSON is not an object", "body")
        if spec.get("require_free", True) and body.get("free") is not True:
            return _bad(cid, f"{spec['path']} free flag is not true", "body")
        for key in spec.get("require_keys") or []:
            if key not in body:
                return _bad(cid, f"{spec['path']} missing {key}", "body")
        if spec.get("require_list"):
            if not isinstance(body.get(spec["require_list"]), list) or not body.get(spec["require_list"]):
                return _bad(cid, f"{spec['path']} missing list {spec['require_list']}", "body")
        expect = spec.get("equals") or {}
        for key, value in expect.items():
            if body.get(key) != value:
                return _bad(cid, f"{spec['path']} {key} != expected", "body")
        if spec.get("as_of_field"):
            age = age_of(body.get(spec["as_of_field"]), self.now)
            limit = float(spec.get("max_age_seconds") or self.cfg.get("free_max_age_seconds") or 7200)
            if age is None:
                return _bad(cid, f"{spec['path']} has no parseable {spec['as_of_field']}", "stale")
            if age > limit:
                return _bad(cid, f"{spec['path']} {spec['as_of_field']} is stale", "stale", age_seconds=int(age))
        return _ok(cid, f"{spec['path']} 200")

    def paid_402(self, spec: Dict[str, Any]) -> Check:
        cid = "paid:" + spec["id"]
        resp = self.get(spec["path"])
        if resp.status == 400:
            return _bad(cid, f"{spec['path']} returned 400; that is not a healthy 402", "status-400")
        if resp.status != 402:
            return _bad(cid, f"{spec['path']} HTTP {resp.status or resp.error}, expected 402", "status")
        try:
            body = loads(resp.body)
        except Exception:
            return _bad(cid, f"{spec['path']} 402 body is not JSON", "body")
        if not isinstance(body, dict) or not near(body.get("priceUsd"), spec["usd"]):
            return _bad(cid, f"{spec['path']} priceUsd is not {money(spec['usd'])}", "price")
        if not isinstance(body.get("accepts"), list) or not body["accepts"]:
            return _bad(cid, f"{spec['path']} 402 has no accepts", "body")
        return _ok(cid, f"{spec['path']} 402 ${money(spec['usd'])}")

    def bad_signature(self, spec: Dict[str, Any]) -> Check:
        cid = "paid-badsig:" + spec["id"]
        resp = self.get(spec["path"], headers={"PAYMENT-SIGNATURE": "not-a-valid-payment"})
        if resp.status == 400:
            return _bad(cid, "malformed payment header returned 400, not 402", "status-400")
        if resp.status == 200:
            return _bad(cid, "malformed payment header was accepted", "accepted")
        if resp.status != 402:
            return _bad(cid, f"malformed payment header HTTP {resp.status or resp.error}", "status")
        try:
            body = loads(resp.body)
        except Exception:
            return _bad(cid, "malformed payment 402 is not JSON", "body")
        if not near(body.get("priceUsd"), spec["usd"]):
            return _bad(cid, "malformed payment 402 price drifted", "price")
        return _ok(cid, "invalid payment still 402 and unpaid")

    def price_consistency(self) -> List[Check]:
        spec = self.cfg.get("price_consistency") or {}
        path = spec.get("path") or "/v1/bundle/market-pulse"
        usd = float(spec.get("usd") if spec.get("usd") is not None else 0.001)
        label = money(usd)
        needle = f"{path} (${label})"
        out = []
        docs = self.get("/docs")
        llms = self.get("/llms.txt")
        doc_text = docs.body.decode("utf-8", "replace") if docs.status == 200 else ""
        llm_text = llms.body.decode("utf-8", "replace") if llms.status == 200 else ""
        if docs.status != 200 or needle not in doc_text:
            out.append(_bad("price:docs", f"docs missing {needle}", "price"))
        else:
            out.append(_ok("price:docs", f"docs shows {needle}"))
        if llms.status != 200 or path not in llm_text or f"${label}" not in llm_text:
            out.append(_bad("price:llms", f"llms.txt missing {path} ${label}", "price"))
        else:
            window = llm_text[max(0, llm_text.find(path) - 40): llm_text.find(path) + 80]
            if f"${label}" not in window and f"${label}" not in llm_text[llm_text.find(path): llm_text.find(path) + 160]:
                out.append(_bad("price:llms", f"llms.txt price near {path} is not ${label}", "price"))
            else:
                out.append(_ok("price:llms", f"llms.txt shows {path} ${label}"))
        spec_resp = self.get("/openapi.json")
        try:
            spec_body = loads(spec_resp.body) if spec_resp.status == 200 else {}
        except Exception:
            spec_body = {}
        amount = (((spec_body.get("paths") or {}).get(path) or {}).get("get") or {}).get("x-payment-info", {})
        amount = (amount.get("price") or {}).get("amount") if isinstance(amount, dict) else None
        guidance = str(((spec_body.get("info") or {}).get("x-guidance") or ""))
        if spec_resp.status != 200 or not str(spec_body.get("openapi", "")).startswith("3.1"):
            out.append(_bad("price:openapi", "openapi.json is not OpenAPI 3.1", "schema"))
        elif not near(amount, usd) or path not in guidance or f"${label}" not in guidance:
            out.append(_bad("price:openapi", f"openapi price for {path} is not {label}", "price"))
        else:
            out.append(_ok("price:openapi", f"openapi {path} amount {label}"))
        manifest = self.get("/.well-known/x402")
        found = _manifest_price(manifest.body if manifest.status == 200 else b"{}", path)
        if manifest.status != 200 or not near(found, usd):
            out.append(_bad("price:x402", f"x402 manifest price for {path} is not {label}", "price"))
        else:
            out.append(_ok("price:x402", f"x402 manifest {path} {label}"))
        return out

    def x402_manifest(self) -> Check:
        resp = self.get("/.well-known/x402")
        if resp.status != 200:
            return _bad("x402", f"x402 manifest HTTP {resp.status or resp.error}", "status")
        try:
            body = loads(resp.body)
        except Exception:
            return _bad("x402", "x402 manifest is not JSON", "body")
        if body.get("x402Version") != 2 or not isinstance(body.get("resources"), list):
            return _bad("x402", "x402 manifest schema mismatch", "schema")
        return _ok("x402", "x402 manifest version 2")

    def mpp_advertisement(self) -> Check:
        spec = self.cfg.get("mpp") or {}
        path = spec.get("path") or "/v1/bundle/market-pulse"
        expect = list(spec.get("intents") or ["charge", "session"])
        resp = self.get(path)
        if resp.status != 402:
            return _bad("mpp", f"MPP probe HTTP {resp.status or resp.error}", "status")
        header = resp.headers.get("www-authenticate") or ""
        intents = re.findall(r'intent="([a-z]+)"', header)
        resources = _mpp_resources(header)
        if intents != expect and sorted(intents) != sorted(expect):
            return _bad("mpp", "MPP intents differ from config", "intents", saw=intents)
        if not resources or any(item != path for item in resources):
            return _bad("mpp", "MPP resource is not the request path", "resource", saw=resources)
        return _ok("mpp", "MPP challenge advertises " + ",".join(expect) + " for " + path)

    def mcp(self) -> List[Check]:
        init = self._rpc("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "scrapling-health", "version": "1"},
        }, 1)
        listed = self._rpc("tools/list", {}, 2)
        return [init, listed]

    def _rpc(self, method: str, params: Dict[str, Any], msg_id: int) -> Check:
        cid = "mcp:" + method
        payload = json.dumps({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}).encode()
        resp = self.client.request(
            "POST", self.url("/mcp"), body=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        )
        if resp.status != 200:
            return _bad(cid, f"MCP {method} HTTP {resp.status or resp.error}", "status")
        try:
            body = loads(resp.body)
        except Exception:
            return _bad(cid, f"MCP {method} is not JSON", "body")
        result = body.get("result") if isinstance(body, dict) else None
        if not isinstance(result, dict) or body.get("id") != msg_id:
            return _bad(cid, f"MCP {method} missing result", "schema")
        if method == "initialize":
            if not result.get("protocolVersion") or not (result.get("serverInfo") or {}).get("name"):
                return _bad(cid, "MCP initialize schema mismatch", "schema")
            return _ok(cid, "MCP initialize " + str(result.get("protocolVersion")))
        tools = result.get("tools")
        if not isinstance(tools, list) or not tools:
            return _bad(cid, "MCP tools/list is empty", "schema")
        if any(not isinstance(t, dict) or not t.get("name") for t in tools):
            return _bad(cid, "MCP tool entry missing name", "schema")
        return _ok(cid, f"MCP tools/list count {len(tools)}")

    def robots(self) -> Check:
        resp = self.get("/robots.txt")
        text = resp.body.decode("utf-8", "replace")
        if resp.status != 200 or "Sitemap:" not in text or "/sitemap.xml" not in text:
            return _bad("robots", f"robots.txt HTTP {resp.status or resp.error} or missing sitemap", "body")
        return _ok("robots", "robots.txt points at sitemap")

    def sitemap(self) -> Check:
        resp = self.get("/sitemap.xml")
        text = resp.body.decode("utf-8", "replace")
        if resp.status != 200 or "<loc>" not in text.lower():
            return _bad("sitemap", f"sitemap HTTP {resp.status or resp.error} or no loc", "body")
        return _ok("sitemap", "sitemap lists locations", locs=text.lower().count("<loc>"))

    def hacks(self) -> Check:
        resp = self.get("/hacks/data")
        if resp.status != 200:
            return _bad("hacks", f"/hacks/data HTTP {resp.status or resp.error}", "status")
        try:
            body = loads(resp.body)
        except Exception:
            return _bad("hacks", "/hacks/data is not JSON", "body")
        age = age_of(body.get("generated_at") if isinstance(body, dict) else None, self.now)
        limit = float(self.cfg.get("hacks_max_age_seconds") or 900)
        if age is None:
            return _bad("hacks", "/hacks/data has no generated_at", "stale")
        if age > limit:
            return _bad("hacks", "/hacks/data generated_at is stale", "stale", age_seconds=int(age))
        if isinstance(body, dict) and body.get("tracker_status") not in (None, 200):
            return _bad("hacks", "/hacks/data tracker_status is not 200", "body")
        return _ok("hacks", "/hacks/data fresh", age_seconds=int(age))

    def sync_evidence(self) -> Check:
        spec = (self.cfg.get("local") or {}).get("sync_evidence") or {}
        return _file_age_check("local:sync", spec, self.now, "standby sync evidence")

    def demand_radar(self) -> Check:
        spec = (self.cfg.get("local") or {}).get("demand_radar") or {}
        return _sqlite_age_check(
            "local:demand_radar", spec, self.now,
            "SELECT MAX(ts) FROM demand",
            "demand_radar",
        )

    def request_log(self) -> Check:
        spec = (self.cfg.get("local") or {}).get("request_log") or {}
        path = spec.get("path")
        if not path:
            return _skip("local:request_log", "request log path not configured")
        if not os.path.isfile(path):
            return _missing("local:request_log", "request log database is missing")
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
            cols = {row[1] for row in con.execute("PRAGMA table_info(request_log)")}
            if "ts" not in cols or "status" not in cols:
                con.close()
                return _missing("local:request_log", "request_log schema is not the expected one")
            newest = con.execute("SELECT MAX(ts) FROM request_log").fetchone()[0]
            errors = con.execute(
                "SELECT COUNT(*) FROM request_log WHERE ts >= ? AND status >= 500",
                (self.now - 86400,),
            ).fetchone()[0]
            con.close()
        except sqlite3.Error:
            return _missing("local:request_log", "request log could not be read")
        if newest is None:
            return _missing("local:request_log", "request log has no rows")
        age = self.now - float(newest)
        limit = float(spec.get("max_age_seconds") or 21600)
        if age > limit:
            return _bad("local:request_log", "request log is stale", "stale", age_seconds=int(age))
        cap = spec.get("max_5xx_24h")
        if cap is not None and int(errors) > int(cap):
            return _bad("local:request_log", "request log 5xx count is above the cap", "errors", errors=int(errors))
        return _ok("local:request_log", "request log fresh", age_seconds=int(age), errors_24h=int(errors))


def _manifest_price(body: bytes, path: str) -> Optional[float]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return None

    found = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            url = str(node.get("url") or "")
            if url.endswith(path) and node.get("priceUsd") is not None:
                found.append(node.get("priceUsd"))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return float(found[0]) if found else None


def _mpp_resources(header: str) -> List[str]:
    import base64
    resources = []
    for token in re.findall(r'request="([A-Za-z0-9_\-]+=*)"', header):
        pad = "=" * ((4 - len(token) % 4) % 4)
        try:
            payload = json.loads(base64.urlsafe_b64decode(token + pad))
        except Exception:
            continue
        scope = payload.get("_mppx_scope") if isinstance(payload, dict) else None
        if isinstance(scope, dict) and scope.get("resource"):
            resources.append(str(scope["resource"]))
    return resources


def _file_age_check(check_id: str, spec: Dict[str, Any], now: float, label: str) -> Check:
    path = spec.get("path")
    if not path:
        return _skip(check_id, label + " path not configured")
    if not os.path.isfile(path):
        return _missing(check_id, label + " file is missing")
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return _missing(check_id, label + " file is unreadable")
    stamp = payload.get("last_ok") if isinstance(payload, dict) else None
    if stamp is None and isinstance(payload, dict):
        stamp = payload.get("last_ok_unix")
    age = age_of(stamp, now)
    if age is None:
        return _missing(check_id, label + " has no last_ok timestamp")
    fresh = float(spec.get("fresh_seconds") or 1800)
    known = float(spec.get("known_gap_seconds") or fresh)
    if age <= fresh:
        return _ok(check_id, label + " is fresh", age_seconds=int(age))
    if age <= known:
        return _watch(check_id, label + " is inside the known gap", "known_gap", age_seconds=int(age))
    return _bad(check_id, label + " is older than the known gap", "worsened", age_seconds=int(age))


def _sqlite_age_check(check_id: str, spec: Dict[str, Any], now: float, sql: str, label: str) -> Check:
    path = spec.get("path")
    if not path:
        return _skip(check_id, label + " path not configured")
    if not os.path.isfile(path):
        return _missing(check_id, label + " database is missing")
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
        cols = {row[1] for row in con.execute("PRAGMA table_info(demand)")}
        if "ts" not in cols:
            con.close()
            return _missing(check_id, label + " schema has no ts column")
        newest = con.execute(sql).fetchone()[0]
        con.close()
    except sqlite3.Error:
        return _missing(check_id, label + " database could not be read")
    if newest is None:
        return _missing(check_id, label + " has no rows")
    age = now - float(newest)
    fresh = float(spec.get("fresh_seconds") or 7200)
    known = float(spec.get("known_stall_max_age_seconds") or 21600)
    if age <= fresh:
        return _ok(check_id, label + " is fresh", age_seconds=int(age))
    if age <= known:
        return _watch(check_id, label + " is inside the known stall window", "known_stall", age_seconds=int(age))
    return _bad(check_id, label + " is older than the known stall window", "worsened", age_seconds=int(age))


def reject_banned_config(config: Dict[str, Any]) -> None:
    blob = json.dumps(config).lower()
    for marker in BANNED_MARKERS:
        if marker in blob:
            raise ValueError(f"config mentions banned provider marker {marker}")
    host = (urlparse(str(config.get("base_url") or "")).hostname or "").lower()
    if urlparse(str(config.get("base_url") or "")).scheme != "https" or not host:
        raise ValueError("base_url must be https")
