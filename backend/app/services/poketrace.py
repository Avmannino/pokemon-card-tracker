from statistics import median
from typing import Any

import httpx

from ..config import settings


BASE_URL = "https://api.poketrace.com/v1"


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


async def _request(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            f"{BASE_URL}{path}",
            headers=_headers(),
            params=params,
        )

    if response.status_code == 429:
        raise PokeTraceRateLimited(
            "PokeTrace rate limit reached. Free accounts allow 250 requests/day."
        )

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


def extract_raw_sources(card: dict[str, Any]) -> list[dict[str, Any]]:
    prices = card.get("prices") or {}
    marketplace_urls = card.get("marketplaceUrls") or {}
    observed_at = card.get("lastUpdated")

    source_map = {
        "ebay": "eBay raw sold average (via PokeTrace)",
        "tcgplayer": "TCGPlayer raw market (via PokeTrace)",
    }

    raw_tier_priority = [
        "NEAR_MINT",
        "MINT",
        "LIGHTLY_PLAYED",
        "EXCELLENT",
        "AGGREGATED",
    ]

    results: list[dict[str, Any]] = []

    for source_key, source_label in source_map.items():
        tiers = prices.get(source_key) or {}
        chosen_tier = None
        chosen_data = None

        for tier in raw_tier_priority:
            candidate = tiers.get(tier)

            if candidate and candidate.get("avg") is not None:
                chosen_tier = tier
                chosen_data = candidate
                break

        if not chosen_data:
            continue

        try:
            avg = float(chosen_data["avg"])
        except (TypeError, ValueError):
            continue

        if avg < 0:
            continue

        results.append(
            {
                "source": source_label,
                "value": avg,
                "source_url": marketplace_urls.get(source_key),
                "observed_at": observed_at,
                "metadata": {
                    "provider": "PokeTrace",
                    "underlying_source": source_key,
                    "tier": chosen_tier,
                    "low": chosen_data.get("low"),
                    "high": chosen_data.get("high"),
                    "sale_count": chosen_data.get("saleCount"),
                    "avg_7d": chosen_data.get("avg7d"),
                    "avg_30d": chosen_data.get("avg30d"),
                    "confidence": chosen_data.get("confidence"),
                },
            }
        )

    return results


def calculate_raw_market_estimate(card: dict[str, Any]) -> float | None:
    raw_sources = extract_raw_sources(card)

    values = [
        float(source["value"])
        for source in raw_sources
        if source.get("value") is not None
    ]

    if not values:
        return None

    return round(float(median(values)), 2)


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


async def get_card(poketrace_id: str) -> dict[str, Any]:
    payload = await _request(f"/cards/{poketrace_id}")
    data = payload.get("data")

    if not data:
        raise PokeTraceError(
            "PokeTrace returned no card data."
        )

    return data