import unittest

from macbook_scraper import Listing, Settings
from target_filter import is_target_match


def make_listing(**overrides):
    values = {
        "source": "bestbuy",
        "source_id": "TEST",
        "title": 'Apple MacBook Air 15-inch Laptop - M5 - 24GB Memory - 1TB SSD',
        "url": "https://example.com/macbook",
        "price": 1899.99,
        "memory_gb": 24,
        "storage_gb": 1024,
        "chip": "M5",
        "model": "MacBook Air",
        "condition": "new",
        "in_stock": True,
    }
    values.update(overrides)
    return Listing(**values)


class TargetFilterTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            max_price=1900,
            min_memory_gb=24,
            min_storage_gb=1024,
            allowed_chips=("M4", "M5"),
        )

    def test_accepts_15_inch_m4_or_m5_under_1900(self):
        self.assertTrue(is_target_match(make_listing(chip="M5"), self.settings))
        self.assertTrue(
            is_target_match(
                make_listing(
                    chip="M4",
                    title='Apple MacBook Air 15.3" M4 32GB Memory 2TB SSD',
                    memory_gb=32,
                    storage_gb=2048,
                ),
                self.settings,
            )
        )

    def test_price_ceiling_is_strictly_less_than_1900(self):
        self.assertTrue(is_target_match(make_listing(price=1899.99), self.settings))
        self.assertFalse(is_target_match(make_listing(price=1900.00), self.settings))

    def test_rejects_13_inch_and_macbook_pro(self):
        self.assertFalse(
            is_target_match(
                make_listing(title='Apple MacBook Air 13-inch M5 24GB Memory 1TB SSD'),
                self.settings,
            )
        )
        self.assertFalse(
            is_target_match(
                make_listing(
                    title='Apple MacBook Pro 15-inch M5 24GB Memory 1TB SSD',
                    model="MacBook Pro",
                ),
                self.settings,
            )
        )

    def test_rejects_low_memory_storage_and_other_chips(self):
        self.assertFalse(is_target_match(make_listing(memory_gb=16), self.settings))
        self.assertFalse(is_target_match(make_listing(storage_gb=512), self.settings))
        self.assertFalse(is_target_match(make_listing(chip="M3"), self.settings))

    def test_rejects_non_new_third_party_but_allows_apple_certified_refurb(self):
        self.assertFalse(
            is_target_match(
                make_listing(
                    title='Open Box Apple MacBook Air 15-inch M5 24GB Memory 1TB SSD'
                ),
                self.settings,
            )
        )
        self.assertTrue(
            is_target_match(
                make_listing(
                    source="apple_refurb",
                    title="Refurbished 15-inch MacBook Air Apple M4 Chip 24GB 1TB SSD",
                    condition="apple_certified_refurbished",
                    chip="M4",
                ),
                self.settings,
            )
        )


if __name__ == "__main__":
    unittest.main()
