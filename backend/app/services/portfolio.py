import bisect
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from .valuation import build_market_values


# How far back the value-over-time chart goes, at most. Keeps the response
# small for long-lived collections without losing "all time" meaning for
# anyone who has been tracking for less than a year.
MAX_HISTORY_DAYS = 365

# Windows shown as "biggest mover" cards, in (label, days-back) form.
MOVER_WINDOWS = [
    ("1D", 1),
    ("1W", 7),
    ("1M", 30),
    ("3M", 90),
]


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
    """Forward-filled per-card estimate as of `day` (None before the card's
    first known value)."""
    dates, estimates = breakpoints
    index = bisect.bisect_right(dates, day) - 1
    return estimates[index] if index >= 0 else None


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
                "quantity": int(item.get("quantity") or 1),
                "breakpoints": breakpoints,
            }
        )

    earliest = min(change_dates) if change_dates else today
    earliest = max(earliest, today - timedelta(days=MAX_HISTORY_DAYS))
    day_count = (today - earliest).days

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
    movers: dict[str, Any] = {}

    for label, days_back in MOVER_WINDOWS:
        cutoff_day = today - timedelta(days=days_back)
        past_total = total_at(cutoff_day)

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

        best = None
        for entry in per_item:
            now_value = _value_at(entry["breakpoints"], today)
            past_value = _value_at(entry["breakpoints"], cutoff_day)

            if now_value is None or past_value is None:
                continue

            item_change = round((now_value - past_value) * entry["quantity"], 2)
            if item_change == 0:
                continue

            item_change_pct = (
                round(((now_value - past_value) / past_value) * 100, 2)
                if past_value
                else None
            )

            if best is None or abs(item_change) > abs(best["change"]):
                best = {
                    "card": entry["card"],
                    "change": item_change,
                    "change_pct": item_change_pct,
                    "current_value": round(now_value * entry["quantity"], 2),
                }

        movers[label] = best

    top_cards = []
    for entry in per_item:
        value = _value_at(entry["breakpoints"], today)
        if value is None:
            continue

        top_cards.append(
            {
                "card": entry["card"],
                "value": round(value * entry["quantity"], 2),
            }
        )

    top_cards.sort(key=lambda row: row["value"], reverse=True)

    return {
        "current_total_value": current_total,
        "history": history,
        "performance": performance,
        "movers": movers,
        "top_cards": top_cards[:5],
    }
