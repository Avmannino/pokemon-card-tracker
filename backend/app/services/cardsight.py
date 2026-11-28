"""CardSight AI (https://api.cardsight.ai): the sports card catalog and eBay
sold prices behind the Sports section.

Free plan: 750 calls a month, 4 a second. A catalog search is one call, and
one pricing request covers up to 100 cards, so the whole collection
reprices in one or two calls. The app counts its own calls per calendar
month (app_state "cardsight_usage") and stops short of the limit.
CardSight's dashboard has the authoritative count, which also includes
calls made outside the app.

Terms (cardsight.ai/terms, Section 3): CardSight ids may be kept on your
records. Everything else (card details, prices, images) may only be cached
short-term and must be refreshed regularly. The daily refresh overwrites
each card's cached prices, so no price history is kept, and images are
only held in memory for a day.
"""

import asyncio
import re
import statistics
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from ..config import settings
from . import state_store


BASE_URL = "https://api.cardsight.ai"

MONTHLY_CALLS = 750
# The app stops making calls here, leaving room for calls it doesn't see
# (e.g. testing from CardSight's playground).
STOP_AT = 735
USAGE_KEY = "cardsight_usage"

# Prices are completed eBay auctions from the past year. CardSight returns
# at most 50 sales per card (the most recent, across raw and graded).
PRICING_PERIOD = "1y"
PRICING_LIMIT = 50
PRICING_BATCH = 100
# A grade's value is the median of its most recent sales, up to this many.
RECENT_SALES = 10

# Multi-card lots and sets aren't the price of one card.
LOT_TITLE = re.compile(
    r"\b(lots?|set of|complete set|team set|\d+\s*cards)\b", re.IGNORECASE
)

# "Ken Griffey Jr." is "Ken Griffey, Jr." in the catalog, so a search that
# finds nothing is retried by surname without these.
NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}

# Stay under 4 requests a second.
MIN_SPACING_SECONDS = 0.3


class CardSightError(Exception):
    pass


class CardSightUnavailable(CardSightError):
    """No API key, or this month's calls are used up."""


def configured() -> bool:
    return bool(settings.cardsight_api_key)


def _month(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m")


def usage() -> dict[str, Any]:
    stored = state_store.get(USAGE_KEY) or {}
    month = _month()
    calls = int(stored.get("calls") or 0) if stored.get("month") == month else 0
    return {
        "month": month,
        "calls": calls,
        "limit": MONTHLY_CALLS,
        "remaining": max(0, MONTHLY_CALLS - calls),
    }


def _count_call() -> None:
    current = usage()
    state_store.set(USAGE_KEY, {"month": current["month"], "calls": current["calls"] + 1})


def _check_budget() -> None:
    if not configured():
        raise CardSightUnavailable(
            "Sports pricing isn't set up yet: add CARDSIGHT_API_KEY to the backend environment."
        )
    if usage()["calls"] >= STOP_AT:
        raise CardSightUnavailable(
            f"This month's CardSight calls are used up ({STOP_AT} of {MONTHLY_CALLS} "
            "kept for the app). Sports prices resume on the 1st."
        )


_lock = asyncio.Lock()
_last_call = 0.0


async def _request(method: str, path: str, **kwargs) -> httpx.Response:
    """One call to CardSight, paced under its rate limit and counted toward
    this month's calls. Retries twice after a 429."""
    global _last_call
    _check_budget()

    async with _lock:
        for attempt in range(3):
            wait = MIN_SPACING_SECONDS - (time.monotonic() - _last_call)
            if wait > 0:
                await asyncio.sleep(wait)

            try:
                async with httpx.AsyncClient(
                    base_url=BASE_URL,
                    timeout=45,
                    headers={"X-API-Key": settings.cardsight_api_key},
                ) as client:
                    response = await client.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                raise CardSightError(f"Couldn't reach CardSight: {exc}") from exc
            finally:
                _last_call = time.monotonic()

            if response.status_code == 429 and attempt < 2:
                try:
                    retry_after = float(response.headers.get("Retry-After") or 1)
                except ValueError:
                    retry_after = 1.0
                await asyncio.sleep(min(max(retry_after, 0.5), 10))
                continue

            break

    if response.status_code != 429:
        _count_call()

    if response.status_code in (401, 403):
        raise CardSightError("CardSight rejected the API key (check CARDSIGHT_API_KEY).")
    if response.status_code == 429:
        raise CardSightError("CardSight is rate limiting requests; try again in a minute.")
    if response.status_code >= 400 and response.status_code != 404:
        raise CardSightError(f"CardSight returned HTTP {response.status_code}.")

    return response


def _words(text: str | None) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def league(attributes: list[str] | None) -> str | None:
    """The league from a team attribute, e.g. "MLB-KCR" -> "MLB"."""
    for attribute in attributes or []:
        match = re.fullmatch(r"([A-Z]{2,5})-[A-Z0-9]{2,5}", str(attribute))
        if match:
            return match.group(1)
    return None


def normalize_card(card: dict[str, Any]) -> dict[str, Any]:
    """A catalog card (GET /v1/catalog/cards) as the app uses it."""
    attributes = [str(a) for a in card.get("attributes") or []]
    return {
        "cardsight_id": card["id"],
        "name": card.get("name") or "Card",
        "number": card.get("number"),
        "year": card.get("releaseYear"),
        "release_name": card.get("releaseName"),
        "set_name": card.get("setName"),
        "attributes": attributes,
        "league": league(attributes),
    }


def _number_key(number: str | None) -> tuple[int, str]:
    digits = re.match(r"\d+", number or "")
    return (int(digits.group()) if digits else 10**9, number or "")


async def _catalog(params: dict[str, Any]) -> list[dict[str, Any]]:
    response = await _request("GET", "/v1/catalog/cards", params=params)
    if response.status_code == 404:
        return []
    return response.json().get("cards") or []


async def search_cards(
    name: str,
    year: str | None = None,
    number: str | None = None,
    set_query: str | None = None,
) -> list[dict[str, Any]]:
    """Base cards matching a player (one call, two if the name needs a
    retry). `set_query` (e.g. "stadium club") ranks matching releases first
    without another call."""
    params: dict[str, Any] = {"name": name.strip(), "take": 100}
    if year:
        params["year"] = year.strip()
    if number:
        params["number"] = number.strip().lstrip("#").strip()

    cards = await _catalog(params)

    if not cards:
        # Words as typed, so the retry keeps their capitals.
        typed = re.findall(r"[A-Za-z0-9]+", name)
        words = [word for word in typed if word.lower() not in NAME_SUFFIXES]
        if words and len(words) < len(typed) or len(words) > 1:
            retried = await _catalog({**params, "name": words[-1]})
            wanted = {word.lower() for word in words}
            cards = [
                card for card in retried
                if wanted <= set(_words(card.get("name")))
            ]

    results = [normalize_card(card) for card in cards if card.get("id")]

    set_words = set(_words(set_query))

    def set_match(card: dict[str, Any]) -> bool:
        if not set_words:
            return True
        text = " ".join(
            str(card.get(key) or "") for key in ("year", "release_name", "set_name")
        )
        return set_words <= set(_words(text))

    results.sort(
        key=lambda card: (
            not set_match(card),
            card.get("year") or "",
            (card.get("release_name") or "").lower(),
            (card.get("set_name") or "").lower() != "base set",
            (card.get("set_name") or "").lower(),
            _number_key(card.get("number")),
        )
    )
    return results


def grade_key(company: str | None, grade_value: Any) -> str:
    """("PSA", "10") -> "PSA_10"; ("BGS", "9.5") -> "BGS_9.5"."""
    value = str(grade_value).strip()
    if value.endswith(".0"):
        value = value[:-2]
    return f"{(company or '').strip().upper()}_{value}"


def _sales(records: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    sales = [
        record for record in records or []
        if isinstance(record.get("price"), (int, float))
        and record["price"] > 0
        and not LOT_TITLE.search(record.get("title") or "")
    ]
    sales.sort(key=lambda record: record.get("date") or "", reverse=True)
    return sales


def _grade_summary(records: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    sales = _sales(records)
    if not sales:
        return None

    recent = sales[:RECENT_SALES]
    return {
        "value": round(statistics.median(record["price"] for record in recent), 2),
        "sales": len(sales),
        "used": len(recent),
        "last_sale": sales[0].get("date"),
        "recent": [
            {"price": record["price"], "date": record.get("date"), "url": record.get("url")}
            for record in sales[:5]
        ],
    }


def summarize(data: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """One card's pricing response (POST /v1/pricing/ results[].data) as the
    app caches it: each grade's value (median of its recent auction sales),
    a photo from a raw sale, and the card details to refresh."""
    raw_records = (data.get("raw") or {}).get("records") or []
    grades: dict[str, Any] = {}

    raw = _grade_summary(raw_records)
    if raw:
        grades["RAW"] = raw

    for company in data.get("graded") or []:
        for grade in company.get("grades") or []:
            summary = _grade_summary(grade.get("records"))
            if summary:
                grades[grade_key(company.get("company_name"), grade.get("grade_value"))] = summary

    photo = next(
        (record.get("image_url") for record in _sales(raw_records) if record.get("image_url")),
        None,
    )

    card = data.get("card") or {}
    card_set = card.get("set") or {}
    return {
        "card": {
            "name": card.get("name"),
            "number": card.get("number"),
            "year": card_set.get("year"),
            "release_name": card_set.get("release"),
            "set_name": card_set.get("name"),
        },
        "grades": grades,
        "photo_url": photo,
        "last_sale_date": (data.get("meta") or {}).get("last_sale_date"),
        "period": PRICING_PERIOD,
        "priced_at": (now or datetime.now(timezone.utc)).isoformat(),
    }


async def get_prices(cardsight_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Price summaries by CardSight id, one call per 100 cards. Cards
    CardSight couldn't price are left out."""
    summaries: dict[str, dict[str, Any]] = {}
    ids = list(dict.fromkeys(cardsight_ids))

    for start in range(0, len(ids), PRICING_BATCH):
        response = await _request(
            "POST",
            "/v1/pricing/",
            json={
                "card_ids": ids[start:start + PRICING_BATCH],
                "period": PRICING_PERIOD,
                "listing_type": "auction",
                "limit": PRICING_LIMIT,
            },
        )
        if response.status_code == 404:
            continue

        for result in response.json().get("results") or []:
            if result.get("success") and result.get("data") and result.get("card_id"):
                summaries[result["card_id"]] = summarize(result["data"])

    return summaries


async def get_image(cardsight_id: str) -> tuple[bytes, str] | None:
    """A card's catalog image (JPEG), or None if CardSight has none."""
    response = await _request(
        "GET", f"/v1/images/cards/{cardsight_id}", params={"format": "raw"}
    )
    if response.status_code == 404 or not response.content:
        return None
    return response.content, response.headers.get("content-type") or "image/jpeg"
