import asyncio
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from .. import db
from ..config import settings
from . import state_store


HOST = "pokemon-tcg-api.p.rapidapi.com"
BASE_URL = f"https://{HOST}"

SOURCE_LABEL = "eBay graded sold median (via TCGGO)"

# PSA grades the app tracks.
PSA_GRADES = ("7", "8", "9", "10")

# RapidAPI Basic plan: 30 requests/minute.
REQUEST_SPACING_SECONDS = 2.1

# Legacy local id cache, only read to seed the database copy.
IDS_PATH = Path(__file__).resolve().parents[2] / ".tcggo_ids.json"

# RapidAPI's plan here is a *soft* limit: requests past the daily quota are
# still served and billed as overage. So we never send a request that would
# dip into the last few.
MIN_REMAINING = 3

# How long before re-searching for a card TCGGO couldn't match.
NOT_FOUND_RETRY = timedelta(days=7)

_last_request_at = 0.0

# Daily quota as reported by RapidAPI's response headers. It's shared through
# app_state so a backend that just woke up, or the scheduled GitHub Action,
# knows what's left without spending a request to find out.
_quota: dict[str, Any] | None = None


def _remember_quota(remaining: int, limit: int | None, reset_seconds: int) -> None:
    global _quota

    _quota = {
        "remaining": remaining,
        "limit": limit,
        "resets_at": (
            datetime.now(timezone.utc) + timedelta(seconds=reset_seconds)
        ).isoformat(),
    }
    state_store.set("tcggo_quota", _quota)


def quota() -> dict[str, Any] | None:
    """Latest known quota ({remaining, limit, resets_at}), or None if unknown
    or the daily window has since reset."""
    global _quota

    current = _quota or state_store.get("tcggo_quota")

    if not current:
        return None

    if datetime.fromisoformat(current["resets_at"]) <= datetime.now(timezone.utc):
        _quota = None
        return None

    _quota = current
    return current


def requests_remaining() -> int | None:
    current = quota()
    return current["remaining"] if current else None


def seconds_until_reset() -> int | None:
    current = quota()
    if not current:
        return None

    delta = datetime.fromisoformat(current["resets_at"]) - datetime.now(timezone.utc)
    return max(0, int(delta.total_seconds()))


class TcggoError(RuntimeError):
    pass


class TcggoRateLimited(TcggoError):
    pass


def is_configured() -> bool:
    key = settings.rapidapi_key
    return bool(key) and not key.startswith(("paste_", "rapidapi_key_"))


async def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    global _last_request_at

    remaining = requests_remaining()
    if remaining is not None and remaining <= MIN_REMAINING:
        hours = (seconds_until_reset() or 0) / 3600
        raise TcggoRateLimited(
            f"TCGGO daily quota nearly used up ({remaining} left, resets in "
            f"{hours:.1f}h). Skipped to avoid overage charges."
        )

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

    def _int(header: str) -> int | None:
        try:
            return int(response.headers[header])
        except (KeyError, ValueError):
            return None

    remaining_header = _int("x-ratelimit-requests-remaining")
    reset_header = _int("x-ratelimit-requests-reset")
    if remaining_header is not None and reset_header is not None:
        _remember_quota(
            remaining_header,
            _int("x-ratelimit-requests-limit"),
            reset_header,
        )

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


def _load_legacy_ids() -> dict[str, int]:
    try:
        return json.loads(IDS_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _search_text(card: dict[str, Any]) -> str:
    name = re.sub(r"\(.*?\)", "", card["name"]).split(" - ")[0].strip()
    number = (card.get("card_number") or "").split("/")[0].strip()
    return f"{name} {number}".strip()


async def resolve_card_id(card: dict[str, Any]) -> int | None:
    """TCGGO's id for one of our cards, matched exactly on TCGPlayer id.
    Remembered on the card row so later pulls cost one request, not two."""
    extras = card.get("marketplace_urls") or {}
    if extras.get("tcggo_id"):
        return int(extras["tcggo_id"])

    # A card TCGGO couldn't match isn't searched for again every sync.
    not_found_at = extras.get("tcggo_not_found_at")
    if not_found_at and (
        datetime.now(timezone.utc) - datetime.fromisoformat(not_found_at)
        < NOT_FOUND_RETRY
    ):
        return None

    tcgplayer_id = str(card.get("tcgplayer_id") or "")
    if not tcgplayer_id:
        return None

    legacy = _load_legacy_ids().get(tcgplayer_id)
    if legacy:
        db.update_marketplace_urls(card["id"], {"tcggo_id": legacy})
        return legacy

    payload = await _get("/cards", {"search": _search_text(card)})

    for candidate in payload.get("data", []):
        if str(candidate.get("tcgplayer_id")) == tcgplayer_id:
            db.update_marketplace_urls(card["id"], {"tcggo_id": candidate["id"]})
            return candidate["id"]

    db.update_marketplace_urls(
        card["id"],
        {"tcggo_not_found_at": datetime.now(timezone.utc).isoformat()},
    )
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
