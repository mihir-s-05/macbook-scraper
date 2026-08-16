import unittest
from unittest.mock import patch

from macbook_scraper import Settings, is_match
from retailer_hardening import (
    amazon_block_reason,
    bestbuy_api_url,
    scrape_bestbuy_api,
    update_source_health,
)


class RetailerHardeningTests(unittest.TestCase):
    def test_bestbuy_api_parser(self):
        payload = '''{"products":[{
          "sku": 6600001,
          "name": "Apple - MacBook Air 13-inch M5 - 24GB Memory - 1TB SSD",
          "salePrice": 1199.0,
          "onlineAvailability": true
        }]}'''
        item = scrape_bestbuy_api(payload)[0]
        self.assertEqual(item.source_id, "6600001")
        self.assertEqual(item.price, 1199.0)
        self.assertEqual(item.url, "https://www.bestbuy.com/site/-/6600001.p?skuId=6600001")
        self.assertTrue(is_match(item, Settings()))

    def test_bestbuy_api_url(self):
        url = bestbuy_api_url("secret")
        self.assertIn("products(search=MacBook&manufacturer=Apple&active=true)", url)
        self.assertIn("apiKey=secret", url)
        self.assertIn("pageSize=100", url)

    def test_amazon_block_detection(self):
        self.assertEqual(
            amazon_block_reason("Sorry, we just need to make sure you're not a robot"),
            "captcha",
        )
        self.assertIsNone(amazon_block_reason("normal product results"))

    @patch("retailer_hardening._send_ntfy_message")
    def test_source_health_alerts_then_recovers(self, send_message):
        settings = Settings(ntfy_topic="secret")
        object.__setattr__(settings, "source_alert_after", 3)
        object.__setattr__(settings, "source_realert_hours", 6.0)
        state = {}
        now = 1000.0
        for i in range(2):
            self.assertEqual(
                update_source_health(settings, state, {"amazon": "HTTP 503"}, now + i),
                0,
            )
        self.assertEqual(
            update_source_health(settings, state, {"amazon": "HTTP 503"}, now + 2),
            1,
        )
        self.assertIn("Amazon", send_message.call_args.kwargs["title"])
        self.assertEqual(update_source_health(settings, state, {}, now + 3), 1)
        self.assertIn("recovered", send_message.call_args.kwargs["title"].lower())


if __name__ == "__main__":
    unittest.main()
