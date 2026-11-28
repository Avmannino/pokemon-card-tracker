"""Sports section endpoints (/api/sports/...).

Calls to CardSight are kept to what you ask for: a search when you press
Search, prices when you check cards or the daily refresh runs. Loading the
section reads saved data only. Short-lived in-memory caches stop repeat
calls for the same search, card or image.
"""

import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response

from . import db
from .config import settings
from .schemas import (
    SportsAddRequest,
    SportsItemUpdate,
    SportsManualAddRequest,
    SportsPricesRequest,
)
from .services import cardsight, grading, sports


router = APIRouter(prefix="/api/sports")

SEARCH_CACHE_SECONDS = 3600
PRICE_CACHE_SECONDS = 6 * 3600
# A saved card priced this recently isn't priced again when checked.
SAVED_PRICES_FRESH_SECONDS = 24 * 3600
IMAGE_CACHE_SECONDS = 24 * 3600
CACHE_SIZE = 300

_search_cache: dict[tuple, tuple[float, list[dict[str, Any]]]] = {}
_price_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_image_cache: dict[str, tuple[float, bytes, str]] = {}


def _remember(cache: dict, key, value) -> None:
    if len(cache) >= CACHE_SIZE:
        cache.pop(min(cache, key=lambda k: cache[k][0]))
    cache[key] = value


def _fresh(entry, max_age: float) -> bool:
    return entry is not None and time.monotonic() - entry[0] < max_age


def _cost(grading_cost: float | None) -> float:
    return settings.grading_cost if grading_cost is None else grading_cost


def _age_seconds(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()
    except ValueError:
        return None


def _status() -> dict[str, Any]:
    return {
        "configured": cardsight.configured(),
        "usage": cardsight.usage(),
        "last_refresh": sports.last_refresh(),
    }


@router.get("/collection")
def sports_collection(
    grading_cost: float | None = Query(default=None, ge=0, le=1000),
) -> dict[str, Any]:
    """Your sports cards from saved data only - never calls CardSight."""
    cost = _cost(grading_cost)
    items = db.get_sports_items()
    cards = db.get_sports_cards({item["sports_card_id"] for item in items})
    return {
        **sports.build_collection(items, cards, cost),
        **_status(),
        "grading_cost": round(cost, 2),
        "default_grading_cost": round(settings.grading_cost, 2),
        "score_explanation": grading.SCORE_EXPLANATION,
    }


@router.get("/search")
async def sports_search(
    name: str = Query(min_length=2, max_length=100),
    year: str | None = Query(default=None, max_length=4),
    number: str | None = Query(default=None, max_length=20),
    set: str | None = Query(default=None, max_length=100),
) -> dict[str, Any]:
    """Cards matching a player (one CardSight call; repeats within the hour
    are free)."""
    key = tuple(" ".join((value or "").lower().split()) for value in (name, year, number, set))
    cached = _search_cache.get(key)
    if _fresh(cached, SEARCH_CACHE_SECONDS):
        return {"results": cached[1], "usage": cardsight.usage()}

    results = await cardsight.search_cards(name, year or None, number or None, set or None)
    _remember(_search_cache, key, (time.monotonic(), results))
    return {"results": results, "usage": cardsight.usage()}


async def _prices_for(cardsight_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Price summaries from the freshest place available: a card you own
    priced in the last day, a recent check, or one CardSight call for the
    rest."""
    found: dict[str, dict[str, Any]] = {}

    for cardsight_id, card in db.get_sports_cards_by_cardsight_ids(cardsight_ids).items():
        age = _age_seconds(card.get("priced_at"))
        if card.get("prices") and age is not None and age < SAVED_PRICES_FRESH_SECONDS:
            found[cardsight_id] = card["prices"]

    for cardsight_id in cardsight_ids:
        cached = _price_cache.get(cardsight_id)
        if cardsight_id not in found and _fresh(cached, PRICE_CACHE_SECONDS):
            found[cardsight_id] = cached[1]

    missing = [cardsight_id for cardsight_id in cardsight_ids if cardsight_id not in found]
    if missing:
        fetched = await cardsight.get_prices(missing)
        for cardsight_id in missing:
            # Cards with no sales are remembered too, so they aren't asked
            # about again within the hour.
            summary = fetched.get(cardsight_id) or {"grades": {}, "photo_url": None, "card": {}}
            _remember(_price_cache, cardsight_id, (time.monotonic(), summary))
            found[cardsight_id] = summary

    return found


@router.post("/prices")
async def sports_prices(
    request: SportsPricesRequest,
    grading_cost: float | None = Query(default=None, ge=0, le=1000),
) -> dict[str, Any]:
    """Values and grading outlook for up to 100 cards, owned or not (at most
    one CardSight call)."""
    cost = _cost(grading_cost)
    ids = list(dict.fromkeys(request.cardsight_ids))
    summaries = await _prices_for(ids)

    prices = {}
    for cardsight_id, summary in summaries.items():
        card = {
            "cardsight_id": cardsight_id,
            **(summary.get("card") or {}),
            "attributes": [],
            "prices": summary,
        }
        prices[cardsight_id] = {
            "grades": summary.get("grades") or {},
            "photo_url": summary.get("photo_url"),
            "last_sale_date": summary.get("last_sale_date"),
            "grading": sports.grading_outlook(card, cost),
        }

    return {"prices": prices, "usage": cardsight.usage(), "grading_cost": round(cost, 2)}


@router.post("/items")
async def add_sports_item(request: SportsAddRequest) -> dict[str, Any]:
    """Adds a card to your sports collection, priced from a recent check
    (free) or one CardSight call. Adding a card you already have in the same
    grade adds to its quantity."""
    card_ref = request.card.model_dump()
    price_error = None
    summary = None
    try:
        summary = (await _prices_for([request.card.cardsight_id])).get(request.card.cardsight_id)
    except cardsight.CardSightError as exc:
        price_error = str(exc)

    card = db.save_sports_card(card_ref, summary)

    existing = next(
        (
            item for item in db.get_sports_items()
            if item["sports_card_id"] == card["id"]
            and item["ownership_grade"] == request.ownership_grade
        ),
        None,
    )
    if existing:
        item = db.update_sports_item(
            existing["id"], {"quantity": int(existing["quantity"]) + request.quantity}
        )
        merged = True
    else:
        item = db.add_sports_item(
            sports_card_id=card["id"],
            quantity=request.quantity,
            ownership_grade=request.ownership_grade,
            notes=request.notes,
            manual_value=request.manual_value,
            manual_source_url=(request.manual_source_url or "").strip() or None,
        )
        merged = False

    # Too few CardSight sales for the grade you own: recent sales by title.
    if sports.needs_sales_lookup(card, {request.ownership_grade}):
        await sports.update_sales_prices([card])

    sports.record_value("add")
    return {"item": item, "merged": merged, "price_error": price_error}


def _clean(text: str | None) -> str | None:
    return (text or "").strip() or None


@router.post("/manual-items")
async def add_manual_sports_item(request: SportsManualAddRequest) -> dict[str, Any]:
    """Adds a card CardSight doesn't have. Never sent to CardSight; valued
    from recent sales found by title (The Card API) when there are any,
    or by your own value, which always wins when set."""
    card = db.save_sports_card(
        {
            "cardsight_id": f"{sports.MANUAL_PREFIX}{uuid.uuid4().hex}",
            "name": request.name.strip(),
            "year": _clean(request.year),
            "release_name": _clean(request.release_name),
            "set_name": _clean(request.set_name),
            "number": _clean((request.number or "").lstrip("#")),
            "attributes": [request.league] if request.league else [],
        }
    )
    item = db.add_sports_item(
        sports_card_id=card["id"],
        quantity=request.quantity,
        ownership_grade=request.ownership_grade,
        notes=_clean(request.notes),
        manual_value=request.manual_value,
        manual_source_url=_clean(request.manual_source_url),
    )
    sales, note = await sports.update_sales_prices([card])
    sports.record_value("add")
    found = bool((sales.get(card["id"]) or {}).get("grades"))
    return {"item": item, "sales_found": found, "sales_note": note}


@router.patch("/items/{item_id}")
async def update_sports_item(item_id: str, request: SportsItemUpdate) -> dict[str, Any]:
    item = db.get_sports_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Card not found in your sports collection.")

    card = db.get_sports_cards([item["sports_card_id"]]).get(item["sports_card_id"]) or {}
    manual_card = sports.is_manual_card(card)

    fields = {}
    for field in request.model_fields_set - {"card"}:
        value = getattr(request, field)
        if field in ("quantity", "ownership_grade") and value is None:
            continue
        if field == "manual_source_url":
            value = _clean(value)
        fields[field] = value

    if "manual_value" in fields and fields["manual_value"] is None:
        fields["manual_source_url"] = None

    card_details = None
    if request.card is not None:
        if not manual_card:
            raise HTTPException(
                status_code=400, detail="Only cards you added by hand can have their details changed."
            )
        details = request.card
        card_details = {
            "name": details.name.strip(),
            "year": _clean(details.year),
            "release_name": _clean(details.release_name),
            "set_name": _clean(details.set_name),
            "number": _clean((details.number or "").lstrip("#")),
            "attributes": [details.league] if details.league else [],
        }

    if not fields and card_details is None:
        raise HTTPException(status_code=400, detail="Nothing to change.")

    if card_details is not None:
        db.update_sports_card_details(item["sports_card_id"], card_details)
        # Corrected details mean a different title search.
        await sports.update_sales_prices([{**card, **card_details}])
    if fields:
        item = db.update_sports_item(item_id, fields)
    sports.record_value("quantity" if set(fields) == {"quantity"} else "edit")
    return {"item": item}


@router.delete("/items/{item_id}")
def delete_sports_item(item_id: str) -> dict[str, Any]:
    """Removes the card; once no copies are left, its cached CardSight data
    goes too."""
    item = db.get_sports_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Card not found in your sports collection.")

    db.delete_sports_item(item_id)
    if not db.sports_card_in_use(item["sports_card_id"]):
        db.delete_sports_card(item["sports_card_id"])

    sports.record_value("remove")
    return {"removed": True}


@router.post("/refresh")
async def refresh_sports_prices(
    reason: str = Query(default="manual", pattern="^(manual|scheduled)$"),
) -> dict[str, Any]:
    """Reprices every sports card you own (one CardSight call per 100
    cards). Run daily by .github/workflows/sports-prices.yml, or by the
    section's Refresh button (at most every 10 minutes)."""
    try:
        record = await sports.refresh_prices(reason)
    except sports.RefreshBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except sports.RefreshCooldown as exc:
        raise HTTPException(
            status_code=429,
            detail=f"Sports prices were refreshed recently. Try again in {-(-exc.retry_after // 60)} min.",
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc

    return {**record, "usage": cardsight.usage()}


@router.get("/image/{cardsight_id}")
async def sports_card_image(cardsight_id: str) -> Response:
    """CardSight's catalog image for a card with no sale photo, held in
    memory for a day (CardSight allows only short-term caching)."""
    if len(cardsight_id) != 36:
        raise HTTPException(status_code=404, detail="No image.")

    cached = _image_cache.get(cardsight_id)
    if not _fresh(cached, IMAGE_CACHE_SECONDS):
        image = await cardsight.get_image(cardsight_id)
        if image is None:
            raise HTTPException(status_code=404, detail="No image.")
        cached = (time.monotonic(), *image)
        _remember(_image_cache, cardsight_id, cached)

    return Response(
        cached[1],
        media_type=cached[2],
        headers={"Cache-Control": "private, max-age=86400"},
    )
