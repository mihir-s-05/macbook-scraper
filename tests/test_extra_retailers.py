import unittest

from extra_retailers import scrape_abt, scrape_adorama, scrape_microcenter
from macbook_scraper import Settings
from target_filter import is_target_match


class ExtraRetailerTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            max_price=1900,
            min_memory_gb=24,
            min_storage_gb=1024,
            allowed_chips=("M4", "M5"),
        )

    def test_adorama_uses_new_price_not_used_price(self):
        html = '''<div class="product-card">
          <h3><a href="/apple-macbook-air-15-inch-m5/p/acmdvc4lla">Apple MacBook Air 15.3&quot; M5 24GB Memory 1TB SSD</a></h3>
          <div>Our Price: $1,899.00</div>
          <div>See all Used options from $1,499.00</div>
        </div>'''
        item = scrape_adorama(html)[0]
        self.assertEqual(item.price, 1899.0)
        self.assertEqual(item.condition, "new")
        self.assertTrue(is_target_match(item, self.settings))

    def test_adorama_skips_used_listing_links(self):
        html = '''<div><a href="/used-apple-macbook-air-15/p/culatest">Used Apple MacBook Air 15.3&quot; M5 24GB Memory 1TB SSD</a><div>Price: $1,399.00</div></div>'''
        self.assertEqual(scrape_adorama(html), [])

    def test_abt_parses_current_new_price(self):
        html = '''<div class="product-card">
          <h3><a href="/Apple-MacBook-Air-Laptop-15-M5/p/240237.html">Apple MacBook Air Laptop 15.3-Inch M5 24GB RAM 1TB SSD</a></h3>
          <div>Your Price: $1,849.00</div>
          <div>Regular Price: $1,999.00</div>
        </div>'''
        item = scrape_abt(html)[0]
        self.assertEqual(item.price, 1849.0)
        self.assertTrue(is_target_match(item, self.settings))

    def test_microcenter_parses_price_and_stock(self):
        html = '''<div class="product-card">
          <h2><a href="/product/693805/macbook-air-15-m4">Apple MacBook Air 15&quot; M4 24GB Unified Memory 1TB Solid State Drive</a></h2>
          <div>Our price $1,799.99</div><div>1 IN STOCK</div>
        </div>'''
        item = scrape_microcenter(html)[0]
        self.assertAlmostEqual(item.price, 1799.99)
        self.assertTrue(item.in_stock)
        self.assertTrue(is_target_match(item, self.settings))

    def test_microcenter_sold_out_is_not_match(self):
        html = '''<div class="product-card">
          <h2><a href="/product/693805/macbook-air-15-m4">Apple MacBook Air 15&quot; M4 24GB Unified Memory 1TB Solid State Drive</a></h2>
          <div>Our price $1,199.99</div><div>SOLD OUT at Store</div>
        </div>'''
        item = scrape_microcenter(html)[0]
        self.assertFalse(item.in_stock)
        self.assertFalse(is_target_match(item, self.settings))


if __name__ == "__main__":
    unittest.main()
