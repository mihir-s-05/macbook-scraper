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
    @patch("lambda_handler.scrape_all_hardened")
    def test_lambda_cycle_notifies_once_then_dedupes(self, scrape_all, send_ntfy, health):
        deal = Listing(
            source="amazon",
            source_id="B0TEST",
            title="Apple MacBook Air M4 24GB Memory 1TB SSD",
            url="https://www.amazon.com/dp/B0TEST",
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
        client = object()

        first = lh.run_lambda_cycle(settings, client, store)
        second = lh.run_lambda_cycle(settings, client, store)

        self.assertEqual(first["notifications_sent"], 1)
        self.assertEqual(second["notifications_sent"], 0)
        self.assertEqual(send_ntfy.call_count, 1)
        self.assertEqual(health.call_count, 2)

    @patch("lambda_handler.update_source_health", return_value=1)
    @patch("lambda_handler.scrape_all_hardened", return_value=([], {"amazon": "HTTP 503"}))
    def test_lambda_reports_health_notifications(self, scrape_all, health):
        store = lh.DynamoStateStore("unused", table=FakeTable())
        result = lh.run_lambda_cycle(Settings(ntfy_topic="secret"), object(), store)
        self.assertEqual(result["health_notifications_sent"], 1)
        self.assertEqual(result["error_sources"], ["amazon"])

    @patch("lambda_handler.run_lambda_cycle", return_value={"ok": True})
    @patch("lambda_handler.DynamoStateStore")
    def test_lambda_discards_invalid_ntfy_token(self, store_cls, run_cycle):
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
                lh.lambda_handler({}, None)
        finally:
            lh._CLIENT = previous_client
        settings = run_cycle.call_args.args[0]
        self.assertEqual(settings.ntfy_token, "")


if __name__ == "__main__":
    unittest.main()
