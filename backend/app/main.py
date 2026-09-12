import asyncio
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from . import db
from .config import settings
from .schemas import AddCollectionRequest, GradedValuesRequest
from .services import poketrace
from .services.portfolio import build_dashboard
from .services.valuation import build_market_values


app = FastAPI(
    title="Pokemon Card Tracker API",
    version="0.1.0",
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


async def refresh_card_raw_prices(card_id: str) -> dict[str, Any]:
    card = db.get_card(card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Card not found.")

    try:
        remote_card = await poketrace.get_card(card["poketrace_id"])
        raw_sources = poketrace.extract_raw_sources(remote_card)
    except poketrace.PokeTraceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    saved = []
    for raw_source in raw_sources:
        snapshot = db.insert_price_snapshot(
            card_id=card_id,
            grade="RAW",
            source=raw_source["source"],
            value=raw_source["value"],
            source_url=raw_source.get("source_url"),
            observed_at=raw_source.get("observed_at"),
            metadata=raw_source.get("metadata"),
        )
        saved.append(snapshot)

    # Update mutable catalog information too.
    normalized = poketrace.normalize_card(remote_card)
    normalized["poketrace_id"] = card["poketrace_id"]
    db.save_card(normalized)

    snapshots = db.get_price_snapshots(card_id)
    return {
        "saved_sources": len(saved),
        "market_values": build_market_values(snapshots),
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

    # Get raw market pricing immediately. If the pricing provider is temporarily
    # unavailable, keep the collection item and return it rather than losing the
    # user's work.
    refresh_error = None
    try:
        await refresh_card_raw_prices(card["id"])
    except HTTPException as exc:
        refresh_error = exc.detail

    card = db.get_card(card["id"])
    response_item = serialize_collection_item(item, card)

    return {
        "item": response_item,
        "refresh_error": refresh_error,
    }


@app.delete("/api/collection/{item_id}")
def remove_from_collection(item_id: str) -> dict[str, bool]:
    db.delete_collection_item(item_id)
    return {"deleted": True}


@app.post("/api/cards/{card_id}/refresh")
async def refresh_card(card_id: str) -> dict[str, Any]:
    return await refresh_card_raw_prices(card_id)


@app.post("/api/refresh-all")
async def refresh_all() -> dict[str, Any]:
    items = db.get_collection_items()

    unique_card_ids = []
    seen = set()
    for item in items:
        card_id = item["card_id"]
        if card_id not in seen:
            seen.add(card_id)
            unique_card_ids.append(card_id)

    # Leave a little room under PokeTrace's 250/day Free limit for searches
    # and manual refreshes.
    max_cards = 220
    card_ids = unique_card_ids[:max_cards]

    results = []
    for index, card_id in enumerate(card_ids):
        try:
            data = await refresh_card_raw_prices(card_id)
            results.append(
                {
                    "card_id": card_id,
                    "ok": True,
                    "saved_sources": data["saved_sources"],
                }
            )
        except HTTPException as exc:
            results.append(
                {
                    "card_id": card_id,
                    "ok": False,
                    "error": str(exc.detail),
                }
            )

        # Free PokeTrace burst limit is 1 request / 2 seconds.
        if index < len(card_ids) - 1:
            await asyncio.sleep(2.1)

    return {
        "attempted": len(card_ids),
        "skipped_due_to_daily_safety_limit": max(
            0,
            len(unique_card_ids) - max_cards,
        ),
        "results": results,
    }


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
