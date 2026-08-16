from __future__ import annotations

import json
import logging
import random
import time
from typing import Any
from urllib.parse import quote, quote_plus, urlencode, urljoin

from bs4 import BeautifulSoup

from macbook_scraper import (
    APPLE_URL,
    BH_URLS,
    HAS_CURL_CFFI,
    MONEY_RE,
    Client,
    Listing,
    Settings,
    closest_price_container,
    http,
    listing,
    scrape_amazon,
    scrape_apple,
    scrape_bestbuy,
    scrape_bh,
    stable_id,
)

LOG = logging.getLogger("macbook-scraper.retailers")

SOURCE_NAMES = {
    "apple_refurb": "Apple Certified Refurbished",
    "bh": "B&H Photo",
    "bestbuy": "Best Buy",
    "amazon": "Amazon",
}
SOURCE_ORDER = tuple(SOURCE_NAMES)


def bestbuy_category_url(memory_gb: int) -> str:
    # Best Buy's current MacBook category/facet pages are server-rendered, unlike
    # the free-text search response that often returns an empty JS shell to AWS.
    qp = (
        f"systemmemoryram_facet=RAM~{memory_gb} gigabytes^"
        "totalstoragecapacityrange_facet=Total Storage Capacity~1 TB - 1.9 TB"
    )
    params = {
        "browsedCategory": "pcmcat247400050001",
        "id": "pcat17071",
        "qp": qp,
        "st": "categoryid$pcmcat247400050001",
        "intl": "nosplash",
    }
    return "https://www.bestbuy.com/site/searchpage.jsp?" + urlencode(params)


BESTBUY_URLS = [bestbuy_category_url(memory) for memory in (24, 32)]
AMAZON_URLS = [
    f"https://www.amazon.com/s?k={quote_plus(q)}&language=en_US"
    for q in ("Apple MacBook 24GB 1TB", "Apple MacBook 32GB 1TB")
]


def _request_headers() -> dict[str, str]:
    return {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Upgrade-Insecure-Requests": "1",
    }


def _add_items(found: dict[str, Listing], items: list[Listing]) -> None:
    for item in items:
        if item.key not in found or item.price < found[item.key].price:
            found[item.key] = item


def bestbuy_api_url(api_key: str) -> str:
    base = "https://api.bestbuy.com/v1/products(search=MacBook&manufacturer=Apple&active=true)"
    params = {
        "format": "json",
        "show": "sku,name,salePrice,onlineAvailability",
        "sort": "salePrice.asc",
        "pageSize": "100",
        "apiKey": api_key,
    }
    return f"{base}?{urlencode(params)}"


def scrape_bestbuy_api(payload: str) -> list[Listing]:
    data = json.loads(payload)
    out: list[Listing] = []
    for product in data.get("products", []):
        title = str(product.get("name") or "").strip()
        sku = str(product.get("sku") or "").strip()
        price = product.get("salePrice")
        if not title or not sku or price in (None, "") or "macbook" not in title.lower():
            continue
        out.append(
            listing(
                "bestbuy",
                sku,
                title,
                f"https://www.bestbuy.com/site/-/{sku}.p?skuId={sku}",
                float(price),
                text=title,
                in_stock=bool(product.get("onlineAvailability", True)),
            )
        )
    return out


def fetch_bestbuy_api(client: Client, api_key: str) -> list[Listing]:
    # Avoid Client.get here because its HTTP error text contains the full URL,
    # which would expose the API key in CloudWatch.
    response = client.session.get(
        bestbuy_api_url(api_key),
        timeout=client.timeout,
        headers={"Accept": "application/json", "Accept-Language": "en-US,en;q=0.9"},
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Best Buy Products API HTTP {response.status_code}")
    items = scrape_bestbuy_api(response.text)
    if not items:
        raise RuntimeError("Best Buy Products API parsed 0 MacBook listings")
    return items


def _first_money(text: str) -> float | None:
    match = MONEY_RE.search(text)
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def scrape_bestbuy_modern(html: str) -> list[Listing]:
    """Parse both Best Buy's current /product/.../sku/<id> cards and legacy cards."""
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, Listing] = {}
    anchors = soup.select(
        "a[href*='/product/'][href*='/sku/'], "
        "a[href*='/site/'][href*='.p']"
    )
    for anchor in anchors:
        title = anchor.get_text(" ", strip=True)
        if "macbook" not in title.lower():
            parent_heading = anchor.find_parent(["h2", "h3", "h4"])
            if parent_heading:
                title = parent_heading.get_text(" ", strip=True)
        if "macbook" not in title.lower():
            continue

        card = closest_price_container(anchor)
        if card is None:
            continue
        text = card.get_text(" ", strip=True)

        price: float | None = None
        for selector in (
            "[data-testid='price-block-customer-price']",
            "[data-testid='customer-price']",
            "[data-testid*='customer-price']",
            ".priceView-customer-price",
        ):
            element = card.select_one(selector)
            if element and (candidate := _first_money(element.get_text(" ", strip=True))):
                price = candidate
                break
        # On current server-rendered category cards the primary/new price is the
        # first dollar amount; later amounts are comparison/open-box prices.
        if price is None:
            price = _first_money(text)
        if price is None:
            continue

        href = anchor.get("href", "")
        url = urljoin("https://www.bestbuy.com", href)
        sku_match = re_search_sku(url)
        source_id = sku_match or stable_id(url.split("?")[0])
        found[source_id] = listing(
            "bestbuy",
            source_id,
            title,
            url,
            price,
            text=text,
            in_stock=not bool(_OUT_OF_STOCK_RE.search(text)),
        )
    return list(found.values())


def re_search_sku(url: str) -> str | None:
    import re

    match = re.search(r"(?:/sku/|skuId=)(\d+)", url)
    return match.group(1) if match else None


import re as _re
_OUT_OF_STOCK_RE = _re.compile(r"sold out|unavailable", _re.I)


def amazon_block_reason(html: str) -> str | None:
    lower = html.lower()
    markers = {
        "captcha": (
            "enter the characters you see below",
            "sorry, we just need to make sure you're not a robot",
        ),
        "automated-access block": ("automated access", "api-services-support@amazon.com"),
        "service-unavailable page": ("service unavailable", "sorry! something went wrong"),
    }
    for reason, phrases in markers.items():
        if any(phrase in lower for phrase in phrases):
            return reason
    return None


def _fetch_with_profile(client: Client, url: str, profile: str) -> str:
    if not HAS_CURL_CFFI:
        return client.get(url)
    session = http.Session(impersonate=profile)
    response = session.get(url, timeout=client.timeout, headers=_request_headers())
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}")
    return response.text


def fetch_bh(client: Client, url: str) -> list[Listing]:
    failures: list[str] = []
    for profile in ("chrome", "safari"):
        try:
            html = _fetch_with_profile(client, url, profile)
            items = scrape_bh(html)
            if items:
                return items
            failures.append(f"{profile}: parsed 0 listings")
        except Exception as exc:
            failures.append(f"{profile}: {exc}")
    raise RuntimeError("B&H unavailable: " + "; ".join(failures))


def fetch_bestbuy_html(client: Client, url: str) -> list[Listing]:
    failures: list[str] = []
    for profile in ("chrome", "safari"):
        try:
            html = _fetch_with_profile(client, url, profile)
            items = scrape_bestbuy_modern(html)
            if not items:
                items = scrape_bestbuy(html)
            if items:
                return items
            failures.append(f"{profile}: parsed 0 product cards")
        except Exception as exc:
            failures.append(f"{profile}: {exc}")
    raise RuntimeError("Best Buy unavailable: " + "; ".join(failures))


def fetch_amazon(client: Client, url: str) -> list[Listing]:
    failures: list[str] = []
    candidates = [url]
    if "?k=" in url:
        candidates.append(url.replace("?k=", "?field-keywords="))

    for candidate in candidates:
        for profile in ("safari", "chrome"):
            try:
                html = _fetch_with_profile(client, candidate, profile)
            except Exception as exc:
                failures.append(f"{profile}: {exc}")
                continue
            if reason := amazon_block_reason(html):
                failures.append(f"{profile}: {reason}")
                continue
            items = scrape_amazon(html)
            if items:
                return items
            failures.append(f"{profile}: parsed 0 product cards")
        time.sleep(0.35)

    raise RuntimeError("Amazon unavailable: " + "; ".join(failures[-4:]))


def scrape_all_hardened(client: Client, settings: Settings) -> tuple[list[Listing], dict[str, str]]:
    found: dict[str, Listing] = {}
    errors: dict[str, str] = {}

    try:
        items = scrape_apple(client.get(APPLE_URL))
        LOG.info("apple_refurb: parsed %d listings", len(items))
        if not items:
            raise RuntimeError("parsed 0 listings")
        _add_items(found, items)
    except Exception as exc:
        LOG.exception("apple_refurb scrape failed")
        errors["apple_refurb"] = str(exc)

    bh_items: list[Listing] = []
    bh_failures: list[str] = []
    for url in BH_URLS:
        try:
            items = fetch_bh(client, url)
            LOG.info("bh: parsed %d listings", len(items))
            bh_items.extend(items)
        except Exception as exc:
            LOG.exception("bh scrape failed")
            bh_failures.append(str(exc))
        time.sleep(random.uniform(0.55, 1.05))
    _add_items(found, bh_items)
    if not bh_items:
        errors["bh"] = bh_failures[-1] if bh_failures else "parsed 0 listings"
    elif bh_failures:
        errors["bh"] = "partial failure: " + bh_failures[-1]

    bestbuy_items: list[Listing] = []
    bestbuy_failures: list[str] = []
    bestbuy_api_key = str(getattr(settings, "bestbuy_api_key", "") or "")
    if bestbuy_api_key:
        try:
            bestbuy_items = fetch_bestbuy_api(client, bestbuy_api_key)
            LOG.info("bestbuy_api: parsed %d listings", len(bestbuy_items))
        except Exception as exc:
            LOG.exception("bestbuy API scrape failed")
            bestbuy_failures.append(str(exc))

    if not bestbuy_items:
        for url in BESTBUY_URLS:
            try:
                items = fetch_bestbuy_html(client, url)
                LOG.info("bestbuy_html: parsed %d listings", len(items))
                bestbuy_items.extend(items)
            except Exception as exc:
                LOG.exception("bestbuy HTML scrape failed")
                bestbuy_failures.append(str(exc))
            time.sleep(random.uniform(0.55, 1.05))
    _add_items(found, bestbuy_items)
    if not bestbuy_items:
        errors["bestbuy"] = (
            bestbuy_failures[-1]
            if bestbuy_failures
            else "parsed 0 listings; likely blocked or markup changed"
        )

    amazon_items: list[Listing] = []
    amazon_failures: list[str] = []
    for url in AMAZON_URLS:
        try:
            items = fetch_amazon(client, url)
            LOG.info("amazon: parsed %d listings", len(items))
            amazon_items.extend(items)
        except Exception as exc:
            LOG.exception("amazon scrape failed")
            amazon_failures.append(str(exc))
        time.sleep(random.uniform(0.55, 1.05))
    _add_items(found, amazon_items)
    if not amazon_items:
        errors["amazon"] = amazon_failures[-1] if amazon_failures else "parsed 0 listings"
    elif amazon_failures:
        errors["amazon"] = "partial failure: " + amazon_failures[-1]

    return list(found.values()), errors


def _send_ntfy_message(
    settings: Settings,
    *,
    title: str,
    body: str,
    priority: str,
    tags: str,
) -> None:
    if not settings.ntfy_topic:
        LOG.warning("source-health message suppressed: NTFY_TOPIC is not configured")
        return
    headers = {"Title": title, "Priority": priority, "Tags": tags}
    if settings.ntfy_token.startswith("tk_"):
        headers["Authorization"] = f"Bearer {settings.ntfy_token}"
    kwargs: dict[str, Any] = {
        "data": body.encode("utf-8"),
        "headers": headers,
        "timeout": settings.timeout,
    }
    if HAS_CURL_CFFI:
        kwargs["impersonate"] = "chrome"
    response = http.post(
        f"{settings.ntfy_server}/{quote(settings.ntfy_topic, safe='')}",
        **kwargs,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"ntfy HTTP {response.status_code}: {response.text[:200]}")


def update_source_health(
    settings: Settings,
    state: dict[str, Any],
    errors: dict[str, str],
    now: float,
) -> int:
    alert_after = max(1, int(getattr(settings, "source_alert_after", 3)))
    realert_hours = float(getattr(settings, "source_realert_hours", 6))
    health = state.setdefault("source_health", {})
    notifications = 0

    for source in SOURCE_ORDER:
        record = health.setdefault(source, {})
        previous_failures = int(record.get("consecutive_failures", 0))
        if source in errors:
            failures = previous_failures + 1
            record.update(
                {
                    "consecutive_failures": failures,
                    "last_error": errors[source],
                    "last_failure_at": now,
                }
            )
            last_alert = float(record.get("last_alert_at", 0))
            if failures >= alert_after and (
                not last_alert or now - last_alert >= realert_hours * 3600
            ):
                try:
                    _send_ntfy_message(
                        settings,
                        title=f"MacBook monitor degraded: {SOURCE_NAMES[source]}",
                        body=(
                            f"{SOURCE_NAMES[source]} has failed {failures} consecutive scans.\n"
                            f"Latest error: {errors[source]}\n"
                            "The other retailer checks are still running."
                        ),
                        priority="high",
                        tags="warning,computer",
                    )
                    notifications += 1
                    record["last_alert_at"] = now
                except Exception:
                    LOG.exception("failed to send source health alert for %s", source)
        else:
            record.update({"consecutive_failures": 0, "last_ok_at": now})
            if previous_failures >= alert_after and record.get("last_alert_at"):
                try:
                    _send_ntfy_message(
                        settings,
                        title=f"MacBook monitor recovered: {SOURCE_NAMES[source]}",
                        body=f"{SOURCE_NAMES[source]} is returning listings again.",
                        priority="default",
                        tags="white_check_mark,computer",
                    )
                    notifications += 1
                except Exception:
                    LOG.exception("failed to send source recovery alert for %s", source)
                record.pop("last_alert_at", None)

    return notifications
