#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

try:
    from curl_cffi import requests as http
    HAS_CURL_CFFI = True
except ImportError:  # parser tests can run without curl-cffi
    import requests as http
    HAS_CURL_CFFI = False

LOG = logging.getLogger("macbook-scraper")

APPLE_URL = "https://www.apple.com/shop/refurbished/mac"
BH_URLS = [
    f"https://www.bhphotovideo.com/c/search?q={quote_plus(q)}&sts=ma"
    for q in ("Apple MacBook 24GB 1TB", "Apple MacBook 32GB 1TB")
]
BESTBUY_URLS = [
    f"https://www.bestbuy.com/site/searchpage.jsp?st={quote_plus(q)}"
    for q in ("MacBook 24GB 1TB", "MacBook 32GB 1TB")
]
AMAZON_URLS = [
    f"https://www.amazon.com/s?k={quote_plus(q)}"
    for q in ("Apple MacBook 24GB 1TB", "Apple MacBook 32GB 1TB")
]

MONEY_RE = re.compile(r"\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.\d{2})?)")
CHIP_RE = re.compile(r"\b(M[1-9])(?:\s+(Pro|Max|Ultra))?\b", re.I)
MODEL_RE = re.compile(r"\bMacBook\s+(Air|Pro)\b", re.I)
MEMORY_PATTERNS = [
    re.compile(r"\b(\d{2,3})\s*GB\s+(?:Unified\s+)?(?:Memory|RAM)\b", re.I),
    re.compile(r"\b(\d{2,3})\s*GB\s+Unified\b", re.I),
    re.compile(r"\b(?:Memory|RAM)\s*[:|-]?\s*(\d{2,3})\s*GB\b", re.I),
]
STORAGE_PATTERNS = [
    re.compile(r"\b(\d+(?:\.\d+)?)\s*(TB|GB)\s+(?:SSD|Storage)\b", re.I),
    re.compile(r"\b(?:SSD|Storage)\s*[:|-]?\s*(\d+(?:\.\d+)?)\s*(TB|GB)\b", re.I),
]


@dataclass(frozen=True)
class Listing:
    source: str
    source_id: str
    title: str
    url: str
    price: float
    memory_gb: int
    storage_gb: int
    chip: str
    model: str
    condition: str
    in_stock: bool = True

    @property
    def key(self) -> str:
        return f"{self.source}:{self.source_id}"


@dataclass(frozen=True)
class Settings:
    max_price: float = 1200
    min_memory_gb: int = 24
    min_storage_gb: int = 1024
    allowed_chips: tuple[str, ...] = ("M4", "M5")
    poll_seconds: int = 300
    timeout: int = 25
    state_path: str = "/data/state.json"
    realert_hours: float = 6
    webhook: str = ""
    run_once: bool = False
    port: int = 0

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            max_price=float(os.getenv("MAX_PRICE", "1200")),
            min_memory_gb=int(os.getenv("MIN_MEMORY_GB", "24")),
            min_storage_gb=int(os.getenv("MIN_STORAGE_GB", "1024")),
            allowed_chips=tuple(x.strip().upper() for x in os.getenv("ALLOWED_CHIPS", "M4,M5").split(",") if x.strip()),
            poll_seconds=max(60, int(os.getenv("POLL_INTERVAL_SECONDS", "300"))),
            timeout=int(os.getenv("REQUEST_TIMEOUT_SECONDS", "25")),
            state_path=os.getenv("STATE_PATH", "/data/state.json"),
            realert_hours=float(os.getenv("REALERT_AFTER_HOURS", "6")),
            webhook=os.getenv("DISCORD_WEBHOOK_URL", "").strip(),
            run_once=os.getenv("RUN_ONCE", "false").lower() in {"1", "true", "yes"},
            port=int(os.getenv("PORT", "0") or 0),
        )


class Client:
    def __init__(self, timeout: int):
        self.timeout = timeout
        self.session = http.Session(impersonate="chrome") if HAS_CURL_CFFI else http.Session()

    def get(self, url: str) -> str:
        response = self.session.get(
            url,
            timeout=self.timeout,
            headers={"Accept-Language": "en-US,en;q=0.9", "Cache-Control": "no-cache"},
        )
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {url}")
        return response.text


def stable_id(value: str) -> str:
    return hashlib.sha1(value.encode()).hexdigest()[:16]


def parse_price(text: str) -> float | None:
    values = [float(x.replace(",", "")) for x in MONEY_RE.findall(text)]
    return min(values) if values else None


def parse_specs(text: str) -> tuple[int, int, str, str]:
    text = " ".join(text.split())
    memory = next((int(m.group(1)) for p in MEMORY_PATTERNS if (m := p.search(text))), 0)
    storage = 0
    for p in STORAGE_PATTERNS:
        if m := p.search(text):
            amount = float(m.group(1))
            storage = round(amount * 1024) if m.group(2).upper() == "TB" else round(amount)
            break
    chip = ""
    if m := CHIP_RE.search(text):
        chip = m.group(1).upper() + ((" " + m.group(2).title()) if m.group(2) else "")
    model = "MacBook"
    if m := MODEL_RE.search(text):
        model = f"MacBook {m.group(1).title()}"
    return memory, int(storage), chip, model


def listing(source: str, source_id: str, title: str, url: str, price: float, *, text: str = "", condition: str = "new", in_stock: bool = True, memory: int | None = None, storage: int | None = None, chip: str | None = None, model: str | None = None) -> Listing:
    pmem, pstore, pchip, pmodel = parse_specs(text or title)
    return Listing(
        source, source_id, " ".join(title.split()), url, round(float(price), 2),
        pmem if memory is None else memory,
        pstore if storage is None else storage,
        pchip if chip is None else chip,
        pmodel if model is None else model,
        condition, in_stock,
    )


def parse_memory(raw: str | None) -> int:
    m = re.search(r"(\d{1,3})\s*GB", raw or "", re.I)
    return int(m.group(1)) if m else 0


def parse_capacity(raw: str | None) -> int:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(TB|GB)", raw or "", re.I)
    if not m:
        return 0
    n = float(m.group(1))
    return int(round(n * 1024 if m.group(2).upper() == "TB" else n))


def scrape_apple(html: str) -> list[Listing]:
    match = re.search(r"window\.REFURB_GRID_BOOTSTRAP\s*=\s*({[\s\S]*?})\s*;?\s*</script>", html)
    if match:
        try:
            data = json.loads(match.group(1))
            out = []
            for tile in data.get("tiles", []):
                dims = tile.get("filters", {}).get("dimensions", {}) or {}
                title = tile.get("title", "")
                path = tile.get("productDetailsUrl", "")
                raw_price = tile.get("price", {}).get("currentPrice", {}).get("raw_amount", "")
                price = float(re.sub(r"[^0-9.]", "", raw_price) or 0)
                _, _, chip, parsed_model = parse_specs(title)
                model = {"macbookair": "MacBook Air", "macbookpro": "MacBook Pro"}.get(dims.get("refurbClearModel", "").lower(), parsed_model)
                if title and path and price:
                    out.append(listing(
                        "apple_refurb", tile.get("partNumber") or stable_id(path), title,
                        urljoin("https://www.apple.com", path), price,
                        condition="apple_certified_refurbished",
                        memory=parse_memory(dims.get("tsMemorySize")),
                        storage=parse_capacity(dims.get("dimensionCapacity")),
                        chip=chip, model=model,
                    ))
            return out
        except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
            pass

    soup = BeautifulSoup(html, "html.parser")
    out = []
    for card in soup.select(".rf-refurb-category-grid-no-js li"):
        a = card.select_one("h3 a")
        p = card.select_one(".as-price-currentprice, .as-producttile-currentprice")
        if a and p and (price := parse_price(p.get_text(" ", strip=True))):
            href = a.get("href", "")
            out.append(listing("apple_refurb", stable_id(href), a.get_text(" ", strip=True), urljoin("https://www.apple.com", href), price, text=card.get_text(" ", strip=True), condition="apple_certified_refurbished"))
    return out


def closest_price_container(node: Any) -> Any | None:
    for _ in range(8):
        node = getattr(node, "parent", None)
        if node is None:
            return None
        text = node.get_text(" ", strip=True)
        if "$" in text and len(text) < 8000:
            return node
    return None


def selected_price(card: Any, selectors: tuple[str, ...]) -> float | None:
    for selector in selectors:
        if el := card.select_one(selector):
            if price := parse_price(el.get_text(" ", strip=True)):
                return price
    return parse_price(card.get_text(" ", strip=True))


def scrape_bh(html: str) -> list[Listing]:
    soup, found = BeautifulSoup(html, "html.parser"), {}
    for a in soup.select('a[href*="/c/product/"]'):
        title = a.get_text(" ", strip=True)
        if "macbook" not in title.lower() and (h := a.find_parent(["h2", "h3", "h4"])):
            title = h.get_text(" ", strip=True)
        if "macbook" not in title.lower() or not (card := closest_price_container(a)):
            continue
        text = card.get_text(" ", strip=True)
        price = selected_price(card, ("[data-selenium='uppedDecimalPriceFirst']", "[data-selenium='pricingPrice']", "[data-testid='price']"))
        if not price:
            continue
        url = urljoin("https://www.bhphotovideo.com", a.get("href", ""))
        item = listing("bh", stable_id(url.split("?")[0]), title, url, price, text=text, in_stock=not bool(re.search(r"temporarily out of stock|discontinued", text, re.I)))
        found[item.source_id] = item
    return list(found.values())


def scrape_bestbuy(html: str) -> list[Listing]:
    soup, found = BeautifulSoup(html, "html.parser"), {}
    cards = soup.select("li.sku-item, .product-grid-view-container li, .shop-sku-list-item")
    if not cards:
        cards = [c for a in soup.select('a[href*="/site/"][href*=".p"]') if (c := closest_price_container(a))]
    for card in cards:
        a = card.select_one("h4.sku-title a, .sku-title a, h4 a, h3 a, a[href*='/site/'][href*='.p']")
        if not a or "macbook" not in (title := a.get_text(" ", strip=True)).lower():
            continue
        text = card.get_text(" ", strip=True)
        price = selected_price(card, ("[data-testid='price-block-customer-price']", "[data-testid='customer-price']", ".priceView-customer-price"))
        if not price:
            continue
        url = urljoin("https://www.bestbuy.com", a.get("href", ""))
        m = re.search(r"skuId=(\d+)", url)
        sid = m.group(1) if m else stable_id(url.split("?")[0])
        found[sid] = listing("bestbuy", sid, title, url, price, text=text, in_stock=not bool(re.search(r"sold out|unavailable", text, re.I)))
    return list(found.values())


def scrape_amazon(html: str) -> list[Listing]:
    soup, out = BeautifulSoup(html, "html.parser"), []
    for card in soup.select('div[data-component-type="s-search-result"][data-asin]'):
        asin, h2, a, p = card.get("data-asin", "").strip(), card.select_one("h2"), card.select_one("h2 a"), card.select_one(".a-price .a-offscreen")
        if not asin or not h2 or not a or not p:
            continue
        title = h2.get_text(" ", strip=True)
        price = parse_price(p.get_text(" ", strip=True))
        if "macbook" in title.lower() and price:
            out.append(listing("amazon", asin, title, urljoin("https://www.amazon.com", a.get("href", "")), price, text=card.get_text(" ", strip=True)))
    return out


def is_match(x: Listing, s: Settings) -> bool:
    generation = (re.match(r"M\d+", x.chip.upper()) or [""])[0]
    bad_condition = x.source != "apple_refurb" and any(t in x.title.lower() for t in ("renewed", "refurbished", "open-box", "open box", "pre-owned", "used"))
    return (
        x.in_stock and not bad_condition and x.model in {"MacBook Air", "MacBook Pro"}
        and generation in s.allowed_chips and x.memory_gb >= s.min_memory_gb
        and x.storage_gb >= s.min_storage_gb and x.price <= s.max_price
    )


def load_state(path: str) -> dict[str, Any]:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return {"listings": {}}


def save_state(path: str, state: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(p)


def send_discord(s: Settings, x: Listing) -> None:
    if not s.webhook:
        LOG.warning("MATCH (no Discord webhook configured): %s", asdict(x))
        return
    payload = {"content": "🚨 **MacBook deal matched your hard limit**", "embeds": [{
        "title": f"${x.price:,.2f} — {x.title}", "url": x.url,
        "fields": [
            {"name": "Source", "value": x.source, "inline": True},
            {"name": "Chip", "value": x.chip or "unknown", "inline": True},
            {"name": "Memory", "value": f"{x.memory_gb} GB", "inline": True},
            {"name": "Storage", "value": f"{x.storage_gb / 1024:g} TB", "inline": True},
            {"name": "Condition", "value": x.condition.replace("_", " "), "inline": True},
        ], "footer": {"text": "macbook-scraper • verify price/stock before checkout"},
    }]}
    kwargs: dict[str, Any] = {"json": payload, "timeout": s.timeout}
    if HAS_CURL_CFFI:
        kwargs["impersonate"] = "chrome"
    r = http.post(s.webhook, **kwargs)
    if r.status_code >= 400:
        raise RuntimeError(f"Discord webhook HTTP {r.status_code}: {r.text[:200]}")


def scrape_all(client: Client) -> tuple[list[Listing], dict[str, str]]:
    jobs: list[tuple[str, str, Callable[[str], list[Listing]]]] = [("apple_refurb", APPLE_URL, scrape_apple)]
    jobs += [("bh", u, scrape_bh) for u in BH_URLS]
    jobs += [("bestbuy", u, scrape_bestbuy) for u in BESTBUY_URLS]
    jobs += [("amazon", u, scrape_amazon) for u in AMAZON_URLS]
    found, errors = {}, {}
    for i, (source, url, parser) in enumerate(jobs):
        try:
            items = parser(client.get(url))
            LOG.info("%s: parsed %d listings", source, len(items))
            for item in items:
                if item.key not in found or item.price < found[item.key].price:
                    found[item.key] = item
        except Exception as exc:
            LOG.exception("%s scrape failed", source)
            errors[source] = str(exc)
        if i + 1 < len(jobs):
            time.sleep(random.uniform(0.7, 1.6))
    return list(found.values()), errors


def run_cycle(s: Settings, client: Client) -> int:
    now = time.time()
    items, errors = scrape_all(client)
    matches = sorted((x for x in items if is_match(x, s)), key=lambda x: (x.price, -x.memory_gb, -x.storage_gb))
    LOG.info("cycle: %d listings, %d matches <= $%.2f; errors=%s", len(items), len(matches), s.max_price, sorted(errors) or "none")
    state = load_state(s.state_path)
    state.setdefault("listings", {})
    sent = 0
    for x in matches:
        old = state["listings"].get(x.key)
        notify = not old or x.price < float(old.get("last_notified_price", 1e18)) - 0.009 or now - float(old.get("last_seen", now)) >= s.realert_hours * 3600
        if notify:
            send_discord(s, x)
            sent += 1
        record = old or {}
        record.update({"last_seen": now, "last_price": x.price, "listing": asdict(x)})
        if notify:
            record.update({"last_notified_price": x.price, "last_notified_at": now})
        state["listings"][x.key] = record
    cutoff = now - 30 * 86400
    state["listings"] = {k: v for k, v in state["listings"].items() if float(v.get("last_seen", 0)) >= cutoff}
    state.update({"last_cycle_at": now, "last_cycle_iso": datetime.now(timezone.utc).isoformat(), "last_error_sources": errors})
    save_state(s.state_path, state)
    return sent


def start_health_server(port: int) -> None:
    if port <= 0:
        return
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = b'{"ok":true,"service":"macbook-scraper"}\n'
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        def log_message(self, *_: Any) -> None:
            pass
    threading.Thread(target=ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever, daemon=True).start()


def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = Settings.from_env()
    LOG.info("target: <= $%.2f, >= %dGB RAM, >= %dGB storage, chips=%s", s.max_price, s.min_memory_gb, s.min_storage_gb, ",".join(s.allowed_chips))
    start_health_server(s.port)
    client = Client(s.timeout)
    while True:
        started = time.monotonic()
        try:
            sent = run_cycle(s, client)
            if sent:
                LOG.info("sent %d notification(s)", sent)
        except Exception:
            LOG.exception("monitor cycle crashed")
        if s.run_once:
            return 0
        time.sleep(max(5, s.poll_seconds - (time.monotonic() - started)))


if __name__ == "__main__":
    sys.exit(main())
