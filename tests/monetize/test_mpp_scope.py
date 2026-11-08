"""MPP challenge scope is the real request path, for charge and session.

No payment is broadcast. A dummy credential is echoed only to prove a credential
minted for one route is rejected on another before settlement.
"""
from __future__ import annotations

import os
import sys
import tempfile


def _http(method: str, path: str, query: str = "", route: str | None = None):
    from starlette.requests import Request

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": query.encode("ascii"),
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("scraplingdata.xyz", 443),
        "root_path": "",
    }
    if route:
        scope["route"] = type("Route", (), {"path": route})()
    return Request(scope)


def _scopes(header: str):
    from mpp import Challenge
    from mpp.client.transport import _payment_challenges

    found = []
    for field in _payment_challenges(header):
        challenge = Challenge.from_www_authenticate(field)
        scope = (challenge.request or {}).get("_mppx_scope") or {}
        found.append(
            {
                "intent": challenge.intent,
                "resource": scope.get("resource"),
                "query": scope.get("query"),
                "http_method": scope.get("http_method"),
                "route": scope.get("route"),
                "amount": (challenge.request or {}).get("amount"),
            }
        )
    return found


def _echo(header: str, intent: str) -> str:
    from mpp import Challenge, Credential

    from mpp.client.transport import _payment_challenges

    for field in _payment_challenges(header):
        challenge = Challenge.from_www_authenticate(field)
        if challenge.intent != intent:
            continue
        credential = Credential(
            challenge=challenge.to_echo(),
            payload={"type": "hash", "hash": "0x" + "ab" * 32},
            source="0x" + "11" * 20,
        )
        return credential.to_authorization()
    raise AssertionError(f"no {intent} challenge")


def main() -> None:
    vault = tempfile.mkdtemp(prefix="mpp-scope-")
    os.environ["SCRAPLING_VAULT_DIR"] = vault
    os.environ["MPP_SECRET_KEY"] = "test-secret-not-a-real-key"
    os.environ["SCRAPLING_MPP_SESSION"] = "1"
    os.environ["MPP_SESSION_OPERATOR_KEY"] = "0x" + "44" * 32
    os.environ.pop("SCRAPLING_STANDBY", None)

    from scrapling.monetize.mpp_bridge import MppDoor

    door = MppDoor("0x72C80C01fD89Bac74f47cf604b61ACd119c9BCdb")
    assert door.enabled and door.sessions_enabled and door._api == "v20", (
        door.enabled,
        door.sessions_enabled,
        door._api,
    )

    cases = [
        (_http("GET", "/v1/company", "q=aave", "/v1/company"), "/v1/company", "q=aave", "0.003", "3000"),
        (
            _http(
                "GET",
                "/v1/wallet-label",
                "address=0x0000000000000000000000000000000000000000",
                "/v1/wallet-label",
            ),
            "/v1/wallet-label",
            "address=0x0000000000000000000000000000000000000000",
            "0.002",
            "2000",
        ),
        (
            _http("GET", "/v1/lookup/binance-flows", "", "/v1/lookup/binance-flows"),
            "/v1/lookup/binance-flows",
            None,
            "0.02",
            "20000",
        ),
        (
            _http("POST", "/v1/batch-scrape", "", "/v1/batch-scrape"),
            "/v1/batch-scrape",
            None,
            "0.005",
            "5000",
        ),
        (
            _http("GET", "/v1/a%2Fb", "q=aave&x=1", "/v1/a%2Fb"),
            "/v1/a%2Fb",
            "q=aave&x=1",
            "0.003",
            "3000",
        ),
    ]

    issued = {}
    for req, resource, query, usd, atomic in cases:
        header = door.challenge_header(float(usd), "scope test", http_request=req)
        assert header, resource
        rows = _scopes(header)
        intents = {row["intent"] for row in rows}
        assert intents == {"charge", "session"}, (resource, intents)
        for row in rows:
            assert row["resource"] == resource, row
            assert row["resource"] != "/", row
            assert row["query"] == query, row
            assert row["http_method"] == req.method, row
            assert row["route"] == resource, row
            assert row["amount"] == atomic, row
        issued[resource] = (header, req, float(usd))

    # Query is its own scope field. A different query is not the same challenge.
    company_a = issued["/v1/company"]
    other_q = _http("GET", "/v1/company", "q=compound", "/v1/company")
    other_header = door.challenge_header(0.003, "scope test", http_request=other_q)
    other_rows = _scopes(other_header)
    assert all(row["resource"] == "/v1/company" and row["query"] == "q=compound" for row in other_rows)

    # No query key when the request has none (binance-flows above). Distinct from "?q=".
    bare = _scopes(door.challenge_header(0.003, "scope test", http_request=_http("GET", "/v1/company", "", "/v1/company")))
    assert all(row["query"] is None and row["resource"] == "/v1/company" for row in bare)

    def present(auth: str, req, usd: float) -> tuple[str, list[dict]]:
        """Replay a credential. Returns (problem detail, scopes of the 402 it issues)."""
        import asyncio

        ok, _info, err = door.verify(auth, usd, "scope test", http_request=req)
        assert ok is False and err, (ok, err)

        async def _once():
            res = await door._handler(usd, "scope test")(await door._engine_request(req, auth))
            assert res.status != 200
            body = (res.challenge.body or b"").decode()
            www = res.challenge.headers.get("www-authenticate") or ""
            return body, _scopes(www)

        return asyncio.run(_once())

    def rejected_for(auth: str, req, usd: float, resource: str, query) -> None:
        # A credential for another scope is not selected. The server answers with a
        # fresh challenge for THIS request and does not settle the foreign one.
        body, rows = present(auth, req, usd)
        assert "Payment is required" in body, body[:400]
        assert "verification-failed" not in body and "invalid-payload" not in body, body[:400]
        assert rows, body[:400]
        for row in rows:
            assert row["resource"] == resource, row
            assert row["query"] == query, row
            assert row["http_method"] == req.method, row

    # Cross-route and cross-query: company charge and session must not pay another route.
    for intent in ("charge", "session"):
        auth = _echo(company_a[0], intent)
        rejected_for(auth, issued["/v1/wallet-label"][1], 0.002, "/v1/wallet-label",
                     "address=0x0000000000000000000000000000000000000000")
        rejected_for(auth, issued["/v1/lookup/binance-flows"][1], 0.02, "/v1/lookup/binance-flows", None)
        rejected_for(auth, other_q, 0.003, "/v1/company", "q=compound")
        rejected_for(auth, _http("POST", "/v1/company", "q=aave", "/v1/company"), 0.003, "/v1/company", "q=aave")

    # Same scope reaches payload checks (still unpaid: the proof is fake, no broadcast).
    for intent, needle in (
        ("charge", "Hash credential source is invalid"),
        ("session", "Credential payload is invalid"),
    ):
        body, rows = present(_echo(company_a[0], intent), company_a[1], 0.003)
        assert needle in body, body[:500]
        assert all(row["resource"] == "/v1/company" and row["query"] == "q=aave" for row in rows)

    # Encoded path stays distinct from the decoded slash form.
    encoded = issued["/v1/a%2Fb"]
    slash = _http("GET", "/v1/a/b", "q=aave&x=1", "/v1/a/b")
    slash_rows = _scopes(door.challenge_header(0.003, "scope test", http_request=slash))
    assert all(row["resource"] == "/v1/a/b" for row in slash_rows)
    for intent in ("charge", "session"):
        rejected_for(_echo(encoded[0], intent), slash, 0.003, "/v1/a/b", "q=aave&x=1")

    # Parameterized route template is part of the scope, and the resource is the real path.
    buy_a = _http("GET", "/v1/datasets/alpha/buy", "format=json", "/v1/datasets/{dataset_id}/buy")
    buy_b = _http("GET", "/v1/datasets/beta/buy", "format=json", "/v1/datasets/{dataset_id}/buy")
    header_a = door.challenge_header(0.01, "scope test", http_request=buy_a)
    rows_a = _scopes(header_a)
    assert rows_a and all(
        row["resource"] == "/v1/datasets/alpha/buy" and row["route"] == "/v1/datasets/{dataset_id}/buy"
        for row in rows_a
    )
    for intent in ("charge", "session"):
        rejected_for(_echo(header_a, intent), buy_b, 0.01, "/v1/datasets/beta/buy", "format=json")

    door.stop()
    print("mpp scope ok", len(cases), "routes; charge+session; cross-route rejected")
    os._exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        os._exit(1)
