"""The Sports section: your sports cards valued from CardSight's cached
prices, each raw card's grading outlook, and the daily price refresh.

Separate from the Pokemon collection and its performance chart: CardSight's
terms don't allow keeping price history, so sports cards have current
values only.
"""

import logging
import math
from datetime import datetime, timezone
from typing import Any

from .. import db
from . import cardsight, grading, state_store


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


def _grades(card: dict[str, Any]) -> dict[str, Any]:
    return ((card.get("prices") or {}).get("grades")) or {}


def owned_value(item: dict[str, Any], card: dict[str, Any]) -> tuple[float | None, str | None]:
    """Value of one copy and where it comes from: your own value when you
    set one, otherwise the median of recent sales of the grade you own."""
    if item.get("manual_value") is not None:
        return float(item["manual_value"]), "manual"

    grade = _grades(card).get(item["ownership_grade"])
    if grade and grade.get("value") is not None:
        return float(grade["value"]), "cardsight"
    return None, None


def _grade_part(grade: dict[str, Any] | None) -> dict[str, Any]:
    if not grade:
        return {"value": None, "method": None, "summary": None, "as_of": None, "sales": None}
    return {
        "value": grade.get("value"),
        "method": "cardsight",
        "summary": (
            f"Median of the last {grade.get('used')} of {grade.get('sales')} "
            "eBay auction sales in the past year."
        ),
        "as_of": grade.get("last_sale"),
        "sales": grade.get("sales"),
    }


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
            result["unavailable_reason"] = "No raw sales in the past year."
        elif result["psa10_value"] is None:
            result["unavailable_reason"] = "No PSA 10 sales in the past year."
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
        # A hand-added card has no sales to judge grading by.
        is_raw = item["ownership_grade"] == "RAW" and not is_manual_card(card)

        rows.append(
            {
                **item,
                "quantity": quantity,
                "card": card_view(card),
                "prices": _grades(card),
                "priced_at": card.get("priced_at"),
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
    cards: dict[str, dict[str, Any]],
    summaries: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """What a refresh changed: each card you own whose grade's value moved,
    biggest dollar move (value x copies) first. Cards valued by your own
    value, or priced for the first time, aren't changes."""
    changes = []
    for item in items:
        card = cards.get(item["sports_card_id"])
        if not card or item.get("manual_value") is not None:
            continue
        summary = summaries.get(card["cardsight_id"])
        if summary is None:
            continue

        grade = item["ownership_grade"]
        before = (_grades(card).get(grade) or {}).get("value")
        after = ((summary.get("grades") or {}).get(grade) or {}).get("value")
        if before is None or after is None or round(float(after) - float(before), 2) == 0:
            continue

        quantity = int(item.get("quantity") or 1)
        each = round(float(after) - float(before), 2)
        changes.append(
            {
                "item_id": item["id"],
                "card": {**card_view(card), "photo_url": summary.get("photo_url") or card_view(card)["photo_url"]},
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
    cards. Each card's cached prices are replaced, never added to."""
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
        cards_by_id = db.get_sports_cards({item["sports_card_id"] for item in items})
        cards = [card for card in cards_by_id.values() if not is_manual_card(card)]
        summaries = await cardsight.get_prices([card["cardsight_id"] for card in cards])
        # Compared before the old prices are overwritten. Only this refresh's
        # changes are kept (replaced by the next one), never a history.
        changes = refresh_changes(items, cards_by_id, summaries)
        db.save_sports_card_prices(
            [(card, summaries[card["cardsight_id"]]) for card in cards if card["cardsight_id"] in summaries]
        )

        # The chart's sports total: price moves are market movement; cards
        # priced for the first time (or no longer priced) are flows.
        repriced = {
            card_id: {**card, "prices": summaries[card["cardsight_id"]]}
            for card_id, card in cards_by_id.items()
            if card["cardsight_id"] in summaries
        }
        record_value(
            "refresh",
            total=collection_total(items, {**cards_by_id, **repriced}),
            market_change=sum(change["change"] for change in changes),
        )

        record = {
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "cards": len(cards),
            "priced": len(summaries),
            "calls": math.ceil(len(cards) / cardsight.PRICING_BATCH),
            "net_change": round(sum(change["change"] for change in changes), 2),
            "changes": changes,
        }
        state_store.set(REFRESH_KEY, record)
        return record
    finally:
        _refreshing = False
