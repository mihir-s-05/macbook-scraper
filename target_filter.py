from __future__ import annotations

import re

from macbook_scraper import Listing, Settings

# Retailer titles use several visually identical Unicode hyphens. Normalize them
# before matching so Apple's "15‑inch" (U+2011) is treated like "15-inch".
UNICODE_DASH_RE = re.compile(r"[\u2010\u2011\u2012\u2013\u2014\u2212]")
FIFTEEN_INCH_RE = re.compile(r"(?<!\d)15(?:\.3)?\s*(?:[- ]?inch|[\"”])", re.I)
BAD_CONDITION_TERMS = (
    "renewed",
    "refurbished",
    "open-box",
    "open box",
    "pre-owned",
    "preowned",
    "used",
)


def _normalized_title(title: str) -> str:
    return UNICODE_DASH_RE.sub("-", title).replace("\u00a0", " ")


def is_target_match(item: Listing, settings: Settings) -> bool:
    """Return True only for the user's focused 15-inch MacBook Air target.

    Target:
      * MacBook Air only
      * 15-inch / 15.3-inch display
      * M4 or M5
      * >=24 GB unified memory
      * >=1 TB SSD
      * strictly less than the configured price ceiling
      * new from third-party retailers, or Apple Certified Refurbished
    """

    generation_match = re.match(r"M\d+", item.chip.upper())
    generation = generation_match.group(0) if generation_match else ""
    normalized_title = _normalized_title(item.title)
    title_lower = normalized_title.lower()

    if item.source == "apple_refurb":
        condition_ok = item.condition == "apple_certified_refurbished"
    else:
        condition_ok = item.condition == "new" and not any(
            term in title_lower for term in BAD_CONDITION_TERMS
        )

    return (
        item.in_stock
        and condition_ok
        and item.model == "MacBook Air"
        and bool(FIFTEEN_INCH_RE.search(normalized_title))
        and generation in settings.allowed_chips
        and item.memory_gb >= settings.min_memory_gb
        and item.storage_gb >= settings.min_storage_gb
        and item.price < settings.max_price
    )
