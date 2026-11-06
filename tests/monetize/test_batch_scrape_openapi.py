"""GET /v1/batch-scrape is documented as the 402 probe it is. POST is unchanged."""
from __future__ import annotations

import unittest
from pathlib import Path

from scrapling.monetize.batch_scrape_openapi import (
    GET_DESCRIPTION,
    apply_batch_scrape_openapi,
)

GATEWAY = Path(__file__).resolve().parents[2] / "scrapling" / "monetize" / "gateway.py"


class BatchScrapeOpenAPITests(unittest.TestCase):
    def test_get_docs_match_runtime_and_post_is_untouched(self) -> None:
        post = {
            "summary": "paid post",
            "parameters": [{"name": "leftover"}],
            "requestBody": {"required": True},
            "responses": {"200": {"description": "scraped"}},
        }
        paths = {"/v1/batch-scrape": {"post": post, "get": {"responses": {"200": {"description": "wrong"}}}}}
        apply_batch_scrape_openapi(paths)
        get = paths["/v1/batch-scrape"]["get"]
        self.assertIs(paths["/v1/batch-scrape"]["post"], post)
        self.assertEqual(post["summary"], "paid post")
        self.assertEqual(post["responses"]["200"]["description"], "scraped")
        self.assertEqual(get["parameters"], [])
        self.assertNotIn("requestBody", get)
        self.assertNotIn("200", get["responses"])
        self.assertIn("402", get["responses"])
        self.assertIn("POST", get["description"])
        self.assertIn("urls", get["description"])
        self.assertFalse(get["x-payment-info"]["payable"])
        self.assertEqual(get["x-payment-info"]["scrape"]["method"], "POST")
        self.assertIn("urls", GET_DESCRIPTION)

    def test_handler_still_returns_402_and_is_in_the_schema(self) -> None:
        text = GATEWAY.read_text(encoding="utf-8")
        start = text.index("async def batch_scrape_get")
        # The next route is the POST handler. Nothing between them may scrape.
        end = text.index("async def batch_scrape_endpoint", start)
        block = text[start:end]
        decorator = text[max(0, start - 400):start]
        self.assertIn("return _payment_required(", block)
        self.assertNotIn("body.urls", block)
        self.assertIn("/v1/batch-scrape", decorator)
        self.assertNotIn("include_in_schema=False", decorator)
        self.assertIn("summary=GET_SUMMARY", decorator)
        self.assertIn("apply_batch_scrape_openapi(paths)", text)


if __name__ == "__main__":
    unittest.main()
