import threading
from datetime import datetime, timezone
from typing import Any

from supabase import Client, create_client

from .config import settings


_local = threading.local()


def _client() -> Client:
    # FastAPI runs sync endpoints in parallel worker threads, and a single
    # shared Supabase client dropped connections under concurrent requests
    # (httpx.RemoteProtocolError: Server disconnected -> HTTP 500). Each
    # thread gets its own client and connection pool instead.
    if not hasattr(_local, "client"):
        _local.client = create_client(
            settings.supabase_url,
            settings.supabase_secret_key,
        )

    return _local.client


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_card(card_id: str) -> dict[str, Any] | None:
    response = (
        _client().table("cards")
        .select("*")
        .eq("id", card_id)
        .limit(1)
        .execute()
    )
    return response.data[0] if response.data else None


def get_card_by_poketrace_id(poketrace_id: str) -> dict[str, Any] | None:
    response = (
        _client().table("cards")
        .select("*")
        .eq("poketrace_id", poketrace_id)
        .limit(1)
        .execute()
    )
    return response.data[0] if response.data else None


def save_card(card: dict[str, Any]) -> dict[str, Any]:
    existing = get_card_by_poketrace_id(card["poketrace_id"])

    payload = {
        "poketrace_id": card["poketrace_id"],
        "name": card["name"],
        "card_number": card.get("card_number"),
        "set_name": card.get("set_name"),
        "set_slug": card.get("set_slug"),
        "variant": card.get("variant"),
        "rarity": card.get("rarity"),
        "image_url": card.get("image_url"),
        "tcgplayer_id": card.get("tcgplayer_id"),
        "marketplace_urls": card.get("marketplace_urls") or {},
        "updated_at": utc_now_iso(),
    }

    if existing:
        # Keep extra keys (e.g. tcggo_id) that a catalog refresh doesn't know.
        payload["marketplace_urls"] = {
            **(existing.get("marketplace_urls") or {}),
            **payload["marketplace_urls"],
        }

        response = (
            _client().table("cards")
            .update(payload)
            .eq("id", existing["id"])
            .execute()
        )
        return response.data[0]

    response = _client().table("cards").insert(payload).execute()
    return response.data[0]


def update_marketplace_urls(card_id: str, extra: dict[str, Any]) -> None:
    """Merge `extra` into a card's marketplace_urls (used to remember the
    TCGGO id so a sleeping/redeployed backend never has to look it up again)."""
    card = get_card(card_id)
    if not card:
        return

    merged = {**(card.get("marketplace_urls") or {}), **extra}
    _client().table("cards").update({"marketplace_urls": merged}).eq(
        "id", card_id
    ).execute()


def add_collection_item(
    card_id: str,
    quantity: int,
    ownership_grade: str,
    purchase_price: float | None,
    notes: str | None,
) -> dict[str, Any]:
    payload = {
        "card_id": card_id,
        "quantity": quantity,
        "ownership_grade": ownership_grade,
        "purchase_price": purchase_price,
        "notes": notes,
    }
    response = _client().table("collection_items").insert(payload).execute()
    return response.data[0]


def delete_collection_item(item_id: str) -> None:
    _client().table("collection_items").delete().eq("id", item_id).execute()


def get_collection_item(item_id: str) -> dict[str, Any] | None:
    response = (
        _client().table("collection_items")
        .select("*")
        .eq("id", item_id)
        .limit(1)
        .execute()
    )
    return response.data[0] if response.data else None


def update_collection_item_quantity(item_id: str, quantity: int) -> None:
    _client().table("collection_items").update(
        {"quantity": quantity, "updated_at": utc_now_iso()}
    ).eq("id", item_id).execute()


# Supabase returns at most 1000 rows per request.
PAGE_SIZE = 1000


def _fetch_all(build_query) -> list[dict[str, Any]]:
    """Every row of a query, fetched a page at a time. `build_query` returns a
    fresh query builder each call, since range() can't be reapplied."""
    rows: list[dict[str, Any]] = []
    start = 0

    while True:
        page = build_query().range(start, start + PAGE_SIZE - 1).execute().data or []
        rows.extend(page)

        if len(page) < PAGE_SIZE:
            return rows

        start += PAGE_SIZE


def get_cards(card_ids) -> dict[str, dict[str, Any]]:
    ids = list(card_ids)
    if not ids:
        return {}

    rows = _fetch_all(
        lambda: _client().table("cards").select("*").in_("id", ids).order("id")
    )
    return {row["id"]: row for row in rows}


def get_price_snapshots_for_cards(card_ids) -> dict[str, list[dict[str, Any]]]:
    """All price snapshots (oldest first) for each card - no row cap, since
    the performance history needs every price observation, not the latest."""
    ids = list(card_ids)
    result: dict[str, list[dict[str, Any]]] = {card_id: [] for card_id in ids}
    if not ids:
        return result

    rows = _fetch_all(
        lambda: _client().table("price_snapshots")
        .select("*")
        .in_("card_id", ids)
        .order("observed_at")
        .order("id")
    )
    for row in rows:
        result.setdefault(row["card_id"], []).append(row)

    return result


def get_collection_events(collection_item_id: str | None = None) -> list[dict[str, Any]]:
    """ADD/REMOVE history, oldest first. Raises if the collection_events
    table hasn't been created yet (supabase/schema.sql)."""

    def build_query():
        query = _client().table("collection_events").select("*")
        if collection_item_id is not None:
            query = query.eq("collection_item_id", collection_item_id)
        return query.order("occurred_at").order("id")

    return _fetch_all(build_query)


def insert_collection_event(
    collection_item_id: str,
    card_id: str,
    grade: str,
    event_type: str,
    quantity: int,
    occurred_at: str,
    market_value_per_card: float | None,
    recorded_by: str = "app",
) -> dict[str, Any]:
    total_flow_value = None
    if market_value_per_card is not None:
        sign = -1 if event_type == "REMOVE" else 1
        total_flow_value = round(sign * quantity * market_value_per_card, 2)

    payload = {
        "collection_item_id": collection_item_id,
        "card_id": card_id,
        "grade": grade,
        "event_type": event_type,
        "quantity": quantity,
        "market_value_per_card": market_value_per_card,
        "total_flow_value": total_flow_value,
        "occurred_at": occurred_at,
        "recorded_by": recorded_by,
    }
    response = _client().table("collection_events").insert(payload).execute()
    return response.data[0]


def delete_collection_event(event_id: int) -> None:
    _client().table("collection_events").delete().eq("id", event_id).execute()


def get_collection_items() -> list[dict[str, Any]]:
    response = (
        _client().table("collection_items")
        .select("*")
        .order("created_at", desc=False)
        .execute()
    )
    return response.data or []


def insert_price_snapshot(
    card_id: str,
    grade: str,
    source: str,
    value: float,
    source_url: str | None = None,
    observed_at: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "card_id": card_id,
        "grade": grade,
        "source": source,
        "value": value,
        "source_url": source_url,
        "observed_at": observed_at or utc_now_iso(),
        "metadata": metadata or {},
    }
    response = _client().table("price_snapshots").insert(payload).execute()
    return response.data[0]


def get_price_snapshots(card_id: str, limit: int = 500) -> list[dict[str, Any]]:
    response = (
        _client().table("price_snapshots")
        .select("*")
        .eq("card_id", card_id)
        .order("observed_at", desc=True)
        .limit(limit)
        .execute()
    )
    return response.data or []
