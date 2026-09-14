from __future__ import annotations

import random
import re
import time
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from macbook_scraper import (
    HAS_CURL_CFFI,
    Client,
    Listing,
    closest_price_container,
    http,
    listing,
    stable_id,
)

EXTRA_SOURCE_NAMES = {
    "adorama": "Adorama",
    "abt": "Abt Electronics",
    "microcenter": "Micro Center",
}

SEARCH_TERMS = (
    "MacBook Air 15 M4",
    "MacBook Air 15 M5",
)

ADORAMA_URLS = [
    f"https://www.adorama.com/l/?searchinfo={quote_plus(term)}" for term in SEARCH_TERMS
]
ABT_URLS = [
    f"https://www.abt.com/resources/pages/search.php?keywords={quote_plus(term)}"
    for term in SEARCH_TERMS
]
# Micro Center's Apple laptop category is server-rendered. Stock can be store-specific,
# so alerts link to the product page for the user to verify local availability.
MICROCENTER_URLS = [
    "https://www.microcenter.com/search/search_results.aspx?"
    "fq=Category%3ALaptops%2FNotebooks%7C618%2CBrand%3AApple&"
    "rpp=96&sortby=match"
]

_OUT_RE = re.compile(
    r"sold out|out of stock|temporarily unavailable|discontinued|no longer available",
    re.I,
)


def _request_headers() -> dict[str, str]:
    return {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Upgrade-Insecure-Requests": "1",
    }


def _fetch_html(client: Client, url: str, profile: str) -> str:
    if not HAS_CURL_CFFI:
        return client.get(url)
    session = http.Session(impersonate=profile)
    response = session.get(url, timeout=client.timeout, headers=_request_headers())
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}")
    return response.text


def _money_after_label(text: str, labels: tuple[str, ...]) -> float | None:
    for label in labels:
        match = re.search(
            rf"{re.escape(label)}\s*:?[\s\u00a0]*\$\s*([0-9]{{1,3}}(?:,[0-9]{{3}})*(?:\.\d{{2}})?)",
            text,
            re.I,
        )
        if match:
            return float(match.group(1).replace(",", ""))
    return None


def _structured_price(card) -> float | None:
    # Prefer explicit machine-readable/current-price fields. We intentionally do
    # not fall back to the minimum dollar amount because retailer cards may also
    # advertise cheaper used/open-box inventory.
    for selector in (
        "[itemprop='price']",
        "meta[itemprop='price']",
        "[data-testid*='price']",
        "[data-price]",
        ".product-price",
        ".sale-price",
        ".price-current",
    ):
        element = card.select_one(selector)
        if element is None:
            continue
        raw = element.get("content") or element.get("data-price") or element.get_text(" ", strip=True)
        match = re.search(r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.\d{2})?)", str(raw))
        if match:
            value = float(match.group(1).replace(",", ""))
            if value >= 500:
                return value
    return None


def _title_for_anchor(anchor) -> str:
    title = anchor.get_text(" ", strip=True)
    if "macbook air" in title.lower():
        return title
    heading = anchor.find_parent(["h1", "h2", "h3", "h4"])
    if heading:
        candidate = heading.get_text(" ", strip=True)
        if "macbook air" in candidate.lower():
            return candidate
    parent = anchor.parent
    if parent:
        candidate = parent.get_text(" ", strip=True)
        if "macbook air" in candidate.lower() and len(candidate) < 1000:
            return candidate
    return ""


def _parse_cards(
    html: str,
    *,
    source: str,
    base_url: str,
    anchor_selector: str,
    price_labels: tuple[str, ...],
) -> list[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, Listing] = {}

    for anchor in soup.select(anchor_selector):
        href = str(anchor.get("href") or "")
        if not href:
            continue
        if source == "adorama" and ("/used-" in href.lower() or href.lower().startswith("/used")):
            continue

        title = _title_for_anchor(anchor)
        if "macbook air" not in title.lower():
            continue

        card = closest_price_container(anchor)
        if card is None:
            continue
        text = card.get_text(" ", strip=True)
        price = _money_after_label(text, price_labels) or _structured_price(card)
        if price is None:
            continue

        # Micro Center spells the storage field out as "Solid State Drive".
        # Normalize that phrase so the shared spec parser recognizes 1TB/2TB/etc.
        spec_text = re.sub(r"\bSolid State Drive\b", "SSD", text, flags=re.I)

        url = urljoin(base_url, href)
        source_id = stable_id(url.split("?")[0])
        found[source_id] = listing(
            source,
            source_id,
            title,
            url,
            price,
            text=spec_text,
            condition="new",
            in_stock=not bool(_OUT_RE.search(text)),
        )

    return list(found.values())


def scrape_adorama(html: str) -> list[Listing]:
    return _parse_cards(
        html,
        source="adorama",
        base_url="https://www.adorama.com",
        anchor_selector="a[href*='/p/']",
        price_labels=("Our Price", "Sale Price", "Price"),
    )


def scrape_abt(html: str) -> list[Listing]:
    return _parse_cards(
        html,
        source="abt",
        base_url="https://www.abt.com",
        anchor_selector="a[href*='/p/']",
        price_labels=("Your Price", "Sale Price", "Price"),
    )


def scrape_microcenter(html: str) -> list[Listing]:
    return _parse_cards(
        html,
        source="microcenter",
        base_url="https://www.microcenter.com",
        anchor_selector="a[href*='/product/']",
        price_labels=("Our price", "Today's price", "Todays price", "Sale Price"),
    )


def _scrape_source(
    client: Client,
    source: str,
    urls: list[str],
    parser,
) -> tuple[list[Listing], str | None]:
    found: dict[str, Listing] = {}
    failures: list[str] = []

    for url in urls:
        parsed_this_url = False
        for profile in ("chrome", "safari"):
            try:
                html = _fetch_html(client, url, profile)
                items = parser(html)
                if items:
                    parsed_this_url = True
                    for item in items:
                        old = found.get(item.source_id)
                        if old is None or item.price < old.price:
                            found[item.source_id] = item
                    break
                failures.append(f"{profile}: parsed 0 product cards")
            except Exception as exc:
                failures.append(f"{profile}: {exc}")
        if not parsed_this_url:
            time.sleep(0.25)
        time.sleep(random.uniform(0.35, 0.7))

    if found:
        return list(found.values()), None
    return [], "; ".join(failures[-4:]) or "parsed 0 product cards"


def scrape_extra_sources(client: Client) -> tuple[list[Listing], dict[str, str]]:
    all_items: list[Listing] = []
    errors: dict[str, str] = {}
    jobs = (
        ("adorama", ADORAMA_URLS, scrape_adorama),
        ("abt", ABT_URLS, scrape_abt),
        ("microcenter", MICROCENTER_URLS, scrape_microcenter),
    )

    for source, urls, parser in jobs:
        items, error = _scrape_source(client, source, urls, parser)
        if items:
            all_items.extend(items)
        if error:
            errors[source] = error

    return all_items, errors
