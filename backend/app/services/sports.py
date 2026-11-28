"""The Sports section: your sports cards valued from recent sales, each raw
card's grading outlook, and the daily price refresh.

Two sources: CardSight (its catalog's cards, eBay auctions from the past
year, one call per 100 cards) and, for cards CardSight can't value - added
by hand, or fewer than 3 CardSight sales - The Card API (sales found by
title from the past 30 days). Each grade is valued from whichever has more
sales behind it. Neither source's terms allow keeping price history, so
the cards have current values only; the chart gets the collection's total
(see record_value).
"""

import logging
import math
from datetime import datetime, timezone
from typing import Any

from .. import db
from . import cardapi, cardsight, grading, state_store


logger = logging.getLogger("sports")

OWNED_GRADES = ("RAW", "PSA_7", "PSA_8", "PSA_9", "PSA_10")
REFRESH_KEY = "sports_refresh"
# The Refresh button can't run again sooner than this (each run is a call
# per 100 cards). The daily scheduled refresh is never this close to one.
REFRESH_COOLDOWN_SECONDS = 600


class RefreshBusy(Exception):
    pass


class RefreshCooldown(Exception):
    def __init__(self, retry_after: int):
        super().__init__(f"Sports prices were refreshed recently; try again in {retry_after}s.")
        self.retry_after = retry_after


# Fewer CardSight sales than this for a grade you own and the card is
# also looked up on The Card API.
THIN_SALES = 3


def _cardsight_grades(card: dict[str, Any]) -> dict[str, Any]:
    return ((card.get("prices") or {}).get("grades")) or {}


def _sales_grades(card: dict[str, Any]) -> dict[str, Any]:
    return ((card.get("sales_prices") or {}).get("grades")) or {}


def _grades(card: dict[str, Any]) -> dict[str, Any]:
    """Each grade's value from whichever source has more sales behind it -
    CardSight (past year) or The Card API (past 30 days) - marked with
    "source". CardSight wins ties."""
    cardsight_grades = _cardsight_grades(card)
    sales_grades = _sales_grades(card)
    merged = {}
    for key in set(cardsight_grades) | set(sales_grades):
        first = cardsight_grades.get(key)
        second = sales_grades.get(key)
        if first and (not second or (first.get("sales") or 0) >= (second.get("sales") or 0)):
            merged[key] = {**first, "source": "cardsight"}
        elif second:
            merged[key] = {**second, "source": "cardapi"}
    return merged


def owned_value(item: dict[str, Any], card: dict[str, Any]) -> tuple[float | None, str | None]:
    """Value of one copy and where it comes from: your own value when you
    set one, otherwise the median of recent sales of the grade you own
    ("cardsight" or "cardapi")."""
    if item.get("manual_value") is not None:
        return float(item["manual_value"]), "manual"

    grade = _grades(card).get(item["ownership_grade"])
    if grade and grade.get("value") is not None:
        return float(grade["value"]), grade["source"]
    return None, None


SOURCE_PERIODS = {
    "cardsight": "eBay auction sales in the past year",
    "cardapi": "sales in the past 30 days",
}


def _grade_part(grade: dict[str, Any] | None) -> dict[str, Any]:
    if not grade:
        return {"value": None, "method": None, "summary": None, "as_of": None, "sales": None}
    return {
        "value": grade.get("value"),
        "method": grade.get("source", "cardsight"),
        "summary": (
            f"Median of the last {grade.get('used')} of {grade.get('sales')} "
            f"{SOURCE_PERIODS.get(grade.get('source'), SOURCE_PERIODS['cardsight'])}."
        ),
        "as_of": grade.get("last_sale"),
        "sales": grade.get("sales"),
    }


def needs_sales_lookup(card: dict[str, Any], grades_owned: set[str]) -> bool:
    """Look the card up on The Card API: it was added by hand, or CardSight
    has fewer than THIN_SALES sales for a grade you own."""
    if is_manual_card(card):
        return True
    cardsight_grades = _cardsight_grades(card)
    return any(
        (cardsight_grades.get(grade) or {}).get("sales", 0) < THIN_SALES
        for grade in grades_owned
    )


async def update_sales_prices(cards: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], str | None]:
    """Looks the cards up on The Card API and saves what it found. Returns
    the values by card id and why it stopped early, if it did. Never
    raises: these values only fill gaps."""
    if not cards or not cardapi.configured():
        return {}, None
    try:
        results, stopped = await cardapi.price_cards(cards)
        db.save_sports_sales_prices(results)
        return results, stopped
    except Exception as exc:
        logger.warning("Card API lookup failed", exc_info=True)
        return {}, str(exc)


# Cards CardSight doesn't have (e.g. 2003 Upper Deck Yankees 100th
# Anniversary, listed with no cards) are added by hand: their id starts with
# this instead of being a CardSight id, they're never sent to CardSight, and
# their value is the one you set.
MANUAL_PREFIX = "manual-"


def is_manual_card(card: dict[str, Any]) -> bool:
    return str(card.get("cardsight_id") or "").startswith(MANUAL_PREFIX)


def card_view(card: dict[str, Any]) -> dict[str, Any]:
    prices = card.get("prices") or {}
    attributes = card.get("attributes") or []
    return {
        "id": card.get("id"),
        "manual": is_manual_card(card),
        "cardsight_id": card["cardsight_id"],
        "name": card.get("name"),
        "number": card.get("number"),
        "year": card.get("year"),
        "release_name": card.get("release_name"),
        "set_name": card.get("set_name"),
        "attributes": attributes,
        "league": cardsight.league(attributes),
        "photo_url": prices.get("photo_url"),
    }


def grading_outlook(
    card: dict[str, Any],
    grading_cost: float,
    raw_override: float | None = None,
    item: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A raw card's grading outlook from its cached prices, scored exactly
    like the Pokemon cards (grading.evaluate). `raw_override` stands in for
    a raw value CardSight doesn't have (e.g. one you entered)."""
    grades = _grades(card)
    raw = _grade_part(grades.get("RAW"))
    if raw["value"] is None and raw_override is not None:
        raw = {**raw, "value": raw_override, "summary": "Your own value."}

    view = card_view(card)
    result = grading.evaluate(
        {
            "collection_item_id": item["id"] if item else None,
            "card": {
                "id": view["cardsight_id"],
                "name": view["name"],
                "card_number": view["number"],
                "set_name": " ".join(
                    part for part in (view["year"], view["release_name"]) if part
                ) or None,
                "variant": None,
                "rarity": None,
                "image_url": view["photo_url"],
            },
            "language": "English",
            "pricecharting_url": None,
            "quantity": int(item["quantity"]) if item else 0,
            "small_hit": False,
            "raw": raw,
            "psa9": _grade_part(grades.get("PSA_9")),
            "psa10": _grade_part(grades.get("PSA_10")),
        },
        grading_cost,
    )

    if not result["available"]:
        if raw["value"] is None:
            result["unavailable_reason"] = "No recent raw sales."
        elif result["psa10_value"] is None:
            result["unavailable_reason"] = "No recent PSA 10 sales."
    return result


def build_collection(
    items: list[dict[str, Any]],
    cards: dict[str, dict[str, Any]],
    grading_cost: float,
) -> dict[str, Any]:
    rows = []
    for item in items:
        card = cards.get(item["sports_card_id"])
        if not card:
            continue

        quantity = int(item.get("quantity") or 1)
        value_each, source = owned_value(item, card)
        is_raw = item["ownership_grade"] == "RAW"

        rows.append(
            {
                **item,
                "quantity": quantity,
                "card": card_view(card),
                "prices": _grades(card),
                "priced_at": card.get("priced_at") or card.get("sales_priced_at"),
                "last_sale_date": (card.get("prices") or {}).get("last_sale_date"),
                "value_each": value_each,
                "value_source": source,
                "value_total": round(value_each * quantity, 2) if value_each is not None else None,
                "grading": (
                    grading_outlook(
                        card,
                        grading_cost,
                        raw_override=value_each if source == "manual" else None,
                        item=item,
                    )
                    if is_raw
                    else None
                ),
            }
        )

    valued = [row["value_total"] for row in rows if row["value_total"] is not None]
    return {
        "items": rows,
        "summary": {
            "item_count": len(rows),
            "card_count": sum(row["quantity"] for row in rows),
            "total_value": round(sum(valued), 2),
            "missing_values": sum(1 for row in rows if row["value_total"] is None),
            "unpriced": sum(1 for row in rows if not row["priced_at"]),
        },
    }


def last_refresh() -> dict[str, Any] | None:
    return state_store.get(REFRESH_KEY)


# --- The sports total in the portfolio chart ---
#
# CardSight's terms rule out keeping its card prices over time, so the
# chart gets only the sports collection's total: a row in
# sports_value_history after every change. Each row's flow is the part of
# the change you made (cards added, removed, regraded or given your own
# value, or priced for the first time); the chart books it like a deposit or
# withdrawal, so only price moves found by a refresh count as gains or
# losses.


def collection_total(items: list[dict[str, Any]], cards: dict[str, dict[str, Any]]) -> float:
    total = 0.0
    for item in items:
        card = cards.get(item["sports_card_id"])
        if card:
            value, _ = owned_value(item, card)
            if value is not None:
                total += value * int(item.get("quantity") or 1)
    return round(total, 2)


def current_total() -> float:
    items = db.get_sports_items()
    return collection_total(items, db.get_sports_cards({item["sports_card_id"] for item in items}))


def record_value(kind: str, total: float | None = None, market_change: float = 0.0) -> None:
    """Records the sports total after a change. Everything since the last
    row except `market_change` is a flow. Never fails the change it follows
    (e.g. before the history table exists)."""
    try:
        total = current_total() if total is None else total
        last = db.get_last_sports_value()
        previous = float(last["total_value"]) if last else 0.0
        if last and abs(total - previous) < 0.005 and kind != "refresh":
            return
        db.insert_sports_value(total, total - previous - market_change, kind)
    except Exception:
        logger.warning("Couldn't record the sports total (%s)", kind, exc_info=True)


def history_rows(
    history: list[dict[str, Any]], current: float, now: datetime
) -> list[dict[str, Any]]:
    """The recorded totals, plus a row bringing them up to the current total
    when they differ (sports cards added before the history existed, or a
    change that couldn't be recorded) - booked as a flow, never a gain."""
    previous = float(history[-1]["total_value"]) if history else 0.0
    if abs(current - previous) < 0.005:
        return history
    return [
        *history,
        {
            "recorded_at": now.isoformat(),
            "total_value": current,
            "flow": round(current - previous, 2),
            "kind": "reconcile",
        },
    ]


def refresh_changes(
    items: list[dict[str, Any]],
    before_cards: dict[str, dict[str, Any]],
    after_cards: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """What a refresh changed: each card you own whose value (from either
    source) moved, biggest dollar move (value x copies) first. Cards valued
    by your own value, or priced for the first time, aren't changes."""
    changes = []
    for item in items:
        before_card = before_cards.get(item["sports_card_id"])
        after_card = after_cards.get(item["sports_card_id"])
        if not before_card or not after_card or item.get("manual_value") is not None:
            continue

        grade = item["ownership_grade"]
        before, _ = owned_value(item, before_card)
        after, _ = owned_value(item, after_card)
        if before is None or after is None or round(float(after) - float(before), 2) == 0:
            continue

        quantity = int(item.get("quantity") or 1)
        each = round(float(after) - float(before), 2)
        changes.append(
            {
                "item_id": item["id"],
                "card": card_view(after_card),
                "grade": grade,
                "quantity": quantity,
                "previous_each": float(before),
                "value_each": float(after),
                "change": round(each * quantity, 2),
                "change_pct": round(each / float(before) * 100, 2) if float(before) else None,
            }
        )

    changes.sort(key=lambda change: -abs(change["change"]))
    return changes


_refreshing = False


async def refresh_prices(reason: str = "manual", now: datetime | None = None) -> dict[str, Any]:
    """Reprices every sports card you own: one CardSight call per 100
    cards, then The Card API for the cards CardSight can't value (one search
    each). Each card's values are replaced, never added to."""
    global _refreshing
    if _refreshing:
        raise RefreshBusy("Sports prices are already refreshing.")

    now = now or datetime.now(timezone.utc)
    previous = last_refresh() or {}
    if reason == "manual" and previous.get("finished_at"):
        elapsed = (now - datetime.fromisoformat(previous["finished_at"])).total_seconds()
        if elapsed < REFRESH_COOLDOWN_SECONDS:
            raise RefreshCooldown(math.ceil(REFRESH_COOLDOWN_SECONDS - elapsed))

    _refreshing = True
    try:
        items = db.get_sports_items()
        # Copies, so saving the new values can't change what they're
        # compared against.
        before = {
            card_id: dict(card)
            for card_id, card in db.get_sports_cards({item["sports_card_id"] for item in items}).items()
        }
        after = {card_id: dict(card) for card_id, card in before.items()}
        catalog_cards = [card for card in before.values() if not is_manual_card(card)]

        summaries: dict[str, dict[str, Any]] = {}
        cardsight_error = None
        if catalog_cards:
            try:
                summaries = await cardsight.get_prices([card["cardsight_id"] for card in catalog_cards])
            except cardsight.CardSightError as exc:
                # The Card API still fills what it can.
                cardsight_error = str(exc)
        for card in catalog_cards:
            if card["cardsight_id"] in summaries:
                after[card["id"]]["prices"] = summaries[card["cardsight_id"]]
        db.save_sports_card_prices(
            [(card, summaries[card["cardsight_id"]]) for card in catalog_cards if card["cardsight_id"] in summaries]
        )

        # The Card API for cards CardSight can't value, least recently
        # looked up first (the day's allowance may run out).
        grades_owned: dict[str, set[str]] = {}
        for item in items:
            grades_owned.setdefault(item["sports_card_id"], set()).add(item["ownership_grade"])
        lookups = sorted(
            (
                card for card_id, card in after.items()
                if needs_sales_lookup(card, grades_owned.get(card_id, set()))
            ),
            key=lambda card: card.get("sales_priced_at") or "",
        )
        sales, sales_stopped = await update_sales_prices(lookups)
        for card_id, summary in sales.items():
            after[card_id]["sales_prices"] = summary

        # Only this refresh's changes are kept (replaced by the next one),
        # never a history. For the chart's sports total, value moves are
        # market movement; cards valued for the first time (or no longer
        # valued) are flows.
        changes = refresh_changes(items, before, after)
        record_value(
            "refresh",
            total=collection_total(items, after),
            market_change=sum(change["change"] for change in changes),
        )

        record = {
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "cards": len(before),
            "priced": len(summaries),
            "calls": math.ceil(len(catalog_cards) / cardsight.PRICING_BATCH),
            "cardsight_error": cardsight_error,
            "sales_checked": len(sales),
            "sales_note": sales_stopped,
            "net_change": round(sum(change["change"] for change in changes), 2),
            "changes": changes,
        }
        state_store.set(REFRESH_KEY, record)
        return record
    finally:
        _refreshing = False
