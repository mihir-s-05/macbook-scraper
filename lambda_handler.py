from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from macbook_scraper import Client, Listing, Settings, is_match, scrape_all, send_ntfy

LOG = logging.getLogger("macbook-scraper.lambda")
STATE_KEY = "monitor-state"


class DynamoStateStore:
    """Tiny DynamoDB-backed state store used by the Lambda deployment.

    The entire monitor state is stored in one item. The scraper tracks a small
    number of listings and prunes entries older than 30 days, so the item stays
    well below DynamoDB's item-size limit for this workload.
    """

    def __init__(self, table_name: str, *, table: Any | None = None):
        if table is None:
            import boto3  # Lambda's Python runtime includes boto3.

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


def run_lambda_cycle(s: Settings, client: Client, store: DynamoStateStore) -> dict[str, Any]:
    """Run one scraper cycle and persist dedupe state in DynamoDB."""

    now = time.time()
    items, errors = scrape_all(client)
    matches = sorted(
        (x for x in items if is_match(x, s)),
        key=lambda x: (x.price, -x.memory_gb, -x.storage_gb),
    )
    LOG.info(
        "cycle: %d listings, %d matches <= $%.2f; errors=%s",
        len(items),
        len(matches),
        s.max_price,
        sorted(errors) or "none",
    )

    state = store.load()
    state.setdefault("listings", {})
    sent = 0

    for x in matches:
        old = state["listings"].get(x.key)
        notify = (
            not old
            or x.price < float(old.get("last_notified_price", 1e18)) - 0.009
            or now - float(old.get("last_seen", now)) >= s.realert_hours * 3600
        )

        if notify:
            send_ntfy(s, x)
            sent += 1

        record = old or {}
        record.update(
            {
                "last_seen": now,
                "last_price": x.price,
                "listing": asdict(x),
            }
        )
        if notify:
            record.update(
                {
                    "last_notified_price": x.price,
                    "last_notified_at": now,
                }
            )
        state["listings"][x.key] = record

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
        }
    )
    store.save(state)

    return {
        "ok": True,
        "listings": len(items),
        "matches": len(matches),
        "notifications_sent": sent,
        "error_sources": sorted(errors),
    }


_CLIENT: Client | None = None


def lambda_handler(event: dict[str, Any] | None, context: Any) -> dict[str, Any]:
    """AWS Lambda entry point invoked by EventBridge Scheduler."""

    global _CLIENT

    logging.getLogger().setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    settings = Settings.from_env()
    table_name = os.getenv("DYNAMODB_TABLE", "").strip()
    if not table_name:
        raise RuntimeError("DYNAMODB_TABLE is required for the Lambda deployment")

    if _CLIENT is None:
        _CLIENT = Client(settings.timeout)

    store = DynamoStateStore(table_name)
    result = run_lambda_cycle(settings, _CLIENT, store)
    LOG.info("lambda result: %s", result)
    return result
