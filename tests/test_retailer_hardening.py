import unittest
from unittest.mock import patch

from macbook_scraper import Settings, is_match
from retailer_hardening import (
    _send_ntfy_message,
    amazon_block_reason,
    bestbuy_api_url,
    bestbuy_category_url,
    scrape_bestbuy_api,
    scrape_bestbuy_modern,
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

    def test_bestbuy_category_url_uses_server_rendered_facets(self):
        url = bestbuy_category_url(24)
        self.assertIn("browsedCategory=pcmcat247400050001", url)
        self.assertIn("24+gigabytes", url)
        self.assertIn("1+TB+-+1.9+TB", url)
        self.assertIn("intl=nosplash", url)

    def test_bestbuy_modern_product_url_parser(self):
        html = '''<div class="product-card">
          <h2><a href="/product/apple-macbook-air-m5/JJGCQLKHZ5/sku/6571045">Apple - MacBook Air 15-inch Laptop - M5 chip - 24GB Memory - 1TB SSD - Midnight</a></h2>
          <div data-testid="customer-price">$1,199.00</div>
          <div>The comparable value is $1,499.00</div>
          <div>More options from $1,099.00</div>
        </div>'''
        item = scrape_bestbuy_modern(html)[0]
        self.assertEqual(item.source_id, "6571045")
        self.assertEqual(item.price, 1199.0)
        self.assertTrue(item.url.endswith("/sku/6571045"))
        self.assertTrue(is_match(item, Settings()))

    def test_amazon_block_detection(self):
        self.assertEqual(
            amazon_block_reason("Sorry, we just need to make sure you're not a robot"),
            "captcha",
        )
        self.assertIsNone(amazon_block_reason("normal product results"))

    @patch("retailer_hardening.http.post")
    def test_ntfy_ignores_non_access_token_value(self, post):
        post.return_value.status_code = 200
        settings = Settings(ntfy_topic="secret", ntfy_token="unused")
        _send_ntfy_message(
            settings,
            title="test",
            body="test body",
            priority="high",
            tags="warning",
        )
        headers = post.call_args.kwargs["headers"]
        self.assertNotIn("Authorization", headers)

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
