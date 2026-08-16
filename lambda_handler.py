from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

import retailer_hardening as retailers
from macbook_scraper import Client, Settings, is_match, send_ntfy
from retailer_hardening import update_source_health

LOG = logging.getLogger("macbook-scraper.lambda")
STATE_KEY = "monitor-state"


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


def scrape_configured_sources(
    settings: Settings,
    client: Client,
) -> tuple[list[Any], dict[str, str]]:
    """Run the cloud-safe sources and optionally Amazon.

    Amazon is disabled by default for Lambda because repeated requests from AWS
    egress IPs are currently returning HTTP 503. Keeping the existing Amazon
    implementation behind ENABLE_AMAZON means it can be re-enabled later
    without changing the scraper again.
    """

    if bool(getattr(settings, "amazon_enabled", False)):
        return retailers.scrape_all_hardened(client, settings)

    original_amazon_urls = retailers.AMAZON_URLS
    retailers.AMAZON_URLS = []
    try:
        items, errors = retailers.scrape_all_hardened(client, settings)
    finally:
        retailers.AMAZON_URLS = original_amazon_urls

    # scrape_all_hardened treats an empty Amazon job list as a source error.
    # For an intentionally disabled source, remove that synthetic error.
    errors.pop("amazon", None)
    LOG.info("amazon: disabled by configuration")
    return items, errors


def run_lambda_cycle(settings: Settings, client: Client, store: DynamoStateStore) -> dict[str, Any]:
    now = time.time()
    items, errors = scrape_configured_sources(settings, client)
    matches = sorted(
        (item for item in items if is_match(item, settings)),
        key=lambda item: (item.price, -item.memory_gb, -item.storage_gb),
    )
    LOG.info(
        "cycle: %d listings, %d matches <= $%.2f; errors=%s",
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
        # Clear any old Amazon failure/alert state without emitting a fake
        # recovery notification when the source is intentionally disabled.
        amazon_health = state.setdefault("source_health", {}).setdefault("amazon", {})
        amazon_health.clear()
        amazon_health.update(
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

    # ntfy access tokens always use the tk_ prefix. If SAM guided deploy captured
    # a placeholder or other non-token value, silently treat it as unauthenticated
    # publishing instead of sending a bad Authorization header and getting HTTP 401.
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
