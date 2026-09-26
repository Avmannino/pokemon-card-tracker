"""Collection performance, accounted like a brokerage account.

Two different numbers come out of here:

- Raw value: what the cards you owned at a moment were worth at that moment.
  Adding a card raises it, removing one lowers it.
- Adjusted (performance) value: raw value minus the net value of cards added
  or removed since the start of the range. Cards entering or leaving are
  treated like deposits and withdrawals, so only price movement moves it.

For a range starting at t0:

    adjusted(t0) = raw(t0)
    adjusted(t)  = raw(t) - (value added - value removed in (t0, t])

An addition is valued at the card's market value the moment it joined, a
removal at its market value the moment it left. Percentages are
time-weighted returns: the range is split at every addition/removal and the
sub-period returns are chained, so a large addition late in a range can't
distort the percentage.

Ownership is time-aware: a card only counts while you actually held it, and a
removed card keeps the gains or losses it had while you owned it.

Ordering at a single moment: price observations at a moment apply first,
then additions/removals at that same moment, and a range includes events at
its start in its starting value.
"""

import bisect
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .valuation import estimate_from_sources, is_manual_entry


# Chart ranges and how far back each looks. A range longer than the
# collection's history starts when the first card was added.
RANGES: list[tuple[str, timedelta | None]] = [
    ("1D", timedelta(days=1)),
    ("1W", timedelta(days=7)),
    ("1M", timedelta(days=30)),
    ("3M", timedelta(days=90)),
    ("1Y", timedelta(days=365)),
    ("ALL", None),
]

# Roughly how many points each range's chart gets.
POINTS_PER_RANGE = 120

# You add a card, then look up and enter its price. A manual price entered
# this soon after adding counts as the card's value when it joined the
# collection, not as a market move after it joined.
ENTRY_SETTLEMENT = timedelta(minutes=60)

# Each biggest-mover entry reports its change over these windows.
MOVER_CARD_WINDOWS = (("1W", timedelta(days=7)), ("1M", timedelta(days=30)))

TOP_MOVER_COUNT = 3


def parse_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        moment = value
    else:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)

    return moment


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class PriceSeries:
    """One card's market price for one grade over time.

    A step function that only changes when a price observation lands,
    valued by the same rule as the current price (newest value per source,
    manual entries win, median across sources), replayed at each moment.

    What the price was before it was first known:
    - Before the first observation, the first known price stands in, so a
      card added before anything priced it enters at its first price.
    - Your first manual entry for a grade supersedes every automated price
      before it: it corrects a guess (e.g. TCGGO's graded prices, which blend
      different prints) rather than recording a market move. Later manual
      entries are real changes from when you made them.
    """

    def __init__(self, snapshots: list[dict[str, Any]], grade: str) -> None:
        rows = sorted(
            (row for row in snapshots if row.get("grade") == grade),
            key=lambda row: (parse_ts(row["observed_at"]), row.get("id") or 0),
        )

        self.manual_times = [
            parse_ts(row["observed_at"]) for row in rows if is_manual_entry(row)
        ]
        self.observed_days = {parse_ts(row["observed_at"]).date() for row in rows}
        self.times: list[datetime] = []
        self.values: list[float] = []

        first_manual = self.manual_times[0] if self.manual_times else None
        latest: dict[str, dict[str, Any]] = {}

        for row in rows:
            observed = parse_ts(row["observed_at"])
            latest[row["source"]] = row

            if first_manual is not None and observed < first_manual:
                continue

            estimate, _ = estimate_from_sources(latest.values())
            if estimate is None:
                continue

            if self.times and self.times[-1] == observed:
                self.values[-1] = estimate
            else:
                self.times.append(observed)
                self.values.append(estimate)

    def at(self, moment: datetime) -> float | None:
        if not self.times:
            return None

        index = bisect.bisect_right(self.times, moment) - 1
        return self.values[max(index, 0)]


@dataclass
class Lot:
    """One collection_items row over its life: added once, then possibly
    removed a few copies at a time."""

    lot_id: str
    card: dict[str, Any]
    grade: str
    added_at: datetime
    quantity: int
    removals: list[tuple[datetime, int]]
    series: PriceSeries
    add_event: dict[str, Any] | None = None
    remove_events: list[dict[str, Any]] = field(default_factory=list)
    entry_price: float | None = None
    entry_settled_at: datetime | None = None

    def __post_init__(self) -> None:
        settling = [
            moment
            for moment in self.series.manual_times
            if self.added_at < moment <= self.added_at + ENTRY_SETTLEMENT
        ]

        if settling:
            # Until the price you entered right after adding lands, the card
            # is held at that price, so the addition is valued at it too.
            self.entry_settled_at = max(settling)
            self.entry_price = self.series.at(self.entry_settled_at)
        else:
            self.entry_price = self.series.at(self.added_at)

    def held(self, moment: datetime, include_events_at_moment: bool = True) -> int:
        def happened(event_time: datetime) -> bool:
            return event_time < moment or (
                include_events_at_moment and event_time == moment
            )

        if not happened(self.added_at):
            return 0

        removed = sum(qty for when, qty in self.removals if happened(when))
        return max(self.quantity - removed, 0)

    def price(self, moment: datetime) -> float | None:
        if self.entry_settled_at is not None and moment < self.entry_settled_at:
            return self.entry_price

        return self.series.at(moment)

    def flows(self):
        """(moment, signed market value) for the addition and each removal.
        A card with no price at all is worth 0 on both sides."""
        yield self.added_at, self.quantity * (self.price(self.added_at) or 0.0)

        for when, qty in self.removals:
            yield when, -qty * (self.price(when) or 0.0)


class Performance:
    """Raw value, flows and adjusted value for a set of lots."""

    def __init__(self, lots: list[Lot]) -> None:
        self.lots = lots

        flows: dict[datetime, float] = defaultdict(float)
        for lot in lots:
            for moment, amount in lot.flows():
                flows[moment] += amount

        self.flow_times = sorted(flows)
        self.flow_amounts = [flows[moment] for moment in self.flow_times]
        self.first_added = min((lot.added_at for lot in lots), default=None)

    def raw_value(self, moment: datetime, include_events_at_moment: bool = True) -> float:
        total = 0.0

        for lot in self.lots:
            held = lot.held(moment, include_events_at_moment)
            if held:
                price = lot.price(moment)
                if price is not None:
                    total += held * price

        return total

    def net_flows(self, start: datetime, end: datetime) -> float:
        """Value added minus value removed in (start, end]."""
        low = bisect.bisect_right(self.flow_times, start)
        high = bisect.bisect_right(self.flow_times, end)
        return sum(self.flow_amounts[low:high])

    def adjusted_value(
        self,
        start: datetime,
        moment: datetime,
        include_events_at_moment: bool = True,
    ) -> float:
        flows_through = moment if include_events_at_moment else moment - timedelta.resolution
        return self.raw_value(moment, include_events_at_moment) - self.net_flows(
            start, max(flows_through, start)
        )

    def report(self, start: datetime, end: datetime, step: timedelta) -> dict[str, Any]:
        """Chart points from start to end, and the range's performance."""
        samples = [start]
        moment = start + step
        while moment < end:
            samples.append(moment)
            moment += step
        if end > start:
            samples.append(end)

        sample_set = set(samples)
        low = bisect.bisect_right(self.flow_times, start)
        high = bisect.bisect_right(self.flow_times, end)
        flow_at = dict(zip(self.flow_times[low:high], self.flow_amounts[low:high]))

        start_value = self.raw_value(start)
        previous_after = start_value
        growth = 1.0
        cumulative = 0.0
        since_last_point = 0.0
        points: list[dict[str, Any]] = []

        for moment in sorted(sample_set | set(flow_at)):
            if moment == start:
                points.append(
                    _point(moment, start_value, 0.0, 0.0, start_value, growth)
                )
                continue

            # Time-weighted return: value just before this moment's additions/
            # removals, against value just after the previous moment's.
            before = self.raw_value(moment, include_events_at_moment=False)
            if previous_after > 0:
                growth *= before / previous_after

            flow = flow_at.get(moment, 0.0)
            cumulative += flow
            since_last_point += flow
            previous_after = self.raw_value(moment)

            if moment in sample_set:
                points.append(
                    _point(
                        moment,
                        previous_after,
                        since_last_point,
                        cumulative,
                        start_value,
                        growth,
                    )
                )
                since_last_point = 0.0

        last = points[-1]
        return {
            "start": _iso(start),
            "end": _iso(end),
            "start_value": round(start_value, 2),
            "end_raw_value": last["raw_value"],
            "end_adjusted_value": last["adjusted_value"],
            "net_flows": round(cumulative, 2),
            "change": last["change"],
            "change_pct": last["change_pct"],
            "points": points,
        }


def _point(
    moment: datetime,
    raw: float,
    net_flow: float,
    cumulative: float,
    start_value: float,
    growth: float,
) -> dict[str, Any]:
    adjusted = raw - cumulative
    return {
        "timestamp": _iso(moment),
        "raw_value": round(raw, 2),
        "net_flow": round(net_flow, 2),
        "cumulative_net_flow": round(cumulative, 2),
        "adjusted_value": round(adjusted, 2),
        "change": round(adjusted - start_value, 2),
        "change_pct": round((growth - 1) * 100, 2),
    }


def _step(span: timedelta) -> timedelta:
    hours = max(1, math.ceil(span.total_seconds() / 3600 / POINTS_PER_RANGE))
    return timedelta(hours=hours)


def build_lots(
    items: list[dict[str, Any]],
    events: list[dict[str, Any]],
    cards: dict[str, dict[str, Any]],
    snapshots_by_card: dict[str, list[dict[str, Any]]],
) -> tuple[list[Lot], int]:
    """Lots from the ADD/REMOVE history plus the current collection rows.

    A current row with no ADD event predates event tracking (or the events
    table isn't set up yet): its creation time is when it was added, and its
    current quantity plus anything since removed is what was added. Returns
    the lots and how many recorded lots couldn't be valued (no ADD and no
    current row, or a missing card)."""
    history: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"add": None, "removes": []}
    )

    for event in sorted(
        events,
        key=lambda row: (parse_ts(row["occurred_at"]), row.get("id") or 0),
    ):
        record = history[event["collection_item_id"]]
        if event["event_type"] == "ADD":
            record["add"] = record["add"] or event
        else:
            record["removes"].append(event)

    items_by_id = {item["id"]: item for item in items}
    lot_ids = list(items_by_id) + [lot_id for lot_id in history if lot_id not in items_by_id]

    series_by_key: dict[tuple[str, str], PriceSeries] = {}
    lots: list[Lot] = []
    skipped = 0

    for lot_id in lot_ids:
        record = history.get(lot_id) or {"add": None, "removes": []}
        add = record["add"]
        item = items_by_id.get(lot_id)
        removed = sum(int(event["quantity"]) for event in record["removes"])

        if add is not None:
            card_id = add["card_id"]
            grade = add["grade"]
            added_at = parse_ts(add["occurred_at"])
            quantity = int(add["quantity"])
        elif item is not None:
            card_id = item["card_id"]
            grade = item["ownership_grade"]
            added_at = parse_ts(item["created_at"])
            quantity = int(item["quantity"]) + removed
        else:
            skipped += 1
            continue

        card = cards.get(card_id)
        if card is None:
            skipped += 1
            continue

        key = (card_id, grade)
        if key not in series_by_key:
            series_by_key[key] = PriceSeries(snapshots_by_card.get(card_id, []), grade)

        lots.append(
            Lot(
                lot_id=lot_id,
                card=card,
                grade=grade,
                added_at=added_at,
                quantity=quantity,
                removals=[
                    (parse_ts(event["occurred_at"]), int(event["quantity"]))
                    for event in record["removes"]
                ],
                series=series_by_key[key],
                add_event=add,
                remove_events=record["removes"],
            )
        )

    return lots, skipped


def lot_price(
    snapshots: list[dict[str, Any]],
    grade: str,
    added_at: Any,
    moment: Any,
) -> float | None:
    """Market value per card of a lot at `moment`, valued exactly the way
    the performance history values it (used to record ADD/REMOVE events)."""
    lot = Lot(
        lot_id="",
        card={},
        grade=grade,
        added_at=parse_ts(added_at),
        quantity=0,
        removals=[],
        series=PriceSeries(snapshots, grade),
    )
    return lot.price(parse_ts(moment))


def _event_log(lots: list[Lot]) -> list[dict[str, Any]]:
    """Every addition/removal and the value it was booked at, for auditing.

    valuation_basis says where that value came from:
    - recorded: the market value stored when the event happened.
    - settled: the price you entered within ENTRY_SETTLEMENT of adding it.
    - restated: differs from the stored value because a later first manual
      entry corrected the automated price it was recorded at.
    - price_history: nothing was stored (backfilled or unpriced at the
      time), so it's the market value from price history at that moment.
    """
    log = []

    for lot in lots:
        entries = [("ADD", lot.added_at, lot.quantity, lot.add_event)] + [
            ("REMOVE", when, qty, event)
            for (when, qty), event in zip(lot.removals, lot.remove_events)
        ]

        for kind, when, qty, stored in entries:
            price = lot.price(when)
            recorded = _as_float(stored.get("market_value_per_card")) if stored else None

            if kind == "ADD" and lot.entry_settled_at is not None:
                basis = "settled"
            elif recorded is None:
                basis = "price_history"
            elif price is not None and abs(price - recorded) < 0.005:
                basis = "recorded"
            else:
                basis = "restated"

            sign = -1 if kind == "REMOVE" else 1
            log.append(
                {
                    "timestamp": _iso(when),
                    "event_type": kind,
                    "lot_id": lot.lot_id,
                    "card_id": lot.card.get("id"),
                    "card_name": lot.card.get("name"),
                    "grade": lot.grade,
                    "quantity": qty,
                    "market_value_per_card": round(price, 2) if price is not None else None,
                    "recorded_market_value_per_card": recorded,
                    "flow_value": round(sign * qty * (price or 0.0), 2),
                    "valuation_basis": basis,
                    "recorded_by": stored.get("recorded_by") if stored else "synthesized",
                }
            )

    log.sort(key=lambda row: row["timestamp"])
    return log


def _top_movers(lots: list[Lot], now: datetime) -> list[dict[str, Any]]:
    ranked = []

    for lot in lots:
        held = lot.held(now)
        now_price = lot.price(now)
        if not held or now_price is None:
            continue

        changes: dict[str, Any] = {}
        for label, window in MOVER_CARD_WINDOWS:
            # Only the part of the window you've owned the card for.
            since = max(now - window, lot.added_at)
            start_price = lot.price(since)
            if start_price is None:
                continue

            changes[label] = {
                "change": round((now_price - start_price) * held, 2),
                "change_pct": (
                    round((now_price - start_price) / start_price * 100, 2)
                    if start_price
                    else None
                ),
            }

        # Ranked by the largest move in either window, so a card that jumped
        # this week and one that drifted all month both surface.
        magnitude = max((abs(window["change"]) for window in changes.values()), default=0.0)
        if magnitude == 0:
            continue

        ranked.append(
            (
                magnitude,
                {
                    "card": lot.card,
                    "grade": lot.grade,
                    "quantity": held,
                    "value_each": round(now_price, 2),
                    "current_value": round(now_price * held, 2),
                    "changes": changes,
                },
            )
        )

    ranked.sort(key=lambda row: row[0], reverse=True)
    return [row for _, row in ranked[:TOP_MOVER_COUNT]]


def _top_cards(lots: list[Lot], now: datetime) -> list[dict[str, Any]]:
    rows = []

    for lot in lots:
        held = lot.held(now)
        price = lot.price(now)
        if not held or price is None:
            continue

        rows.append(
            {
                "card": lot.card,
                "grade": lot.grade,
                "quantity": held,
                "value_each": round(price, 2),
                "value": round(price * held, 2),
            }
        )

    rows.sort(key=lambda row: row["value"], reverse=True)
    return rows[:5]


def build_dashboard(
    items: list[dict[str, Any]],
    events: list[dict[str, Any]],
    cards: dict[str, dict[str, Any]],
    snapshots_by_card: dict[str, list[dict[str, Any]]],
    now: datetime | None = None,
    events_recorded: bool = True,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    lots, skipped = build_lots(items, events, cards, snapshots_by_card)
    performance = Performance(lots)

    ranges: dict[str, Any] = {}
    if performance.first_added is not None:
        first_added = min(performance.first_added, now)

        for key, duration in RANGES:
            start = first_added if duration is None else max(now - duration, first_added)
            ranges[key] = performance.report(start, now, _step(now - start))
    else:
        for key, _ in RANGES:
            ranges[key] = {"points": [], "change": None, "change_pct": None}

    return {
        "current_total_value": round(performance.raw_value(now), 2),
        "ranges": ranges,
        "top_movers": _top_movers(lots, now),
        "top_cards": _top_cards(lots, now),
        "events": _event_log(lots),
        "tracking": {
            "events_recorded": events_recorded,
            "lots_without_recorded_add": sum(1 for lot in lots if lot.add_event is None),
            "lots_skipped": skipped,
            "tracked_since": (
                _iso(performance.first_added) if performance.first_added else None
            ),
        },
    }


def value_change(
    snapshots: list[dict[str, Any]],
    grade: str,
    days_back: int = 7,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Change in one card's market price for `grade` over the last
    `days_back` days, or None when there isn't a price for it yet."""
    now = now or datetime.now(timezone.utc)
    series = PriceSeries(snapshots, grade)

    current = series.at(now)
    past = series.at(now - timedelta(days=days_back))

    if current is None or past is None:
        return None

    change = round(current - past, 2)

    return {
        "change": change,
        "change_pct": round((change / past) * 100, 2) if past else None,
        # False while the card has only one day of prices on record, so a $0
        # change means "no history yet" rather than "hasn't moved".
        "has_history": len(series.observed_days) > 1,
    }
