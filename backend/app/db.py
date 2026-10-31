import logging
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


def refresh_saved_cards(updates: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]]) -> None:
    """Catalog details for many saved cards in one request: each update is
    (saved card row, fresh normalized details, variant siblings or None).
    Like save_card, extra marketplace_urls keys (e.g. tcggo_id) are kept,
    and siblings left as they were when None."""
    if not updates:
        return

    now = utc_now_iso()
    payload = [
        {
            "poketrace_id": saved["poketrace_id"],
            "name": fresh["name"],
            "card_number": fresh.get("card_number"),
            "set_name": fresh.get("set_name"),
            "set_slug": fresh.get("set_slug"),
            "variant": fresh.get("variant"),
            "rarity": fresh.get("rarity"),
            "image_url": fresh.get("image_url"),
            "tcgplayer_id": fresh.get("tcgplayer_id"),
            "marketplace_urls": {
                **(saved.get("marketplace_urls") or {}),
                **(fresh.get("marketplace_urls") or {}),
            },
            "variant_siblings": siblings if siblings is not None else saved.get("variant_siblings"),
            "updated_at": now,
        }
        for saved, fresh, siblings in updates
    ]

    for start in range(0, len(payload), 500):
        _client().table("cards").upsert(
            payload[start:start + 500], on_conflict="poketrace_id"
        ).execute()


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


# Columns added by supabase/migrations/2026-10-10_variant_identity.sql. Until
# that's run, writing them fails; those writes are skipped (and reported)
# rather than breaking the request they're part of.
_missing_columns: set[str] = set()


def _optional_update(table: str, row_id: str, column: str, value: Any) -> bool:
    if column in _missing_columns:
        return False

    try:
        _client().table(table).update({column: value}).eq("id", row_id).execute()
        return True
    except Exception as exc:
        if column in str(exc) or "PGRST204" in str(exc):
            _missing_columns.add(column)
            logging.getLogger("db").warning(
                "%s.%s doesn't exist yet; run "
                "supabase/migrations/2026-10-10_variant_identity.sql",
                table,
                column,
            )
            return False
        raise


def update_card_variant_siblings(card_id: str, siblings: dict[str, Any] | None) -> bool:
    if siblings is None:
        return False
    return _optional_update("cards", card_id, "variant_siblings", siblings)


def set_item_variant_confirmed(item_id: str, confirmed: bool = True) -> bool:
    return _optional_update(
        "collection_items",
        item_id,
        "variant_confirmed_at",
        utc_now_iso() if confirmed else None,
    )


def set_item_cert(item_id: str, cert_number: str | None) -> None:
    """Raises if the column doesn't exist yet
    (supabase/migrations/2026-10-17_slab_images.sql)."""
    _client().table("collection_items").update(
        {"psa_cert_number": cert_number}
    ).eq("id", item_id).execute()


def migration_applied() -> bool:
    return not _missing_columns


def add_collection_item(
    card_id: str,
    quantity: int,
    ownership_grade: str,
    purchase_price: float | None,
    notes: str | None,
    small_hit: bool = False,
) -> dict[str, Any]:
    payload = {
        "card_id": card_id,
        "quantity": quantity,
        "ownership_grade": ownership_grade,
        "purchase_price": purchase_price,
        "notes": notes,
    }
    # Only sent when set, so adding regular cards works before
    # supabase/migrations/2026-10-30_small_hits.sql is run.
    if small_hit:
        payload["small_hit"] = True
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


# Set once the price_changes view turns out not to exist yet, so reads
# stop trying it until the backend restarts.
_price_changes_missing = False


def get_price_snapshots_for_cards(card_ids) -> dict[str, list[dict[str, Any]]]:
    """Each card's price history (oldest first) - no row cap, since the
    performance history needs every price change, not just the latest.

    Read from the price_changes view, which leaves out rows that only
    repeat the price before them (the history is identical, at about a
    quarter of the rows). Falls back to every row until
    supabase/migrations/2026-10-27_price_changes_view.sql is run."""
    global _price_changes_missing

    ids = list(card_ids)
    result: dict[str, list[dict[str, Any]]] = {card_id: [] for card_id in ids}
    if not ids:
        return result

    def query(table: str):
        return lambda: (
            _client().table(table)
            .select("*")
            .in_("card_id", ids)
            .order("observed_at")
            .order("id")
        )

    rows = None
    if not _price_changes_missing:
        try:
            rows = _fetch_all(query("price_changes"))
        except Exception as exc:
            if "price_changes" not in str(exc) and "PGRST205" not in str(exc):
                raise
            _price_changes_missing = True
            logging.getLogger("db").warning(
                "price_changes view doesn't exist yet; run "
                "supabase/migrations/2026-10-27_price_changes_view.sql"
            )

    if rows is None:
        rows = _fetch_all(query("price_snapshots"))

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


def insert_price_snapshots(rows: list[dict[str, Any]]) -> None:
    """Many snapshots in one request per 500 (a sync's worth), instead of a
    request each. Rows take insert_price_snapshot's arguments."""
    now = utc_now_iso()
    payload = [
        {
            "card_id": row["card_id"],
            "grade": row["grade"],
            "source": row["source"],
            "value": row["value"],
            "source_url": row.get("source_url"),
            "observed_at": row.get("observed_at") or now,
            "metadata": row.get("metadata") or {},
        }
        for row in rows
    ]

    for start in range(0, len(payload), 500):
        _client().table("price_snapshots").insert(payload[start:start + 500]).execute()


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
