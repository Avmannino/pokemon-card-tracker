import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from . import db
from .config import settings
from .schemas import AddCollectionRequest, GradedValuesRequest, VariantChangeRequest
from .services import poketrace, price_sync
from .services.portfolio import build_dashboard, lot_price, value_change
from .services.valuation import (
    build_market_values,
    is_manual_entry,
    variant_label,
    variant_options,
)


logger = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title="Pokemon Card Tracker API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# The dashboard returns a chart series per range; compress it.
app.add_middleware(GZipMiddleware, minimum_size=1000)


def _variant_info(item: dict[str, Any], card: dict[str, Any]) -> dict[str, Any]:
    options = variant_options(card)
    distinct = {option.get("variant") for option in options}
    confirmed_at = item.get("variant_confirmed_at")

    return {
        "variant": card.get("variant"),
        "label": variant_label(card.get("variant")),
        # Other variants PokeTrace has for this exact printing (filled in by
        # the price sync); empty until known.
        "options": [
            {**option, "label": variant_label(option.get("variant"))}
            for option in options
        ],
        "confirmed_at": confirmed_at,
        "needs_confirmation": len(distinct) > 1 and not confirmed_at,
        "tracking_enabled": "variant_confirmed_at" in item,
    }


def serialize_collection_item(
    item: dict[str, Any],
    card: dict[str, Any],
    snapshots: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """`snapshots` newest first; fetched when not given."""
    if snapshots is None:
        snapshots = db.get_price_snapshots(card["id"])

    market_values = build_market_values(snapshots, card)

    owned_grade = item["ownership_grade"]
    owned_estimate = market_values.get(owned_grade, {}).get("estimate")
    quantity = int(item.get("quantity") or 1)

    return {
        **item,
        "card": card,
        "market_values": market_values,
        "owned_market_value_each": owned_estimate,
        # The price movement of the grade you own (raw or PSA) - never another
        # grade's movement standing in for it.
        "owned_grade_change": value_change(snapshots, owned_grade, card=card),
        "variant_info": _variant_info(item, card),
        "owned_market_value_total": (
            round(owned_estimate * quantity, 2)
            if owned_estimate is not None
            else None
        ),
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/search")
async def search(
    q: str = Query(min_length=2, max_length=100),
) -> dict[str, Any]:
    try:
        cards = await poketrace.search_cards(q, limit=20)
    except poketrace.PokeTraceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {"results": cards}


@app.get("/api/collection")
def list_collection() -> dict[str, Any]:
    """Reads saved data only - never calls a pricing API."""
    items = db.get_collection_items()
    card_ids = {item["card_id"] for item in items}
    cards = db.get_cards(card_ids)
    snapshots = db.get_price_snapshots_for_cards(card_ids)
    serialized = []

    for item in items:
        card = cards.get(item["card_id"])
        if card:
            serialized.append(
                serialize_collection_item(
                    item, card, list(reversed(snapshots.get(card["id"], [])))
                )
            )

    known_totals = [
        float(item["owned_market_value_total"])
        for item in serialized
        if item["owned_market_value_total"] is not None
    ]

    return {
        "items": serialized,
        "summary": {
            "item_count": len(serialized),
            "known_market_total": round(sum(known_totals), 2),
            "items_missing_owned_value": sum(
                1
                for item in serialized
                if item["owned_market_value_total"] is None
            ),
        },
    }


@app.get("/api/portfolio/dashboard")
def portfolio_dashboard() -> dict[str, Any]:
    items = db.get_collection_items()

    events_recorded = True
    try:
        events = db.get_collection_events()
    except Exception:
        # Table not created yet: current cards still chart from when each was
        # added, but nothing removed can be accounted for.
        logger.warning(
            "collection_events unavailable; run supabase/schema.sql",
            exc_info=True,
        )
        events = []
        events_recorded = False

    card_ids = {item["card_id"] for item in items} | {
        event["card_id"] for event in events
    }

    return build_dashboard(
        items=items,
        events=events,
        cards=db.get_cards(card_ids),
        snapshots_by_card=db.get_price_snapshots_for_cards(card_ids),
        events_recorded=events_recorded,
    )


def _record_add_event(item: dict[str, Any]) -> None:
    """Book the card's entry into the collection at its market value right
    now. If this fails (e.g. the table doesn't exist yet), the schema.sql
    backfill or the dashboard fall back to the row's creation time."""
    try:
        price = lot_price(
            db.get_price_snapshots(item["card_id"]),
            item["ownership_grade"],
            added_at=item["created_at"],
            moment=item["created_at"],
            card=db.get_card(item["card_id"]),
        )
        db.insert_collection_event(
            collection_item_id=item["id"],
            card_id=item["card_id"],
            grade=item["ownership_grade"],
            event_type="ADD",
            quantity=int(item["quantity"]),
            occurred_at=item["created_at"],
            market_value_per_card=price,
        )
    except Exception:
        logger.exception(
            "Couldn't record ADD event for collection item %s", item["id"]
        )


@app.post("/api/collection")
async def add_to_collection(
    request: AddCollectionRequest,
) -> dict[str, Any]:
    card = db.save_card(request.card.model_dump())
    item = db.add_collection_item(
        card_id=card["id"],
        quantity=request.quantity,
        ownership_grade=request.ownership_grade,
        purchase_price=request.purchase_price,
        notes=request.notes,
    )

    if request.variant_siblings:
        options = [option.model_dump() for option in request.variant_siblings]
        if not any(o["poketrace_id"] == card["poketrace_id"] for o in options):
            options.append(
                {
                    "poketrace_id": card["poketrace_id"],
                    "variant": card.get("variant"),
                    "image_url": card.get("image_url"),
                    "rarity": card.get("rarity"),
                }
            )
        db.update_card_variant_siblings(
            card["id"],
            {
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "source": "search",
                "variants": sorted(options, key=lambda o: (o.get("variant") or "", o["poketrace_id"])),
            },
        )

    if request.variant_confirmed:
        db.set_item_variant_confirmed(item["id"])

    # One PokeTrace call for the card you just added, so it shows a price
    # straight away instead of waiting for the next scheduled pull. This is
    # a deliberate single add, not a collection-wide refresh. If pricing is
    # unavailable, keep the card rather than losing the user's entry.
    price_error = None
    try:
        await price_sync.pull_card_raw_prices(card["id"])
    except Exception as exc:
        price_error = str(exc)
        logging.getLogger("main").exception(
            "Price fetch failed for newly added card %s", card["id"]
        )

    # Recorded after the price fetch, so the entry is valued at the price
    # you'd see for it right now.
    _record_add_event(item)

    card = db.get_card(card["id"])

    return {
        "item": serialize_collection_item(item, card),
        "price_error": price_error,
    }


@app.delete("/api/collection/{item_id}")
def remove_from_collection(
    item_id: str,
    quantity: int | None = Query(default=None, ge=1),
) -> dict[str, Any]:
    """Removes `quantity` copies (default: all) and books the removal at the
    cards' market value right now, so the performance chart treats it as a
    withdrawal instead of a loss and keeps the gains they had while owned."""
    item = db.get_collection_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Card not found in your collection.")

    event = _book_removal_or_refuse(item, quantity)

    held = int(item["quantity"])
    count = held if quantity is None else quantity

    try:
        if count == held:
            db.delete_collection_item(item_id)
        else:
            db.update_collection_item_quantity(item_id, held - count)
    except Exception:
        # Keep the history consistent with what's actually in the collection.
        db.delete_collection_event(event["id"])
        raise

    return {
        "removed": count,
        "remaining": held - count,
        "market_value_per_card": event.get("market_value_per_card"),
    }


def _ensure_add_event(item: dict[str, Any]) -> None:
    """Raises 503 if collection history isn't set up; records the ADD for a
    row that predates event tracking."""
    try:
        events = db.get_collection_events(item["id"])
    except Exception as exc:
        # Removing without recording it would erase this card's history, so
        # refuse rather than lose it.
        raise HTTPException(
            status_code=503,
            detail=(
                "Removal history isn't set up yet. Run the collection_events "
                "SQL in supabase/schema.sql, then try again."
            ),
        ) from exc

    if not any(event["event_type"] == "ADD" for event in events):
        # Added before event tracking and not backfilled yet: record when it
        # was added first, so the removal has a lot to come out of.
        removed_before = sum(int(event["quantity"]) for event in events)
        db.insert_collection_event(
            collection_item_id=item["id"],
            card_id=item["card_id"],
            grade=item["ownership_grade"],
            event_type="ADD",
            quantity=int(item["quantity"]) + removed_before,
            occurred_at=item["created_at"],
            market_value_per_card=None,
            recorded_by="backfill",
        )


def _book_removal_or_refuse(item: dict[str, Any], quantity: int | None) -> dict[str, Any]:
    """Records a REMOVE of `quantity` copies (default all) at the market
    value right now. Returns the event."""
    held = int(item["quantity"])
    count = held if quantity is None else quantity
    if count > held:
        raise HTTPException(
            status_code=400,
            detail=f"You only have {held} of this card.",
        )

    _ensure_add_event(item)

    now = datetime.now(timezone.utc)
    price = lot_price(
        db.get_price_snapshots(item["card_id"]),
        item["ownership_grade"],
        added_at=item["created_at"],
        moment=now,
        card=db.get_card(item["card_id"]),
    )

    return db.insert_collection_event(
        collection_item_id=item["id"],
        card_id=item["card_id"],
        grade=item["ownership_grade"],
        event_type="REMOVE",
        quantity=count,
        occurred_at=now.isoformat(),
        market_value_per_card=price,
    )


@app.post("/api/collection/{item_id}/variant")
async def set_collection_variant(
    item_id: str,
    request: VariantChangeRequest,
) -> dict[str, Any]:
    """Confirms or corrects which variant of a printing you own.

    Same variant: just marks it confirmed. Different variant: books the
    correction like a trade - the wrong variant leaves the collection at its
    market value and the right one joins at its own - so the performance
    chart shows no gain or loss from the correction, and each card's price
    history stays one variant. Old snapshots are left untouched on the old
    card; prices you entered by hand for graded grades come along, since they
    describe the card you actually own. Costs one PokeTrace request."""
    item = db.get_collection_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Card not found in your collection.")

    card = db.get_card(item["card_id"])
    if not card:
        raise HTTPException(status_code=404, detail="Card not found.")

    if request.poketrace_id == card["poketrace_id"]:
        if not db.set_item_variant_confirmed(item_id):
            raise HTTPException(
                status_code=503,
                detail=(
                    "Variant confirmation isn't set up yet. Run "
                    "supabase/migrations/2026-10-10_variant_identity.sql, then try again."
                ),
            )
        return {"changed": False, "item": serialize_collection_item(db.get_collection_item(item_id), card)}

    _ensure_add_event(item)

    try:
        remote = await poketrace.get_card(request.poketrace_id)
    except poketrace.PokeTraceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if (
        str(remote.get("id")) != request.poketrace_id
        or poketrace.printing_key(remote) != poketrace.saved_printing_key(card)
    ):
        raise HTTPException(
            status_code=400,
            detail="That's not a variant of the same card (set and number must match).",
        )

    new_card = db.save_card(poketrace.normalize_card(remote))
    price_sync.store_raw_prices(new_card, remote)

    if card.get("variant_siblings"):
        db.update_card_variant_siblings(new_card["id"], card["variant_siblings"])

    # Carry your manual graded prices over (newest per grade), marked with
    # where they came from. Automated prices stay with the variant they
    # were recorded for.
    carried = {}
    for snapshot in db.get_price_snapshots(card["id"]):
        if snapshot["grade"] != "RAW" and is_manual_entry(snapshot):
            carried.setdefault((snapshot["grade"], snapshot["source"]), snapshot)

    for snapshot in carried.values():
        db.insert_price_snapshot(
            card_id=new_card["id"],
            grade=snapshot["grade"],
            source=snapshot["source"],
            value=float(snapshot["value"]),
            source_url=snapshot.get("source_url"),
            observed_at=snapshot["observed_at"],
            metadata={
                **(snapshot.get("metadata") or {}),
                "entry_method": "manual",
                "carried_from_card_id": card["id"],
                "carried_from_snapshot_id": snapshot.get("id"),
            },
        )

    new_item = db.add_collection_item(
        card_id=new_card["id"],
        quantity=int(item["quantity"]),
        ownership_grade=item["ownership_grade"],
        purchase_price=item.get("purchase_price"),
        notes=item.get("notes"),
    )
    _record_add_event(new_item)

    try:
        remove_event = _book_removal_or_refuse(item, None)
        try:
            db.delete_collection_item(item_id)
        except Exception:
            db.delete_collection_event(remove_event["id"])
            raise
    except Exception:
        # Undo the new row so you don't end up holding both variants.
        db.delete_collection_item(new_item["id"])
        for event in db.get_collection_events(new_item["id"]):
            db.delete_collection_event(event["id"])
        raise

    db.set_item_variant_confirmed(new_item["id"])

    return {
        "changed": True,
        "item": serialize_collection_item(
            db.get_collection_item(new_item["id"]) or new_item,
            db.get_card(new_card["id"]),
        ),
    }


@app.get("/api/refresh-status")
def refresh_status() -> dict[str, Any]:
    return price_sync.get_status()


@app.post("/api/refresh-now", status_code=202)
async def refresh_now() -> dict[str, bool]:
    """Manual "Refresh Prices": starts a real sync (raw + graded) in the
    background; progress is reported by /api/refresh-status."""
    try:
        price_sync.start_manual_sync()
    except price_sync.SyncBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except price_sync.SyncCooldown as exc:
        minutes = -(-exc.retry_after // 60)
        raise HTTPException(
            status_code=429,
            detail=(
                "Prices were synced recently. You can sync again in "
                f"{minutes} min."
            ),
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc

    return {"started": True}


@app.post("/api/cards/{card_id}/graded-values")
def save_graded_values(
    card_id: str,
    request: GradedValuesRequest,
) -> dict[str, Any]:
    card = db.get_card(card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Card not found.")

    grade_map = {
        "PSA_7": request.psa_7,
        "PSA_8": request.psa_8,
        "PSA_9": request.psa_9,
        "PSA_10": request.psa_10,
    }

    saved = []
    for grade, value in grade_map.items():
        if value is None:
            continue

        saved.append(
            db.insert_price_snapshot(
                card_id=card_id,
                grade=grade,
                source=request.source,
                value=value,
                source_url=request.source_url,
                metadata={"entry_method": "manual"},
            )
        )

    if not saved:
        raise HTTPException(
            status_code=400,
            detail="Enter at least one PSA value.",
        )

    snapshots = db.get_price_snapshots(card_id)
    return {
        "saved": len(saved),
        "market_values": build_market_values(snapshots),
    }
