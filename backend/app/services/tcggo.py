import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx

from ..config import settings


HOST = "pokemon-tcg-api.p.rapidapi.com"
BASE_URL = f"https://{HOST}"

SOURCE_LABEL = "eBay graded sold median (via TCGGO)"

# PSA grades the app tracks.
PSA_GRADES = ("7", "8", "9", "10")

# RapidAPI Basic plan: 30 requests/minute.
REQUEST_SPACING_SECONDS = 2.1

# Our card -> TCGGO card id, so later pulls skip the search request.
IDS_PATH = Path(__file__).resolve().parents[2] / ".tcggo_ids.json"

_last_request_at = 0.0

# Requests left today per RapidAPI's response headers, and when that count
# stops being valid (the quota resets).
_remaining: int | None = None
_remaining_expires_at = 0.0


def requests_remaining() -> int | None:
    """TCGGO requests left today as of the last request, or None if unknown
    (nothing requested yet, or the daily quota has since reset)."""
    if time.monotonic() >= _remaining_expires_at:
        return None

    return _remaining


class TcggoError(RuntimeError):
    pass


class TcggoRateLimited(TcggoError):
    pass


def is_configured() -> bool:
    key = settings.rapidapi_key
    return bool(key) and not key.startswith(("paste_", "rapidapi_key_"))


async def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    global _last_request_at, _remaining, _remaining_expires_at

    wait = REQUEST_SPACING_SECONDS - (time.monotonic() - _last_request_at)
    if wait > 0:
        await asyncio.sleep(wait)
    _last_request_at = time.monotonic()

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{BASE_URL}{path}",
            headers={
                "x-rapidapi-host": HOST,
                "x-rapidapi-key": settings.rapidapi_key,
            },
            params=params,
        )

    remaining = response.headers.get("x-ratelimit-requests-remaining")
    reset_seconds = response.headers.get("x-ratelimit-requests-reset")
    if remaining and remaining.isdigit() and reset_seconds and reset_seconds.isdigit():
        _remaining = int(remaining)
        _remaining_expires_at = time.monotonic() + int(reset_seconds)

    if response.status_code == 429:
        raise TcggoRateLimited("TCGGO daily request limit reached.")

    if response.status_code in (401, 403):
        raise TcggoError(
            "TCGGO rejected the RapidAPI key (check RAPIDAPI_KEY and that "
            "you are subscribed to the plan)."
        )

    if response.status_code >= 400:
        raise TcggoError(
            f"TCGGO returned HTTP {response.status_code}: "
            f"{response.text[:200]}"
        )

    return response.json()


def _load_ids() -> dict[str, int]:
    try:
        return json.loads(IDS_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _search_text(card: dict[str, Any]) -> str:
    name = re.sub(r"\(.*?\)", "", card["name"]).split(" - ")[0].strip()
    number = (card.get("card_number") or "").split("/")[0].strip()
    return f"{name} {number}".strip()


async def resolve_card_id(card: dict[str, Any]) -> int | None:
    """TCGGO's id for one of our cards, matched exactly on TCGPlayer id."""
    tcgplayer_id = str(card.get("tcgplayer_id") or "")
    if not tcgplayer_id:
        return None

    ids = _load_ids()
    if tcgplayer_id in ids:
        return ids[tcgplayer_id]

    payload = await _get("/cards", {"search": _search_text(card)})

    for candidate in payload.get("data", []):
        if str(candidate.get("tcgplayer_id")) == tcgplayer_id:
            ids[tcgplayer_id] = candidate["id"]
            IDS_PATH.write_text(json.dumps(ids, indent=2))
            return candidate["id"]

    return None


async def get_psa_medians(card_id: int) -> dict[str, tuple[float, int]]:
    """{"9": (median_price_usd, sample_size), ...} for the PSA grades with
    recent eBay sales. Empty when the card has no graded sales data."""
    payload = await _get("/ebay-sold-prices", {"id": card_id})

    medians: dict[str, tuple[float, int]] = {}
    for entry in payload.get("data") or []:
        grade = str(entry.get("grade"))

        if entry.get("company") != "PSA" or grade not in PSA_GRADES:
            continue
        if entry.get("median_price") is None:
            continue

        medians[grade] = (
            float(entry["median_price"]),
            int(entry.get("sample_size") or 0),
        )

    return medians
