import bisect
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from .valuation import build_market_values


# How far back the value-over-time chart goes, at most. Keeps the response
# small for long-lived collections without losing "all time" meaning for
# anyone who has been tracking for less than a year.
MAX_HISTORY_DAYS = 365

# Windows the portfolio-level change is reported over, in (label, days-back)
# form.
MOVER_WINDOWS = [
    ("1D", 1),
    ("1W", 7),
    ("1M", 30),
    ("3M", 90),
]

# Each biggest-mover entry reports its change over these windows.
MOVER_CARD_WINDOWS = ("1W", "1M")

TOP_MOVER_COUNT = 3


def _parse_observed_at(value: str) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _end_of_day_utc(day: date) -> datetime:
    return datetime.combine(day, time(23, 59, 59, 999999), tzinfo=timezone.utc)


def _estimate_as_of(
    snapshots_desc: list[dict[str, Any]],
    grade: str,
    cutoff: datetime,
) -> float | None:
    """Median-of-latest-per-source estimate for `grade`, using only snapshots
    observed at or before `cutoff`. Mirrors build_market_values' "current"
    logic exactly, just replayed against an earlier point in time."""
    filtered = [
        snapshot
        for snapshot in snapshots_desc
        if _parse_observed_at(snapshot["observed_at"]) <= cutoff
    ]
    return build_market_values(filtered).get(grade, {}).get("estimate")


def _item_breakpoints(
    snapshots_desc: list[dict[str, Any]],
    grade: str,
) -> tuple[list[date], list[float]]:
    """The value of one collection item's owned grade only changes on days
    a new snapshot lands for it. Returns those step-change dates (ascending)
    paired with the estimate effective from that date forward, so later
    lookups are a cheap bisect instead of rescanning every snapshot."""
    change_dates = sorted(
        {
            _parse_observed_at(snapshot["observed_at"]).date()
            for snapshot in snapshots_desc
            if snapshot.get("grade") == grade
        }
    )

    dates: list[date] = []
    estimates: list[float] = []

    for day in change_dates:
        estimate = _estimate_as_of(snapshots_desc, grade, _end_of_day_utc(day))
        if estimate is not None:
            dates.append(day)
            estimates.append(estimate)

    return dates, estimates


def _value_at(breakpoints: tuple[list[date], list[float]], day: date) -> float | None:
    """Per-card estimate as of `day`, forward-filled from the last known
    price.

    Days before the card's first known price fall back to that first price
    rather than counting as zero. The chart values the collection you hold
    *now* at each day's market prices, so adding a card lifts the whole curve
    instead of spiking on the day it was added, and day-to-day movement is
    only ever price movement. A card with no price data at all stays None.
    """
    dates, estimates = breakpoints

    if not dates:
        return None

    index = bisect.bisect_right(dates, day) - 1
    return estimates[max(index, 0)]


def build_dashboard(
    entries: list[dict[str, Any]],
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    entries: one dict per collection item, each with:
        - "item": the collection_items row (needs quantity, ownership_grade)
        - "card": the cards row
        - "snapshots": that card's price_snapshots, newest first
    """
    now = now or datetime.now(timezone.utc)
    today = now.date()

    per_item = []
    change_dates: set[date] = set()

    for entry in entries:
        item = entry["item"]
        breakpoints = _item_breakpoints(
            entry["snapshots"], item["ownership_grade"]
        )
        change_dates.update(breakpoints[0])

        per_item.append(
            {
                "card": entry["card"],
                "grade": item["ownership_grade"],
                "quantity": int(item.get("quantity") or 1),
                "breakpoints": breakpoints,
            }
        )

    earliest = min(change_dates) if change_dates else today
    earliest = max(earliest, today - timedelta(days=MAX_HISTORY_DAYS))
    day_count = (today - earliest).days

    # Before this date at least one held card is flat-lined at its first
    # known price, because we have no market data from back then.
    first_priced_days = [
        entry["breakpoints"][0][0]
        for entry in per_item
        if entry["breakpoints"][0]
    ]
    full_history_since = (
        max(first_priced_days).isoformat() if first_priced_days else None
    )

    def total_at(day: date) -> float | None:
        total = 0.0
        known = False

        for entry in per_item:
            value = _value_at(entry["breakpoints"], day)
            if value is not None:
                total += value * entry["quantity"]
                known = True

        return round(total, 2) if known else None

    history = [
        {"date": (earliest + timedelta(days=offset)).isoformat(), "total_value": total_at(earliest + timedelta(days=offset))}
        for offset in range(day_count + 1)
    ]

    current_total = total_at(today)

    performance: dict[str, Any] = {}

    for label, days_back in MOVER_WINDOWS:
        past_total = total_at(today - timedelta(days=days_back))

        change = None
        change_pct = None
        if current_total is not None and past_total is not None and past_total != 0:
            change = round(current_total - past_total, 2)
            change_pct = round((change / past_total) * 100, 2)

        performance[label] = {
            "current_value": current_total,
            "past_value": past_total,
            "change": change,
            "change_pct": change_pct,
        }

    window_days = dict(MOVER_WINDOWS)
    ranked = []

    for entry in per_item:
        now_value = _value_at(entry["breakpoints"], today)
        if now_value is None:
            continue

        changes: dict[str, Any] = {}
        for label in MOVER_CARD_WINDOWS:
            past_value = _value_at(
                entry["breakpoints"],
                today - timedelta(days=window_days[label]),
            )
            if past_value is None:
                continue

            changes[label] = {
                "change": round((now_value - past_value) * entry["quantity"], 2),
                "change_pct": (
                    round(((now_value - past_value) / past_value) * 100, 2)
                    if past_value
                    else None
                ),
            }

        # Ranked by the largest move in either reported window, so a card that
        # jumped this week and one that drifted all month both surface.
        magnitude = max(
            (abs(window["change"]) for window in changes.values()),
            default=0.0,
        )
        if magnitude == 0:
            continue

        ranked.append(
            (
                magnitude,
                {
                    "card": entry["card"],
                    "grade": entry["grade"],
                    "quantity": entry["quantity"],
                    "value_each": round(now_value, 2),
                    "current_value": round(now_value * entry["quantity"], 2),
                    "changes": changes,
                },
            )
        )

    ranked.sort(key=lambda row: row[0], reverse=True)
    top_movers = [row for _, row in ranked[:TOP_MOVER_COUNT]]

    top_cards = []
    for entry in per_item:
        value = _value_at(entry["breakpoints"], today)
        if value is None:
            continue

        top_cards.append(
            {
                "card": entry["card"],
                "grade": entry["grade"],
                "quantity": entry["quantity"],
                "value_each": round(value, 2),
                "value": round(value * entry["quantity"], 2),
            }
        )

    top_cards.sort(key=lambda row: row["value"], reverse=True)

    return {
        "current_total_value": current_total,
        "full_history_since": full_history_since,
        "history": history,
        "performance": performance,
        "top_movers": top_movers,
        "top_cards": top_cards[:5],
    }
