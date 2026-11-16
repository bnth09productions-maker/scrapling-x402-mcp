"""/.well-known/openapi.json returns the same document as /openapi.json."""
from __future__ import annotations

import unittest
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from scrapling.monetize.openapi_alias import (
    CANONICAL_PATH,
    WELL_KNOWN_PATH,
    manifest_endpoints,
    register_well_known_openapi,
)

GATEWAY = Path(__file__).resolve().parents[2] / "scrapling" / "monetize" / "gateway.py"


def _app() -> FastAPI:
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    @app.get(CANONICAL_PATH, include_in_schema=False)
    async def openapi_json(request: Request) -> JSONResponse:
        host = request.headers.get("host") or "scraplingdata.xyz"
        return JSONResponse(
            {
                "openapi": "3.1.0",
                "info": {"title": "Scrapling x402 Data Store", "x-guidance": "mpp and x402"},
                "servers": [{"url": f"https://{host}"}],
                "paths": {"/v1/company": {"get": {}}, "/openapi.json": {"get": {}}},
            }
        )

    register_well_known_openapi(app, openapi_json)
    return app


class OpenAPIAliasTests(unittest.TestCase):
    def test_both_urls_return_the_same_schema(self) -> None:
        client = TestClient(_app())
        headers = {"host": "scraplingdata.xyz"}
        canonical = client.get(CANONICAL_PATH, headers=headers, follow_redirects=False)
        alias = client.get(WELL_KNOWN_PATH, headers=headers, follow_redirects=False)
        self.assertEqual(canonical.status_code, 200)
        self.assertEqual(alias.status_code, 200)
        self.assertTrue(canonical.headers["content-type"].startswith("application/json"))
        self.assertTrue(alias.headers["content-type"].startswith("application/json"))
        self.assertIsNone(canonical.headers.get("location"))
        self.assertIsNone(alias.headers.get("location"))
        self.assertEqual(canonical.json(), alias.json())
        self.assertEqual(canonical.json()["openapi"], "3.1.0")
        self.assertEqual(canonical.json()["paths"]["/v1/company"], alias.json()["paths"]["/v1/company"])

    def test_following_redirects_does_not_loop(self) -> None:
        client = TestClient(_app())
        response = client.get(WELL_KNOWN_PATH, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["openapi"], "3.1.0")
        self.assertLessEqual(len(response.history), 1)

    def test_alias_is_not_inside_the_generated_paths(self) -> None:
        spec = _app().openapi()
        self.assertNotIn(WELL_KNOWN_PATH, spec.get("paths", {}))

    def test_discovery_keeps_the_canonical_path(self) -> None:
        endpoints = manifest_endpoints()
        self.assertEqual(endpoints["openapi"], "/openapi.json")
        self.assertEqual(endpoints["openapiWellKnown"], "/.well-known/openapi.json")
        text = GATEWAY.read_text(encoding="utf-8")
        self.assertIn("register_well_known_openapi(app, openapi_json)", text)
        self.assertIn('url": f"{base_url}/openapi.json"', text)
        self.assertIn("return JSONResponse(spec)", text)


if __name__ == "__main__":
    unittest.main()
