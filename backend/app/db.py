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
