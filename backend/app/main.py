import asyncio
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from . import db
from .config import settings
from .schemas import AddCollectionRequest, GradedValuesRequest
from .services import poketrace, price_sync
from .services.portfolio import build_dashboard
from .services.valuation import build_market_values


@asynccontextmanager
async def lifespan(_: FastAPI):
    scheduler = asyncio.create_task(price_sync.run_scheduler())

    try:
        yield
    finally:
        scheduler.cancel()
        with suppress(asyncio.CancelledError):
            await scheduler


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

    entries = []
    for item in items:
        card = db.get_card(item["card_id"])
        if not card:
            continue

        entries.append(
            {
                "item": item,
                "card": card,
                "snapshots": db.get_price_snapshots(card["id"]),
            }
        )

    return build_dashboard(entries)


@app.post("/api/collection")
def add_to_collection(
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

    # No price pull here: prices only come from PokeTrace on the twice-daily
    # schedule (services/price_sync.py), so a new card shows no value until
    # the next pull.
    return {"item": serialize_collection_item(item, card)}


@app.delete("/api/collection/{item_id}")
def remove_from_collection(item_id: str) -> dict[str, bool]:
    db.delete_collection_item(item_id)
    return {"deleted": True}


@app.get("/api/refresh-status")
def refresh_status() -> dict[str, Any]:
    return price_sync.get_status()


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
