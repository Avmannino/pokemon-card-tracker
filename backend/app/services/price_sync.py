import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .. import db
from ..config import settings
from . import poketrace, state_store, tcggo
from .valuation import is_variant_ambiguous


logger = logging.getLogger("price_sync")

# Which pull slot last ran, so restarts (including uvicorn --reload) can
# never trigger a second pull for a slot that already happened.
STATE_PATH = Path(__file__).resolve().parents[2] / ".price_sync_state.json"

# Two pulls a day against PokeTrace Free's 250 requests/day leaves room for
# searches, so keep each pull well under half of that.
MAX_CARDS_PER_PULL = 100

# PokeTrace Free burst limit is 1 request / 2 seconds (poketrace.py spaces
# its own requests).
REQUEST_SPACING_SECONDS = 2.1

# A sync stops once PokeTrace's remaining daily requests reach this, leaving
# the rest for searches and adding cards.
RAW_SYNC_RESERVE = 20

# TCGGO's free plan is a hard 100 requests/day, and a card can cost two
# requests (id lookup + prices) the first time it is seen.
MAX_GRADED_CARDS_PER_PULL = 40

# Refresh Prices runs a real sync, so repeated clicks are spaced out to keep
# PokeTrace's 250/day and TCGGO's 100/day for the scheduled pulls.
MANUAL_SYNC_COOLDOWN = timedelta(minutes=15)

# One sync at a time, scheduled or manual; `_progress` is what the status
# endpoint reports while it runs.
_sync_lock = asyncio.Lock()
_progress: dict[str, Any] = {
    "running": False,
    "kind": None,
    "phase": None,
    "done": 0,
    "total": 0,
}
_background_tasks: set[asyncio.Task] = set()


class SyncBusy(RuntimeError):
    pass


class SyncCooldown(RuntimeError):
    def __init__(self, retry_after: int) -> None:
        super().__init__(f"Sync is on cooldown for {retry_after}s.")
        self.retry_after = retry_after


def _tz() -> ZoneInfo:
    return ZoneInfo(settings.refresh_timezone)


def _now() -> datetime:
    return datetime.now(_tz())


def _slots_around(now: datetime) -> list[datetime]:
    slots = set()

    for offset in (-1, 0, 1):
        day = now.date() + timedelta(days=offset)
        for clock in (settings.refresh_am_time, settings.refresh_pm_time):
            slots.add(datetime.combine(day, clock, tzinfo=_tz()))

    return sorted(slots)


def current_slot(now: datetime) -> datetime:
    """The most recent scheduled pull time at or before `now`."""
    return max(slot for slot in _slots_around(now) if slot <= now)


def next_slot(now: datetime) -> datetime:
    return min(slot for slot in _slots_around(now) if slot > now)


def _read_state() -> dict[str, Any] | None:
    state = state_store.get("price_sync")
    if state is not None:
        return state

    # Older installs kept this in a local file.
    try:
        return json.loads(STATE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _write_state(state: dict[str, Any]) -> None:
    state_store.set("price_sync", state)


def store_raw_prices(card: dict[str, Any], remote: dict[str, Any]) -> int:
    """Saves Near Mint snapshots from `remote`, which must already be matched
    to `card` exactly (match_exact), and refreshes the catalog details."""
    raw_sources = poketrace.extract_raw_sources(remote)

    for raw_source in raw_sources:
        db.insert_price_snapshot(
            card_id=card["id"],
            grade="RAW",
            source=raw_source["source"],
            value=raw_source["value"],
            source_url=raw_source.get("source_url"),
            observed_at=raw_source.get("observed_at"),
            metadata=raw_source.get("metadata"),
        )

    # Update mutable catalog information too.
    normalized = poketrace.normalize_card(remote)
    normalized["poketrace_id"] = card["poketrace_id"]
    db.save_card(normalized)

    return len(raw_sources)


async def pull_card_raw_prices(card_id: str) -> int:
    """One card's raw prices (used when a card is added or its variant is
    changed): a single exact lookup by its PokeTrace id."""
    card = db.get_card(card_id)
    if not card:
        raise LookupError(f"Card {card_id} not found.")

    remote, problem = poketrace.match_exact(
        card, [await poketrace.get_card(card["poketrace_id"])]
    )
    if remote is None:
        raise poketrace.PokeTraceError(problem)

    return store_raw_prices(card, remote)


def _new_raw_record() -> dict[str, Any]:
    return {
        "attempted": 0,
        "succeeded": 0,
        "failed": 0,
        "skipped": 0,
        "stopped_early": None,
        "requests": 0,
        "issues": [],
    }


def _new_graded_record() -> dict[str, Any]:
    return {
        "attempted": 0,
        "with_data": 0,
        "failed": 0,
        "skipped": 0,
        "skipped_variant_ambiguous": 0,
        "stopped_early": None,
    }


def _card_ids_for_pull(
    limit: int,
    graded_first: bool = False,
) -> tuple[list[str], int]:
    """(unique card ids to pull, how many were left out by `limit`). A card
    held in several collection rows is pulled once. With `graded_first`,
    cards you own graded come first, so they're never the ones cut off."""
    items = db.get_collection_items()

    if graded_first:
        items = sorted(items, key=lambda item: item["ownership_grade"] == "RAW")

    unique_card_ids = list(dict.fromkeys(item["card_id"] for item in items))
    return unique_card_ids[:limit], max(0, len(unique_card_ids) - limit)


@asynccontextmanager
async def _syncing(kind: str):
    async with _sync_lock:
        _progress.update(running=True, kind=kind, phase="raw", done=0, total=0)

        try:
            yield
        finally:
            _progress.update(running=False, phase=None)


def _note(record: dict[str, Any], card: dict[str, Any], problem: str) -> None:
    record["issues"] = (record.get("issues") or [])[:24] + [
        {"card_id": card["id"], "name": card.get("name"), "problem": problem}
    ]


async def _pull_raw(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Pulls raw prices for every card in the collection, counting results
    into `record`. Returns each card's variant siblings (for the graded pull).

    Cards are looked up 20 TCGPlayer products per request, which returns
    every variant of those printings; each card is then matched to its exact
    PokeTrace id and variant. A card missing from the batch is looked up on
    its own; a sibling variant is never used in its place."""
    card_ids, record["skipped"] = _card_ids_for_pull(MAX_CARDS_PER_PULL)
    cards = db.get_cards(card_ids)
    ordered = [cards[card_id] for card_id in card_ids if card_id in cards]
    _progress.update(phase="raw", done=0, total=len(ordered))

    by_printing: dict[tuple[str, str, str] | None, list[dict[str, Any]]] = {}
    for card in ordered:
        by_printing.setdefault(poketrace.saved_printing_key(card), []).append(card)

    batched = [key for key in by_printing if key is not None]
    singles = by_printing.get(None, [])
    siblings_by_card: dict[str, dict[str, Any]] = {}
    done = 0

    async def handle(card: dict[str, Any], remote_cards: list[dict[str, Any]]) -> None:
        nonlocal done
        record["attempted"] += 1

        try:
            remote, problem = poketrace.match_exact(card, remote_cards)

            if problem == "not_returned":
                record["requests"] += 1
                remote, problem = poketrace.match_exact(
                    card,
                    [await poketrace.get_card(card["poketrace_id"], reserve=RAW_SYNC_RESERVE)],
                )

            if remote is None:
                record["failed"] += 1
                _note(record, card, problem)
            else:
                store_raw_prices(card, remote)
                record["succeeded"] += 1

                siblings = poketrace.variant_siblings(
                    poketrace.saved_printing_key(card), remote_cards or [remote]
                )
                if siblings is not None:
                    siblings_by_card[card["id"]] = siblings
                    db.update_card_variant_siblings(card["id"], siblings)
        except poketrace.PokeTraceRateLimited:
            raise
        except Exception as exc:
            record["failed"] += 1
            _note(record, card, f"{type(exc).__name__}: {exc}")
            logger.exception("Price pull failed for card %s", card["id"])

        done += 1
        _progress["done"] = done

    try:
        for start in range(0, len(batched), poketrace.TCGPLAYER_IDS_PER_REQUEST):
            chunk = batched[start:start + poketrace.TCGPLAYER_IDS_PER_REQUEST]

            try:
                remote_cards = await poketrace.get_cards_by_tcgplayer_ids(
                    [key[0] for key in chunk], reserve=RAW_SYNC_RESERVE
                )
                record["requests"] += 1
            except poketrace.PokeTraceRateLimited:
                raise
            except Exception as exc:
                logger.exception("Batched price lookup failed")
                for key in chunk:
                    for card in by_printing[key]:
                        record["attempted"] += 1
                        record["failed"] += 1
                        _note(record, card, f"Lookup failed: {exc}")
                        done += 1
                        _progress["done"] = done
                continue

            for key in chunk:
                for card in by_printing[key]:
                    await handle(card, remote_cards)

        for card in singles:
            await handle(card, [])
    except poketrace.PokeTraceRateLimited as exc:
        record["stopped_early"] = str(exc)

    return siblings_by_card


def _scheduled_runs_before_reset() -> int:
    """Scheduled syncs still to come before TCGGO's daily quota resets."""
    seconds = tcggo.seconds_until_reset()
    if seconds is None:
        return 0

    now = _now()
    end = now + timedelta(seconds=seconds)

    return sum(1 for slot in _slots_around(now) if now < slot <= end)


async def _pull_graded(
    record: dict[str, Any],
    siblings_by_card: dict[str, dict[str, Any]] | None = None,
) -> None:
    """Pulls graded prices for the collection, counting results into
    `record`. Stops once TCGGO's remaining daily requests would no longer
    cover the scheduled syncs still to come before the quota resets, so no
    sync (manual or scheduled) can push the day into paid overage.

    Cards you own graded go first. A card whose TCGPlayer product covers
    several variants is skipped without a request: TCGGO only knows the
    product, so its graded sales couldn't be tied to your variant."""
    card_ids, record["skipped"] = _card_ids_for_pull(
        MAX_GRADED_CARDS_PER_PULL, graded_first=True
    )
    siblings_by_card = siblings_by_card or {}
    _progress.update(phase="graded", done=0, total=len(card_ids))

    upcoming = _scheduled_runs_before_reset()
    reserve = upcoming * len(card_ids) + (5 if upcoming else 0)

    for index, card_id in enumerate(card_ids):
        remaining = tcggo.requests_remaining()
        if remaining is not None and remaining <= reserve:
            hours = (tcggo.seconds_until_reset() or 0) / 3600
            record["stopped_early"] = (
                f"Graded sync paused: {remaining} TCGGO requests left today, "
                f"kept for the {upcoming} scheduled sync(s) before the quota "
                f"resets in {hours:.1f}h."
            )
            break

        card = db.get_card(card_id)
        if card and card_id in siblings_by_card:
            card = {**card, "variant_siblings": siblings_by_card[card_id]}

        if card and is_variant_ambiguous(card):
            record["skipped_variant_ambiguous"] += 1
            _progress["done"] = index + 1
            continue

        record["attempted"] += 1

        try:
            if card and await pull_card_graded_prices(card):
                record["with_data"] += 1
        except tcggo.TcggoRateLimited as exc:
            record["failed"] += 1
            record["stopped_early"] = str(exc)
            break
        except Exception:
            record["failed"] += 1
            logger.exception("Graded price pull failed for card %s", card_id)

        _progress["done"] = index + 1


async def sync_all_cards(slot: datetime) -> None:
    # Keep the other records ("graded", "manual") when rewriting this one.
    state = _read_state() or {}
    state.update(
        {
            "slot": slot.isoformat(),
            "started_at": _now().isoformat(),
            "finished_at": None,
            **_new_raw_record(),
        }
    )

    # Recorded up front: a crash or restart mid-pull must not cause a second
    # pull for the same slot.
    _write_state(state)

    await _pull_raw(state)

    state["finished_at"] = _now().isoformat()
    _write_state(state)

    logger.info(
        "Price pull finished: %s succeeded, %s failed, %s skipped",
        state["succeeded"],
        state["failed"],
        state["skipped"],
    )


async def pull_card_graded_prices(card: dict[str, Any]) -> int:
    """Saves PSA 7-10 eBay sold medians for one card. Returns how many grades
    had data (0 when TCGGO has no graded sales for the card)."""
    api_id = await tcggo.resolve_card_id(card)
    if api_id is None:
        return 0

    medians = await tcggo.get_psa_medians(api_id)

    for grade, (median, sample_size) in medians.items():
        db.insert_price_snapshot(
            card_id=card["id"],
            grade=f"PSA_{grade}",
            source=tcggo.SOURCE_LABEL,
            value=median,
            metadata={
                "provider": "TCGGO",
                "underlying_source": "ebay",
                "grader": "PSA",
                "currency": "USD",
                "sample_size": sample_size,
                # TCGGO matches on the TCGPlayer product, which is only one
                # variant when the product has one (checked before pulling).
                "variant": card.get("variant"),
                "tcggo_id": api_id,
            },
        )

    return len(medians)


async def sync_graded_prices(slot: datetime) -> None:
    graded = {
        "slot": slot.isoformat(),
        "started_at": _now().isoformat(),
        "finished_at": None,
        **_new_graded_record(),
    }

    # Recorded up front, like the raw pull, so it can run at most once per
    # slot no matter how often the backend restarts.
    state = _read_state() or {}
    state["graded"] = graded
    _write_state(state)

    await _pull_graded(graded)

    graded["finished_at"] = _now().isoformat()
    state = _read_state() or {}
    state["graded"] = graded
    _write_state(state)

    logger.info(
        "Graded price pull finished: %s with data, %s failed, %s attempted",
        graded["with_data"],
        graded["failed"],
        graded["attempted"],
    )


async def run_scheduler() -> None:
    """Pulls prices once per slot (AM and PM). Also catches up on startup if
    the current slot's pull never happened, e.g. the backend was off.

    Raw prices (PokeTrace) and graded prices (TCGGO) are tracked separately,
    each at most once per slot. Manual syncs never touch these slot records,
    so they neither use up nor replace a scheduled pull."""
    while True:
        slot = current_slot(_now())
        state = _read_state() or {}

        needs_raw = state.get("slot") != slot.isoformat()
        needs_graded = (
            tcggo.is_configured()
            and (state.get("graded") or {}).get("slot") != slot.isoformat()
        )

        if needs_raw or needs_graded:
            async with _syncing("scheduled"):
                if needs_raw:
                    try:
                        await sync_all_cards(slot)
                    except Exception:
                        logger.exception("Scheduled price pull failed")

                if needs_graded:
                    try:
                        await sync_graded_prices(slot)
                    except Exception:
                        logger.exception("Scheduled graded price pull failed")

        wait = (next_slot(_now()) - _now()).total_seconds()
        await asyncio.sleep(max(wait, 0) + 1)


def _manual_cooldown_remaining() -> int:
    manual = (_read_state() or {}).get("manual")
    if not manual:
        return 0

    elapsed = _now() - datetime.fromisoformat(manual["started_at"])
    return max(0, int((MANUAL_SYNC_COOLDOWN - elapsed).total_seconds()) + 1)


def start_manual_sync() -> None:
    """Starts a full sync in the background (raw + graded). Raises SyncBusy
    if one is already running and SyncCooldown if the last manual sync was
    too recent."""
    if _progress["running"] or _sync_lock.locked():
        raise SyncBusy("A price sync is already running.")

    remaining = _manual_cooldown_remaining()
    if remaining > 0:
        raise SyncCooldown(remaining)

    # Claimed now, before the task gets to run, so a second click can't slip
    # in between.
    _progress.update(running=True, kind="manual", phase="raw", done=0, total=0)

    task = asyncio.create_task(_manual_sync())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _manual_sync() -> None:
    try:
        async with _syncing("manual"):
            record: dict[str, Any] = {
                "started_at": _now().isoformat(),
                "finished_at": None,
                "error": None,
                "raw": _new_raw_record(),
                "graded": _new_graded_record(),
            }

            state = _read_state() or {}
            state["manual"] = record
            _write_state(state)

            # Raw and graded are independent: a graded failure or quota stop
            # never undoes or blocks the raw prices already saved.
            siblings_by_card: dict[str, dict[str, Any]] = {}
            try:
                siblings_by_card = await _pull_raw(record["raw"])
            except Exception as exc:
                logger.exception("Manual raw price sync failed")
                record["error"] = f"{type(exc).__name__}: {exc}"

            try:
                if tcggo.is_configured():
                    await _pull_graded(record["graded"], siblings_by_card)
            except Exception as exc:
                logger.exception("Manual price sync failed")
                record["error"] = f"{type(exc).__name__}: {exc}"

            record["finished_at"] = _now().isoformat()
            state = _read_state() or {}
            state["manual"] = record
            _write_state(state)

            logger.info(
                "Manual sync finished: raw %s/%s, graded %s with data",
                record["raw"]["succeeded"],
                record["raw"]["attempted"],
                record["graded"]["with_data"],
            )
    finally:
        _progress.update(running=False, phase=None)


def get_status() -> dict[str, Any]:
    state = _read_state() or {}
    manual = state.get("manual")

    scheduled_at = state.get("started_at")
    manual_at = manual["started_at"] if manual else None

    manual_is_latest = bool(
        manual_at
        and (
            not scheduled_at
            or datetime.fromisoformat(manual_at)
            > datetime.fromisoformat(scheduled_at)
        )
    )

    if manual_is_latest:
        last_pull_at = manual_at
        last_result = {
            **manual["raw"],
            "failed": manual["raw"]["failed"] + manual["graded"]["failed"],
        }
    elif scheduled_at:
        last_pull_at = scheduled_at
        last_result = {
            key: state.get(key) for key in _new_raw_record()
        }
    else:
        last_pull_at = None
        last_result = None

    return {
        "last_pull_at": last_pull_at,
        "last_result": last_result,
        "graded": state.get("graded"),
        "manual": manual,
        "syncing": _progress["running"],
        "progress": dict(_progress) if _progress["running"] else None,
        "manual_available_in": _manual_cooldown_remaining(),
        "quota": {
            "poketrace": poketrace.quota(),
            "tcggo": tcggo.quota() if tcggo.is_configured() else None,
        },
        "next_pull_at": next_slot(_now()).isoformat(),
    }
