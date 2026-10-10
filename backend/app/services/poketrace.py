"""PokeTrace adapter (https://api.poketrace.com/v1, OpenAPI 1.7.0).

Response shape this relies on, per the published OpenAPI spec and checked
against live responses:

- Every card id is exactly one variant (`variant`: Normal, Holofoil,
  Reverse_Holofoil, 1st_Edition, ...). A Holo and a Reverse Holo of the same
  card number are two ids, but share one TCGPlayer product id
  (`refs.tcgplayerId`).
- `prices` is {source: {tier: TierPrice}}, e.g. prices.tcgplayer.NEAR_MINT.
  TierPrice has avg/low/high, avg1d/avg7d/avg30d, median3d/median7d/
  median30d, saleCount, approxSaleCount, lastUpdated (some may be null).
- GET /cards accepts tcgplayer_ids (max 20, comma-separated) and pages with
  pagination.hasMore / pagination.nextCursor (passed back as `cursor`).
- Responses carry x-ratelimit-limit / x-ratelimit-remaining /
  x-ratelimit-reset (ISO time) headers.
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from ..config import settings
from . import state_store
from .valuation import evaluate


BASE_URL = "https://api.poketrace.com/v1"

# Free plan burst limit is 1 request / 2 seconds.
REQUEST_SPACING_SECONDS = 2.1

# Never spend the last few requests of the day (searches and adding a card
# need them).
MIN_REMAINING = 2

# Short-term 429s (requests still left today) are retried after 4s, 8s, 16s.
RATE_LIMIT_RETRIES = 3

RAW_TIER = "NEAR_MINT"

# Stored source labels stay as they were so price history stays one series
# per source. What each value actually is lives in metadata.value_basis.
SOURCE_LABELS = {
    "ebay": "eBay raw sold average (via PokeTrace)",
    "tcgplayer": "TCGPlayer raw market (via PokeTrace)",
}

# Which TierPrice field each source's value comes from, best first. eBay is
# sold listings, so its recent median resists one odd sale; TCGPlayer's avg
# is its current market price.
VALUE_FIELDS = {
    "ebay": ("median30d", "median7d", "avg30d", "avg7d", "avg"),
    "tcgplayer": ("avg",),
}

# A tcgplayer_ids lookup takes at most this many ids.
TCGPLAYER_IDS_PER_REQUEST = 20

_last_request_at = 0.0
_quota: dict[str, Any] | None = None


class PokeTraceError(RuntimeError):
    pass


class PokeTraceRateLimited(PokeTraceError):
    pass


def _headers() -> dict[str, str]:
    return {
        "X-API-Key": settings.poketrace_api_key,
        "Accept": "application/json",
        "User-Agent": "PokemonCardTracker/0.1",
    }


def _remember_quota(response: httpx.Response) -> None:
    global _quota

    try:
        remaining = int(response.headers["x-ratelimit-remaining"])
    except (KeyError, ValueError):
        return

    try:
        limit = int(response.headers.get("x-ratelimit-limit", ""))
    except ValueError:
        limit = None

    _quota = {
        "remaining": remaining,
        "limit": limit,
        "resets_at": response.headers.get("x-ratelimit-reset"),
    }
    try:
        state_store.set("poketrace_quota", _quota)
    except Exception:
        pass


def quota() -> dict[str, Any] | None:
    """Latest known daily quota ({remaining, limit, resets_at}), or None if
    unknown or the day has since reset."""
    global _quota

    current = _quota or state_store.get("poketrace_quota")
    if not current:
        return None

    resets_at = current.get("resets_at")
    if resets_at:
        try:
            moment = datetime.fromisoformat(str(resets_at).replace("Z", "+00:00"))
            if moment <= datetime.now(timezone.utc):
                _quota = None
                return None
        except ValueError:
            pass

    _quota = current
    return current


def requests_remaining() -> int | None:
    current = quota()
    return current["remaining"] if current else None


async def _request(
    path: str,
    params: dict[str, Any] | None = None,
    reserve: int = MIN_REMAINING,
) -> dict[str, Any]:
    """One GET. Refuses (PokeTraceRateLimited) instead of sending when the
    known remaining daily quota is at or below `reserve`."""
    global _last_request_at

    remaining = requests_remaining()
    if remaining is not None and remaining <= max(reserve, MIN_REMAINING):
        raise PokeTraceRateLimited(
            f"PokeTrace daily quota nearly used up ({remaining} left); "
            "stopped to keep requests for searches and new cards."
        )

    for attempt in range(RATE_LIMIT_RETRIES + 1):
        wait = REQUEST_SPACING_SECONDS - (time.monotonic() - _last_request_at)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_at = time.monotonic()

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                f"{BASE_URL}{path}",
                headers=_headers(),
                params=params,
            )

        _remember_quota(response)

        if response.status_code != 429:
            break

        # A 429 with requests still left today is PokeTrace's short-term
        # limiter, which clears within seconds: back off and retry. Only a
        # 429 with none left is the daily limit.
        left = response.headers.get("x-ratelimit-remaining")
        if left == "0":
            raise PokeTraceRateLimited(
                "PokeTrace daily limit reached (250 requests/day); it resets at "
                f"{response.headers.get('x-ratelimit-reset', 'midnight UTC')}."
            )

        if attempt == RATE_LIMIT_RETRIES:
            raise PokeTraceRateLimited(
                "PokeTrace is temporarily rate-limiting requests"
                + (f" ({left} of today's requests still left)" if left else "")
                + ". Try again in a minute."
            )

        retry_after = response.headers.get("retry-after", "")
        delay = float(retry_after) if retry_after.isdigit() else 4 * 2**attempt
        await asyncio.sleep(min(delay, 30))

    if response.status_code == 403:
        detail = response.text
        raise PokeTraceError(
            "PokeTrace rejected this request. The requested data may require "
            f"a paid plan. Details: {detail[:300]}"
        )

    if response.status_code >= 400:
        raise PokeTraceError(
            f"PokeTrace returned HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )

    return response.json()


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def extract_raw_sources(card: dict[str, Any]) -> list[dict[str, Any]]:
    """Near Mint price snapshots for this exact card/variant, one per source.
    A source with no Near Mint price is skipped; other conditions are never
    used in its place."""
    prices = card.get("prices") or {}
    marketplace_urls = card.get("marketplaceUrls") or {}
    observed_at = card.get("lastUpdated")

    results: list[dict[str, Any]] = []

    for source_key, source_label in SOURCE_LABELS.items():
        tier = (prices.get(source_key) or {}).get(RAW_TIER)
        if not tier:
            continue

        value = None
        basis = None
        for field in VALUE_FIELDS[source_key]:
            value = _number(tier.get(field))
            if value is not None:
                basis = field
                break

        if value is None:
            continue

        results.append(
            {
                "source": source_label,
                "value": value,
                "source_url": marketplace_urls.get(source_key),
                "observed_at": observed_at,
                "metadata": {
                    "provider": "PokeTrace",
                    "underlying_source": source_key,
                    "tier": RAW_TIER,
                    "value_basis": basis,
                    "variant": card.get("variant"),
                    "poketrace_id": str(card.get("id") or "") or None,
                    "avg": tier.get("avg"),
                    "low": tier.get("low"),
                    "high": tier.get("high"),
                    "avg_1d": tier.get("avg1d"),
                    "avg_7d": tier.get("avg7d"),
                    "avg_30d": tier.get("avg30d"),
                    "median_3d": tier.get("median3d"),
                    "median_7d": tier.get("median7d"),
                    "median_30d": tier.get("median30d"),
                    "sale_count": tier.get("saleCount"),
                    "approx_sale_count": tier.get("approxSaleCount"),
                    "tier_last_updated": tier.get("lastUpdated"),
                    "confidence": tier.get("confidence"),
                    "trend": tier.get("trend"),
                },
            }
        )

    return results


def calculate_raw_market_estimate(card: dict[str, Any]) -> float | None:
    """Same rule the collection uses (valuation.evaluate), so a search result
    shows the value the card would get once added."""
    return evaluate("RAW", extract_raw_sources(card), card.get("variant"))["estimate"]


def rarity_score(rarity: str | None) -> int:
    """
    Used only as a fallback/tiebreaker for search sorting.

    Market value is the primary sort because price is a better measure of
    practical scarcity/value than the printed rarity label alone.
    """
    text = (rarity or "").strip().lower()

    if not text:
        return 0

    ranking = [
        ("special illustration rare", 120),
        ("shiny ultra rare", 118),
        ("hyper rare", 115),
        ("secret rare", 112),
        ("rainbow rare", 110),
        ("gold rare", 108),
        ("illustration rare", 105),
        ("ultra rare", 100),
        ("amazing rare", 95),
        ("shiny rare", 92),
        ("double rare", 90),
        ("holo rare", 85),
        ("rare holo", 85),
        ("promo", 80),
        ("rare", 70),
        ("uncommon", 40),
        ("common", 20),
    ]

    for label, score in ranking:
        if label in text:
            return score

    return 10


def normalize_card(card: dict[str, Any]) -> dict[str, Any]:
    set_data = card.get("set") or {}
    refs = card.get("refs") or {}
    marketplace_urls = card.get("marketplaceUrls") or {}

    return {
        "poketrace_id": str(card.get("id", "")),
        "name": card.get("name") or "Unknown card",
        "card_number": card.get("cardNumber"),
        "set_name": set_data.get("name"),
        "set_slug": set_data.get("slug"),
        "variant": card.get("variant"),
        "rarity": card.get("rarity"),
        "image_url": card.get("image"),
        "tcgplayer_id": refs.get("tcgplayerId"),
        "marketplace_urls": {
            "tcgplayer": marketplace_urls.get("tcgplayer"),
            "ebay": marketplace_urls.get("ebay"),
            "cardmarket": marketplace_urls.get("cardmarket"),
        },
        "raw_market_estimate": calculate_raw_market_estimate(card),
        "rarity_score": rarity_score(card.get("rarity")),
    }


def printing_key(card: dict[str, Any]) -> tuple[str, str, str] | None:
    """Identifies one printing (set + number) across its variants, from a
    raw PokeTrace card: the same TCGPlayer product, card number and set."""
    tcgplayer_id = (card.get("refs") or {}).get("tcgplayerId")
    if not tcgplayer_id:
        return None

    return (
        str(tcgplayer_id),
        str(card.get("cardNumber") or ""),
        str((card.get("set") or {}).get("slug") or ""),
    )


def saved_printing_key(card_row: dict[str, Any]) -> tuple[str, str, str] | None:
    """printing_key() for one of our saved card rows."""
    if not card_row.get("tcgplayer_id"):
        return None

    return (
        str(card_row["tcgplayer_id"]),
        str(card_row.get("card_number") or ""),
        str(card_row.get("set_slug") or ""),
    )


def variant_siblings(
    printing: tuple[str, str, str] | None,
    remote_cards: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Every variant PokeTrace has for one printing, for cards.variant_siblings."""
    if printing is None:
        return None

    variants = [
        {
            "poketrace_id": str(card.get("id")),
            "variant": card.get("variant"),
            "image_url": card.get("image"),
            "rarity": card.get("rarity"),
            "raw_market_estimate": calculate_raw_market_estimate(card),
        }
        for card in remote_cards
        if printing_key(card) == printing
    ]
    if not variants:
        return None

    variants.sort(key=lambda option: (option["variant"] or "", option["poketrace_id"]))
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "variants": variants,
    }


def match_exact(
    card_row: dict[str, Any],
    remote_cards: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str | None]:
    """The remote card for exactly this saved card (same PokeTrace id and
    variant), or (None, reason). Never substitutes another variant."""
    remote = next(
        (card for card in remote_cards if str(card.get("id")) == str(card_row["poketrace_id"])),
        None,
    )
    if remote is None:
        return None, "not_returned"

    saved = card_row.get("variant")
    current = remote.get("variant")
    if saved and current and saved != current:
        return None, (
            f"PokeTrace now lists this card as {current}, but it was saved as "
            f"{saved}; prices weren't stored until the variant is confirmed."
        )

    return remote, None


async def search_cards(query: str, limit: int = 20) -> list[dict[str, Any]]:
    payload = await _request(
        "/cards",
        params={
            "search": query,
            "market": "US",
            "product_type": "single",
            "limit": min(max(limit, 1), 20),
        },
    )

    cards = [
        normalize_card(card)
        for card in payload.get("data", [])
    ]

    # PokeTrace returns "/cards?search=" results in relevance order for the
    # query text. Pin its top two matches to the front so an exact/close
    # name match never gets buried under a pricier but less relevant card,
    # then value-sort everything else as before.
    most_relevant, remaining = cards[:2], cards[2:]

    remaining.sort(
        key=lambda card: (
            card.get("raw_market_estimate") is not None,
            card.get("raw_market_estimate") or 0,
            card.get("rarity_score") or 0,
        ),
        reverse=True,
    )

    return most_relevant + remaining


async def get_card(poketrace_id: str, reserve: int = MIN_REMAINING) -> dict[str, Any]:
    payload = await _request(f"/cards/{poketrace_id}", reserve=reserve)
    data = payload.get("data")

    if not data:
        raise PokeTraceError(
            "PokeTrace returned no card data."
        )

    return data


async def get_cards_by_tcgplayer_ids(
    tcgplayer_ids: list[str],
    reserve: int = MIN_REMAINING,
) -> list[dict[str, Any]]:
    """Every US PokeTrace card (all variants) for up to 20 TCGPlayer
    products, following pagination."""
    ids = list(dict.fromkeys(str(value) for value in tcgplayer_ids if value))
    if not ids:
        return []
    if len(ids) > TCGPLAYER_IDS_PER_REQUEST:
        raise ValueError(f"At most {TCGPLAYER_IDS_PER_REQUEST} TCGPlayer ids per lookup.")

    cards: list[dict[str, Any]] = []
    cursor = None
    seen_cursors = set()

    while True:
        params = {
            "tcgplayer_ids": ",".join(ids),
            "market": "US",
            "limit": 20,
        }
        if cursor:
            params["cursor"] = cursor

        payload = await _request("/cards", params=params, reserve=reserve)
        cards.extend(payload.get("data") or [])

        page = payload.get("pagination") or {}
        cursor = page.get("nextCursor")
        if not page.get("hasMore") or not cursor or cursor in seen_cursors:
            return cards
        seen_cursors.add(cursor)
