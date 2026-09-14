from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

import extra_retailers as extras
import retailer_hardening as retailers
from macbook_scraper import Client, Settings, send_ntfy
from retailer_hardening import update_source_health
from target_filter import is_target_match
from target_sources import apply_target_source_queries

LOG = logging.getLogger("macbook-scraper.lambda")
STATE_KEY = "monitor-state"

# Extend the existing source-health machinery so the added retailers get the
# same consecutive-failure/recovery alerts as the original sources.
retailers.SOURCE_NAMES.update(extras.EXTRA_SOURCE_NAMES)
retailers.SOURCE_ORDER = tuple(retailers.SOURCE_NAMES)
apply_target_source_queries(retailers)


class DynamoStateStore:
    def __init__(self, table_name: str, *, table: Any | None = None):
        if table is None:
            import boto3

            table = boto3.resource("dynamodb").Table(table_name)
        self.table = table

    def load(self) -> dict[str, Any]:
        response = self.table.get_item(Key={"pk": STATE_KEY}, ConsistentRead=True)
        item = response.get("Item") or {}
        raw = item.get("state_json")
        if not raw:
            return {"listings": {}}
        try:
            state = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            LOG.warning("DynamoDB state was unreadable; starting with empty state")
            return {"listings": {}}
        if not isinstance(state, dict):
            return {"listings": {}}
        state.setdefault("listings", {})
        return state

    def save(self, state: dict[str, Any]) -> None:
        self.table.put_item(
            Item={
                "pk": STATE_KEY,
                "state_json": json.dumps(state, separators=(",", ":"), sort_keys=True),
                "updated_at": int(time.time()),
            }
        )


def _merge_items(*groups: list[Any]) -> list[Any]:
    found: dict[str, Any] = {}
    for group in groups:
        for item in group:
            if item.key not in found or item.price < found[item.key].price:
                found[item.key] = item
    return list(found.values())


def scrape_configured_sources(
    settings: Settings,
    client: Client,
) -> tuple[list[Any], dict[str, str]]:
    """Run cloud-safe retailers; Amazon and Adorama are opt-in on Lambda."""

    if bool(getattr(settings, "amazon_enabled", False)):
        core_items, core_errors = retailers.scrape_all_hardened(client, settings)
    else:
        original_amazon_urls = retailers.AMAZON_URLS
        retailers.AMAZON_URLS = []
        try:
            core_items, core_errors = retailers.scrape_all_hardened(client, settings)
        finally:
            retailers.AMAZON_URLS = original_amazon_urls
        core_errors.pop("amazon", None)
        LOG.info("amazon: disabled by configuration")

    adorama_enabled = bool(getattr(settings, "adorama_enabled", False))
    if adorama_enabled:
        extra_items, extra_errors = extras.scrape_extra_sources(client)
    else:
        original_adorama_urls = extras.ADORAMA_URLS
        extras.ADORAMA_URLS = []
        try:
            extra_items, extra_errors = extras.scrape_extra_sources(client)
        finally:
            extras.ADORAMA_URLS = original_adorama_urls
        extra_errors.pop("adorama", None)
        LOG.info("adorama: disabled by configuration")

    counts = Counter(item.source for item in extra_items)
    for source in extras.EXTRA_SOURCE_NAMES:
        if source == "adorama" and not adorama_enabled:
            continue
        if source in counts:
            LOG.info("%s: parsed %d listings", source, counts[source])
        elif source in extra_errors:
            LOG.warning("%s scrape degraded: %s", source, extra_errors[source])

    errors = dict(core_errors)
    errors.update(extra_errors)
    return _merge_items(core_items, extra_items), errors


def run_lambda_cycle(settings: Settings, client: Client, store: DynamoStateStore) -> dict[str, Any]:
    now = time.time()
    items, errors = scrape_configured_sources(settings, client)
    matches = sorted(
        (item for item in items if is_target_match(item, settings)),
        key=lambda item: (item.price, -item.memory_gb, -item.storage_gb),
    )
    LOG.info(
        "cycle: %d listings, %d focused 15-inch Air matches < $%.2f; errors=%s",
        len(items),
        len(matches),
        settings.max_price,
        sorted(errors) or "none",
    )

    state = store.load()
    state.setdefault("listings", {})

    disabled_sources: list[str] = []
    if not bool(getattr(settings, "amazon_enabled", False)):
        disabled_sources.append("amazon")
    if not bool(getattr(settings, "adorama_enabled", False)):
        disabled_sources.append("adorama")

    # Clear stale degraded/alert state for intentionally disabled sources without
    # generating a misleading recovery notification.
    health = state.setdefault("source_health", {})
    for source in disabled_sources:
        record = health.setdefault(source, {})
        record.clear()
        record.update(
            {
                "disabled": True,
                "consecutive_failures": 0,
                "disabled_at": now,
            }
        )

    health_sent = update_source_health(settings, state, errors, now)
    deal_sent = 0

    for item in matches:
        old = state["listings"].get(item.key)
        notify = (
            not old
            or item.price < float(old.get("last_notified_price", 1e18)) - 0.009
            or now - float(old.get("last_seen", now)) >= settings.realert_hours * 3600
        )
        if notify:
            send_ntfy(settings, item)
            deal_sent += 1

        record = old or {}
        record.update({"last_seen": now, "last_price": item.price, "listing": asdict(item)})
        if notify:
            record.update({"last_notified_price": item.price, "last_notified_at": now})
        state["listings"][item.key] = record

    cutoff = now - 30 * 86400
    state["listings"] = {
        key: value
        for key, value in state["listings"].items()
        if float(value.get("last_seen", 0)) >= cutoff
    }
    state.update(
        {
            "last_cycle_at": now,
            "last_cycle_iso": datetime.now(timezone.utc).isoformat(),
            "last_error_sources": errors,
            "disabled_sources": disabled_sources,
            "target": {
                "model": "MacBook Air",
                "display_inches": 15,
                "min_memory_gb": settings.min_memory_gb,
                "min_storage_gb": settings.min_storage_gb,
                "allowed_chips": list(settings.allowed_chips),
                "price_ceiling_exclusive": settings.max_price,
            },
        }
    )
    store.save(state)

    return {
        "ok": True,
        "listings": len(items),
        "matches": len(matches),
        "notifications_sent": deal_sent,
        "health_notifications_sent": health_sent,
        "error_sources": sorted(errors),
        "disabled_sources": disabled_sources,
    }


_CLIENT: Client | None = None


def lambda_handler(event: dict[str, Any] | None, context: Any) -> dict[str, Any]:
    global _CLIENT

    logging.getLogger().setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    settings = Settings.from_env()

    # This monitor is intentionally focused now. TARGET_MAX_PRICE is separate
    # from the old CloudFormation MAX_PRICE value so an existing saved 1300
    # deployment does not override the new <1900 target on update.
    object.__setattr__(settings, "max_price", float(os.getenv("TARGET_MAX_PRICE", "1900")))
    object.__setattr__(settings, "min_memory_gb", 24)
    object.__setattr__(settings, "min_storage_gb", 1024)
    object.__setattr__(settings, "allowed_chips", ("M4", "M5"))

    if settings.ntfy_token and not settings.ntfy_token.startswith("tk_"):
        LOG.warning("Ignoring NTFY_TOKEN because it is not a valid tk_... access token")
        object.__setattr__(settings, "ntfy_token", "")

    object.__setattr__(settings, "bestbuy_api_key", os.getenv("BESTBUY_API_KEY", "").strip())
    object.__setattr__(
        settings,
        "amazon_enabled",
        os.getenv("ENABLE_AMAZON", "false").strip().lower() in {"1", "true", "yes", "on"},
    )
    object.__setattr__(
        settings,
        "adorama_enabled",
        os.getenv("ENABLE_ADORAMA", "false").strip().lower() in {"1", "true", "yes", "on"},
    )
    object.__setattr__(
        settings,
        "source_alert_after",
        max(1, int(os.getenv("SOURCE_ALERT_AFTER", "3"))),
    )
    object.__setattr__(
        settings,
        "source_realert_hours",
        float(os.getenv("SOURCE_REALERT_HOURS", "6")),
    )

    table_name = os.getenv("DYNAMODB_TABLE", "").strip()
    if not table_name:
        raise RuntimeError("DYNAMODB_TABLE is required for the Lambda deployment")
    if _CLIENT is None:
        _CLIENT = Client(settings.timeout)

    store = DynamoStateStore(table_name)
    result = run_lambda_cycle(settings, _CLIENT, store)
    LOG.info("lambda result: %s", result)
    return result
