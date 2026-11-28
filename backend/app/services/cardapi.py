"""The Card API (https://www.thecardapi.com): recent eBay and auction-house
sales found by title, for sports cards CardSight can't value - cards added
by hand (sets missing from CardSight's catalog) and cards CardSight has
fewer than 3 sales for.

Free plan: 5,000 sale records a day (counted by records returned, reset at
00:00 UTC), sales from the last 30 days. Its terms don't allow storing the
sale records, so they're only read in memory: the app keeps its own value
per grade (the median of recent confirmed sale prices) and how many sales it
came from, never the sales themselves.
"""

import asyncio
import re
import statistics
from datetime import datetime, timezone
from typing import Any

import httpx

from ..config import settings
from .cardsight import SIGNED_TITLE


BASE_URL = "https://www.thecardapi.com/api/v1/market"

# Sales read per card; a whole collection fits in the daily allowance.
SALES_PER_CARD = 40
# Stop a run with this many of the day's records left.
DAILY_RESERVE = 100
# A grade's value is the median of its most recent sales, up to this many.
RECENT_SALES = 10
PERIOD = "30d"

# Not one card at its own price: lots, sets, reprints.
EXCLUDED_TITLE = re.compile(
    r"\b(lots?|set of|complete set|team set|\d+\s*cards|reprints?|rp|custom|facsimile|proofs?|oversized?|uncut)\b",
    re.IGNORECASE,
)
GRADED_TITLE = re.compile(r"\b(PSA|BGS|SGC|CGC|CSG|BVG|HGA|KSA|GMA)\s*(?:GEM\s*MT\s*|MINT\s*)?(\d{1,2}(?:\.5)?)\b", re.IGNORECASE)

NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "mc", "rc"}
SET_NOISE = {
    "topps", "upper", "deck", "donruss", "fleer", "bowman", "panini", "leaf",
    "base", "set", "series", "the", "and", "of",
}


class CardApiError(Exception):
    pass


class DailyLimitReached(CardApiError):
    pass


def configured() -> bool:
    return bool(settings.cardapi_key)


def _words(text: str | None) -> list[str]:
    return re.findall(r"[a-z0-9é]+", (text or "").lower())


def _surname(name: str | None) -> str | None:
    # "Frank Thomas / Will Clark" -> the first player.
    first = re.split(r"\s*/\s*", name or "")[0]
    words = [word for word in _words(first) if word not in NAME_SUFFIXES]
    return words[-1] if words else None


def _set_words(card: dict[str, Any]) -> list[str]:
    """The words that pick out the card's set: the subset when there is one
    ("Members Choice"), else the set ("Major League Debut")."""
    subset = card.get("set_name") if (card.get("set_name") or "").lower() != "base set" else None
    for text in (subset, card.get("release_name")):
        words = [
            word for word in _words(text)
            if word not in SET_NOISE and len(word) >= 3 and not word.isdigit()
        ]
        if words:
            return words[:3]
    return []


def _number(card: dict[str, Any]) -> str | None:
    number = (card.get("number") or "").strip().lstrip("#").strip()
    return number.lower() or None


def query_for(card: dict[str, Any]) -> str | None:
    """Title search for a card, e.g. "thomas major league debut 153"."""
    surname = _surname(card.get("name"))
    if not surname:
        return None
    parts = [surname, *_set_words(card)]
    if _number(card):
        parts.append(_number(card))
    query = " ".join(parts)
    return query if len(query) >= 4 else None


def is_signed(record: dict[str, Any]) -> bool:
    """A signed copy, by its title or The Card API's autograph fields."""
    if SIGNED_TITLE.search(record.get("title") or ""):
        return True
    if record.get("has_autograph_grade") or record.get("autograph_grade"):
        return True
    return any(
        "auto" in str(feature).lower() or "sign" in str(feature).lower()
        for feature in record.get("features") or []
    )


def _matches(record: dict[str, Any], card: dict[str, Any]) -> bool:
    """The sale is this card: the player and number are in it, the year is
    within one (sets dated by season, e.g. "1990-91"), and it isn't a lot,
    a reprint or a signed copy."""
    title = record.get("title") or ""
    if not title or EXCLUDED_TITLE.search(title) or is_signed(record):
        return False

    surname = _surname(card.get("name"))
    if surname and surname not in _words(title):
        return False

    number = _number(card)
    if number:
        recorded = str(record.get("card_number") or "").strip().lstrip("#").lower()
        if recorded:
            if recorded.lstrip("0") != number.lstrip("0"):
                return False
        elif not re.search(rf"#\s*0*{re.escape(number.lstrip('0') or '0')}\b", title, re.IGNORECASE):
            return False

    year = str(card.get("year") or "")[:4]
    recorded_year = str(record.get("year") or "")[:4]
    if year.isdigit() and recorded_year.isdigit() and abs(int(year) - int(recorded_year)) > 1:
        return False

    return True


def _grade_key(record: dict[str, Any]) -> str:
    grader = (record.get("grader") or record.get("grading_company") or "").strip().upper()
    grade = str(record.get("grade") or "").strip()
    if not grader or not grade:
        found = GRADED_TITLE.search(record.get("title") or "")
        if not found:
            return "RAW"
        grader, grade = found.group(1).upper(), found.group(2)
    if grade.endswith(".0"):
        grade = grade[:-2]
    return f"{grader}_{grade}"


def summarize(records: list[dict[str, Any]], card: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """The card's value per grade from its matching confirmed sales. Only
    these figures are kept - never the sales."""
    by_grade: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        price = record.get("price")
        if not record.get("price_confirmed") or not isinstance(price, (int, float)) or price <= 0:
            continue
        if not _matches(record, card):
            continue
        by_grade.setdefault(_grade_key(record), []).append(record)

    grades = {}
    for key, sales in by_grade.items():
        sales.sort(key=lambda record: record.get("sold_at") or record.get("sale_date") or "", reverse=True)
        recent = sales[:RECENT_SALES]
        grades[key] = {
            "value": round(statistics.median(record["price"] for record in recent), 2),
            "sales": len(sales),
            "used": len(recent),
            "last_sale": sales[0].get("sold_at") or sales[0].get("sale_date"),
        }

    return {
        "source": "cardapi",
        "period": PERIOD,
        "query": query_for(card),
        "grades": grades,
        "priced_at": (now or datetime.now(timezone.utc)).isoformat(),
    }


_remaining: int | None = None


async def search_sales(client: httpx.AsyncClient, query: str) -> list[dict[str, Any]]:
    """Recent sales whose titles match `query`, newest first."""
    global _remaining
    if _remaining is not None and _remaining < SALES_PER_CARD + DAILY_RESERVE:
        raise DailyLimitReached("Today's Card API records are used up; values update tomorrow.")

    for attempt in range(2):
        try:
            response = await client.get(
                "/sales",
                params={"q": query, "category": "sports", "sort": "date_desc", "limit": SALES_PER_CARD},
            )
        except httpx.HTTPError as exc:
            raise CardApiError(f"Couldn't reach The Card API: {exc}") from exc

        if response.status_code == 429 and attempt == 0:
            retry_after = response.headers.get("Retry-After")
            if retry_after and retry_after.isdigit() and int(retry_after) <= 10:
                await asyncio.sleep(int(retry_after))
                continue
            raise DailyLimitReached("Today's Card API records are used up; values update tomorrow.")
        break

    remaining = response.headers.get("x-ratelimit-remaining")
    if remaining and remaining.isdigit():
        _remaining = int(remaining)

    if response.status_code in (401, 403):
        raise CardApiError("The Card API rejected the key (check CARDAPI_KEY).")
    if response.status_code == 429:
        raise DailyLimitReached("Today's Card API records are used up; values update tomorrow.")
    if response.status_code >= 400:
        raise CardApiError(f"The Card API returned HTTP {response.status_code}.")

    return response.json().get("data") or []


async def price_cards(cards: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], str | None]:
    """Values for each card (by sports_cards id) from its recent sales, one
    search each, until the day's allowance runs low. Returns the values and
    why it stopped early, if it did."""
    if not configured():
        return {}, "CARDAPI_KEY isn't set."

    results: dict[str, dict[str, Any]] = {}
    async with httpx.AsyncClient(
        base_url=BASE_URL,
        timeout=30,
        headers={"x-market-api-key": settings.cardapi_key},
    ) as client:
        for card in cards:
            query = query_for(card)
            if not query:
                continue
            try:
                records = await search_sales(client, query)
            except DailyLimitReached as exc:
                return results, str(exc)
            except CardApiError as exc:
                return results, str(exc)
            results[card["id"]] = summarize(records, card)

    return results, None
