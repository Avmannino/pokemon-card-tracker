import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from . import db
from .config import settings
from .schemas import AddCollectionRequest, GradedValuesRequest
from .services import poketrace, price_sync
from .services.portfolio import build_dashboard, lot_price, value_change
from .services.valuation import build_market_values


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


def serialize_collection_item(
    item: dict[str, Any],
    card: dict[str, Any],
) -> dict[str, Any]:
    snapshots = db.get_price_snapshots(card["id"])
    market_values = build_market_values(snapshots)

    owned_grade = item["ownership_grade"]
    owned_estimate = market_values.get(owned_grade, {}).get("estimate")
    quantity = int(item.get("quantity") or 1)

    return {
        **item,
        "card": card,
        "market_values": market_values,
        "owned_market_value_each": owned_estimate,
        # Always the raw price's movement, whatever grade is owned: graded
        # values are entered by hand or pulled sparsely, so they rarely have
        # enough history to show a change.
        "week_change": value_change(snapshots, "RAW"),
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
    items = db.get_collection_items()
    serialized = []

    for item in items:
        card = db.get_card(item["card_id"])
        if card:
            serialized.append(serialize_collection_item(item, card))

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

    held = int(item["quantity"])
    count = held if quantity is None else quantity
    if count > held:
        raise HTTPException(
            status_code=400,
            detail=f"You only have {held} of this card.",
        )

    try:
        events = db.get_collection_events(item_id)
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
            collection_item_id=item_id,
            card_id=item["card_id"],
            grade=item["ownership_grade"],
            event_type="ADD",
            quantity=held + removed_before,
            occurred_at=item["created_at"],
            market_value_per_card=None,
            recorded_by="backfill",
        )

    now = datetime.now(timezone.utc)
    price = lot_price(
        db.get_price_snapshots(item["card_id"]),
        item["ownership_grade"],
        added_at=item["created_at"],
        moment=now,
    )

    event = db.insert_collection_event(
        collection_item_id=item_id,
        card_id=item["card_id"],
        grade=item["ownership_grade"],
        event_type="REMOVE",
        quantity=count,
        occurred_at=now.isoformat(),
        market_value_per_card=price,
    )

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
        "market_value_per_card": price,
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
