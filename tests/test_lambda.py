import os
import unittest
from unittest.mock import patch

import lambda_handler as lh
from macbook_scraper import Listing, Settings


class FakeTable:
    def __init__(self):
        self.item = None

    def get_item(self, **kwargs):
        return {"Item": self.item} if self.item else {}

    def put_item(self, **kwargs):
        self.item = kwargs["Item"]
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}


class LambdaTests(unittest.TestCase):
    def test_dynamo_state_round_trip(self):
        table = FakeTable()
        store = lh.DynamoStateStore("unused", table=table)
        self.assertEqual(store.load(), {"listings": {}})

        expected = {"listings": {"amazon:B0TEST": {"last_price": 1199.0}}}
        store.save(expected)
        self.assertEqual(store.load(), expected)
        self.assertEqual(table.item["pk"], "monitor-state")

    @patch("lambda_handler.update_source_health", return_value=0)
    @patch("lambda_handler.send_ntfy")
    @patch("lambda_handler.scrape_configured_sources")
    def test_lambda_cycle_notifies_once_then_dedupes(self, scrape_all, send_ntfy, health):
        deal = Listing(
            source="bestbuy",
            source_id="6571045",
            title="Apple MacBook Air M4 24GB Memory 1TB SSD",
            url="https://www.bestbuy.com/product/example/sku/6571045",
            price=1199.0,
            memory_gb=24,
            storage_gb=1024,
            chip="M4",
            model="MacBook Air",
            condition="new",
        )
        scrape_all.return_value = ([deal], {})

        store = lh.DynamoStateStore("unused", table=FakeTable())
        settings = Settings(ntfy_topic="secret-topic")
        object.__setattr__(settings, "amazon_enabled", False)
        client = object()

        first = lh.run_lambda_cycle(settings, client, store)
        second = lh.run_lambda_cycle(settings, client, store)

        self.assertEqual(first["notifications_sent"], 1)
        self.assertEqual(second["notifications_sent"], 0)
        self.assertEqual(first["disabled_sources"], ["amazon"])
        self.assertEqual(send_ntfy.call_count, 1)
        self.assertEqual(health.call_count, 2)

    @patch("lambda_handler.update_source_health", return_value=1)
    @patch("lambda_handler.scrape_configured_sources", return_value=([], {"amazon": "HTTP 503"}))
    def test_lambda_reports_health_notifications_when_amazon_enabled(self, scrape_all, health):
        store = lh.DynamoStateStore("unused", table=FakeTable())
        settings = Settings(ntfy_topic="secret")
        object.__setattr__(settings, "amazon_enabled", True)
        result = lh.run_lambda_cycle(settings, object(), store)
        self.assertEqual(result["health_notifications_sent"], 1)
        self.assertEqual(result["error_sources"], ["amazon"])
        self.assertEqual(result["disabled_sources"], [])

    @patch("lambda_handler.retailers.scrape_all_hardened")
    def test_scrape_configured_sources_skips_amazon_when_disabled(self, scrape_all):
        original_urls = list(lh.retailers.AMAZON_URLS)
        seen_urls = []

        def fake_scrape(client, settings):
            seen_urls.append(list(lh.retailers.AMAZON_URLS))
            return [], {"amazon": "parsed 0 listings"}

        scrape_all.side_effect = fake_scrape
        settings = Settings()
        object.__setattr__(settings, "amazon_enabled", False)

        items, errors = lh.scrape_configured_sources(settings, object())

        self.assertEqual(items, [])
        self.assertNotIn("amazon", errors)
        self.assertEqual(seen_urls, [[]])
        self.assertEqual(lh.retailers.AMAZON_URLS, original_urls)

    @patch("lambda_handler.run_lambda_cycle", return_value={"ok": True})
    @patch("lambda_handler.DynamoStateStore")
    def test_lambda_discards_invalid_ntfy_token_and_disables_amazon_by_default(
        self, store_cls, run_cycle
    ):
        previous_client = lh._CLIENT
        lh._CLIENT = object()
        try:
            with patch.dict(
                os.environ,
                {
                    "DYNAMODB_TABLE": "test-table",
                    "NTFY_TOPIC": "secret-topic",
                    "NTFY_TOKEN": "unused",
                },
                clear=False,
            ):
                os.environ.pop("ENABLE_AMAZON", None)
                lh.lambda_handler({}, None)
        finally:
            lh._CLIENT = previous_client
        settings = run_cycle.call_args.args[0]
        self.assertEqual(settings.ntfy_token, "")
        self.assertFalse(settings.amazon_enabled)


if __name__ == "__main__":
    unittest.main()
