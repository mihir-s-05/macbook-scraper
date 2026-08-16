import unittest

from macbook_scraper import Settings, is_match, parse_specs, scrape_amazon, scrape_apple, scrape_bestbuy, scrape_bh


class ScraperTests(unittest.TestCase):
    def test_parse_specs(self):
        memory, storage, chip, model = parse_specs('Apple 13" MacBook Air (M5) 24GB Unified RAM | 1TB SSD')
        self.assertEqual(memory, 24)
        self.assertEqual(storage, 1024)
        self.assertEqual(chip, "M5")
        self.assertEqual(model, "MacBook Air")

    def test_filter(self):
        html = '''<div data-component-type="s-search-result" data-asin="B0TEST">
          <h2><a href="/dp/B0TEST">Apple MacBook Air M4 24GB Memory 1TB SSD</a></h2>
          <span class="a-price"><span class="a-offscreen">$1,199.00</span></span>
        </div>'''
        listing = scrape_amazon(html)[0]
        self.assertTrue(is_match(listing, Settings()))

    def test_reject_renewed(self):
        html = '''<div data-component-type="s-search-result" data-asin="B0TEST">
          <h2><a href="/dp/B0TEST">Apple MacBook Air M4 24GB Memory 1TB SSD (Renewed)</a></h2>
          <span class="a-price"><span class="a-offscreen">$899.00</span></span>
        </div>'''
        listing = scrape_amazon(html)[0]
        self.assertFalse(is_match(listing, Settings()))

    def test_apple_bootstrap(self):
        html = '''<script>window.REFURB_GRID_BOOTSTRAP = {"tiles":[{
          "partNumber":"TEST1",
          "title":"Refurbished 13-inch MacBook Air Apple M4 Chip",
          "productDetailsUrl":"/shop/product/TEST1",
          "price":{"currentPrice":{"raw_amount":"$1,199.00"}},
          "filters":{"dimensions":{"dimensionCapacity":"1tb","refurbClearModel":"macbookair","tsMemorySize":"24gb"}}
        }]};</script>'''
        listing = scrape_apple(html)[0]
        self.assertEqual(listing.memory_gb, 24)
        self.assertEqual(listing.storage_gb, 1024)
        self.assertEqual(listing.price, 1199.0)
        self.assertTrue(is_match(listing, Settings()))

    def test_bestbuy_prefers_new_price(self):
        html = '''<ul><li class="sku-item">
          <h4><a href="/site/apple-macbook/123.p?skuId=123">Apple MacBook Air M5 - 24GB Memory - 1TB SSD</a></h4>
          <div class="priceView-customer-price"><span>$1,199.00</span></div>
          <div>More Buying Options from $999.00</div><div>Get it tomorrow</div>
        </li></ul>'''
        listing = scrape_bestbuy(html)[0]
        self.assertEqual(listing.price, 1199.0)
        self.assertTrue(is_match(listing, Settings()))

    def test_bh_prefers_new_price(self):
        html = '''<div class="card"><h3><a href="/c/product/123/REG/apple_macbook.html">Apple 13&quot; MacBook Air (M4)</a></h3>
          <div>24GB Unified RAM | 1TB SSD</div>
          <div data-selenium="uppedDecimalPriceFirst">$1,199.00</div>
          <div>Used from $899.00</div><div>In Stock</div></div>'''
        listing = scrape_bh(html)[0]
        self.assertEqual(listing.price, 1199.0)
        self.assertTrue(is_match(listing, Settings()))


if __name__ == "__main__":
    unittest.main()
