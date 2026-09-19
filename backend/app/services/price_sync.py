import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .. import db
from ..config import settings
from . import poketrace


logger = logging.getLogger("price_sync")

# Which pull slot last ran, so restarts (including uvicorn --reload) can
# never trigger a second pull for a slot that already happened.
STATE_PATH = Path(__file__).resolve().parents[2] / ".price_sync_state.json"

# Two pulls a day against PokeTrace Free's 250 requests/day leaves room for
# searches, so keep each pull well under half of that.
MAX_CARDS_PER_PULL = 100

# PokeTrace Free burst limit is 1 request / 2 seconds.
REQUEST_SPACING_SECONDS = 2.1


def _now() -> datetime:
    return datetime.now().astimezone()


def _slots_around(now: datetime) -> list[datetime]:
    slots = set()

    for offset in (-1, 0, 1):
        day = now.date() + timedelta(days=offset)
        for clock in (settings.refresh_am_time, settings.refresh_pm_time):
            slots.add(datetime.combine(day, clock).astimezone())

    return sorted(slots)


def current_slot(now: datetime) -> datetime:
    """The most recent scheduled pull time at or before `now`."""
    return max(slot for slot in _slots_around(now) if slot <= now)


def next_slot(now: datetime) -> datetime:
    return min(slot for slot in _slots_around(now) if slot > now)


def _read_state() -> dict[str, Any] | None:
    try:
        return json.loads(STATE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _write_state(state: dict[str, Any]) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2))


async def pull_card_raw_prices(card_id: str) -> int:
    card = db.get_card(card_id)
    if not card:
        raise LookupError(f"Card {card_id} not found.")

    remote_card = await poketrace.get_card(card["poketrace_id"])
    raw_sources = poketrace.extract_raw_sources(remote_card)

    for raw_source in raw_sources:
        db.insert_price_snapshot(
            card_id=card_id,
            grade="RAW",
            source=raw_source["source"],
            value=raw_source["value"],
            source_url=raw_source.get("source_url"),
            observed_at=raw_source.get("observed_at"),
            metadata=raw_source.get("metadata"),
        )

    # Update mutable catalog information too.
    normalized = poketrace.normalize_card(remote_card)
    normalized["poketrace_id"] = card["poketrace_id"]
    db.save_card(normalized)

    return len(raw_sources)


async def sync_all_cards(slot: datetime) -> None:
    state: dict[str, Any] = {
        "slot": slot.isoformat(),
        "started_at": _now().isoformat(),
        "finished_at": None,
        "attempted": 0,
        "succeeded": 0,
        "failed": 0,
        "skipped": 0,
        "stopped_early": None,
    }

    # Recorded up front: a crash or restart mid-pull must not cause a second
    # pull for the same slot.
    _write_state(state)

    unique_card_ids = list(
        dict.fromkeys(item["card_id"] for item in db.get_collection_items())
    )
    card_ids = unique_card_ids[:MAX_CARDS_PER_PULL]
    state["skipped"] = len(unique_card_ids) - len(card_ids)

    for index, card_id in enumerate(card_ids):
        state["attempted"] += 1

        try:
            await pull_card_raw_prices(card_id)
            state["succeeded"] += 1
        except poketrace.PokeTraceRateLimited as exc:
            state["failed"] += 1
            state["stopped_early"] = str(exc)
            break
        except Exception:
            state["failed"] += 1
            logger.exception("Price pull failed for card %s", card_id)

        if index < len(card_ids) - 1:
            await asyncio.sleep(REQUEST_SPACING_SECONDS)

    state["finished_at"] = _now().isoformat()
    _write_state(state)

    logger.info(
        "Price pull finished: %s succeeded, %s failed, %s skipped",
        state["succeeded"],
        state["failed"],
        state["skipped"],
    )


async def run_scheduler() -> None:
    """Pulls prices once per slot (AM and PM). Also catches up on startup if
    the current slot's pull never happened, e.g. the backend was off."""
    while True:
        try:
            slot = current_slot(_now())
            state = _read_state()

            if not state or state.get("slot") != slot.isoformat():
                await sync_all_cards(slot)
        except Exception:
            logger.exception("Scheduled price pull failed")

        wait = (next_slot(_now()) - _now()).total_seconds()
        await asyncio.sleep(max(wait, 0) + 1)


def get_status() -> dict[str, Any]:
    state = _read_state()

    return {
        "last_pull_at": state["started_at"] if state else None,
        "last_result": (
            {
                key: state[key]
                for key in (
                    "attempted",
                    "succeeded",
                    "failed",
                    "skipped",
                    "stopped_early",
                )
            }
            if state
            else None
        ),
        "next_pull_at": next_slot(_now()).isoformat(),
    }
