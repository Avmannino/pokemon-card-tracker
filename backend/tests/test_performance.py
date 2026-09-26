"""Cash-flow-neutral performance accounting (app/services/portfolio.py).

Run from backend/:  python -m unittest discover -s tests -v
"""

import unittest
from datetime import datetime, timedelta, timezone

from app.services.portfolio import (
    ENTRY_SETTLEMENT,
    Lot,
    Performance,
    PriceSeries,
    build_dashboard,
)


T0 = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


def at(hours: float) -> datetime:
    return T0 + timedelta(hours=hours)


def snap(value, hours, grade="RAW", source="market", manual=False):
    return {
        "grade": grade,
        "source": source,
        "value": value,
        "observed_at": at(hours).isoformat(),
        "metadata": {"entry_method": "manual"} if manual else {},
    }


def lot(lot_id, prices, added, quantity=1, removals=(), grade="RAW"):
    """prices: [(hours, value)] for the card's market price over time."""
    series = PriceSeries([snap(value, hours, grade) for hours, value in prices], grade)
    return Lot(
        lot_id=lot_id,
        card={"id": lot_id, "name": lot_id},
        grade=grade,
        added_at=at(added),
        quantity=quantity,
        removals=[(at(hours), qty) for hours, qty in removals],
        series=series,
    )


class PerformanceScenarios(unittest.TestCase):
    """The six scenarios from the spec, in one continuous history."""

    def setUp(self):
        # $1,000 start: card A $700 + card C $300, both held from t=0.
        # Add B ($500) at 10h; B rises to $550 at 20h; C ($300) removed at
        # 30h; A falls $75 to $625 at 40h.
        self.perf = Performance(
            [
                lot("A", [(0, 700), (40, 625)], added=0),
                lot("B", [(0, 500), (20, 550)], added=10),
                lot("C", [(0, 300)], added=0, removals=[(30, 1)]),
            ]
        )
        self.start = at(0)

    def raw(self, hours):
        return self.perf.raw_value(at(hours))

    def adjusted(self, hours, include_events=True):
        return self.perf.adjusted_value(self.start, at(hours), include_events)

    def test_1_addition_without_price_movement(self):
        self.assertAlmostEqual(self.raw(11), 1500)
        self.assertAlmostEqual(self.adjusted(11), 1000)

    def test_2_appreciation_after_addition(self):
        self.assertAlmostEqual(self.raw(21), 1550)
        self.assertAlmostEqual(self.adjusted(21), 1050)
        self.assertAlmostEqual(self.adjusted(21) - self.adjusted(0), 50)

    def test_3_removal(self):
        self.assertAlmostEqual(self.adjusted(30, include_events=False), 1050)
        self.assertAlmostEqual(self.adjusted(30), 1050)
        self.assertAlmostEqual(self.raw(30) - self.perf.raw_value(at(30), False), -300)

    def test_4_depreciation(self):
        self.assertAlmostEqual(self.adjusted(41), 975)
        self.assertAlmostEqual(self.adjusted(41) - self.adjusted(31), -75)

    def test_5_multiple_additions(self):
        perf = Performance(
            [
                lot("A", [(0, 2000)], added=0),
                lot("B", [(0, 500)], added=1),
                lot("D", [(0, 250)], added=2),
            ]
        )
        self.assertAlmostEqual(perf.raw_value(at(3)), 2750)
        self.assertAlmostEqual(perf.adjusted_value(at(0), at(3)), 2000)

    def test_6_addition_then_appreciation(self):
        perf = Performance(
            [
                lot("A", [(0, 2000), (3, 2100)], added=0),
                lot("B", [(0, 500), (2, 600)], added=1),
            ]
        )
        report = perf.report(at(0), at(4), timedelta(hours=1))

        self.assertAlmostEqual(report["end_raw_value"], 2700)
        self.assertAlmostEqual(report["net_flows"], 500)
        self.assertAlmostEqual(report["end_adjusted_value"], 2200)
        self.assertAlmostEqual(report["change"], 200)
        # Time-weighted: flat on $2,000, then $2,500 -> $2,700.
        self.assertAlmostEqual(report["change_pct"], 8.0)


class AccountingRules(unittest.TestCase):
    def test_quantities_add_three_remove_one(self):
        perf = Performance(
            [lot("X", [(0, 100), (1, 120)], added=0, quantity=3, removals=[(2, 1)])]
        )
        flows = dict(zip(perf.flow_times, perf.flow_amounts))
        self.assertAlmostEqual(flows[at(0)], 300)
        self.assertAlmostEqual(flows[at(2)], -120)
        self.assertAlmostEqual(perf.raw_value(at(3)), 240)
        # 3 x $20 appreciation, kept after one copy leaves.
        self.assertAlmostEqual(perf.adjusted_value(at(0), at(3)) - 300, 60)

    def test_continuity_at_exact_event_timestamps(self):
        perf = Performance(
            [
                lot("A", [(0, 1037)], added=0),
                lot("B", [(0, 400)], added=5),
                lot("C", [(0, 250)], added=0, removals=[(8, 1)]),
            ]
        )
        for event_hours in (5, 8):
            before = perf.adjusted_value(at(0), at(event_hours), include_events_at_moment=False)
            after = perf.adjusted_value(at(0), at(event_hours))
            self.assertAlmostEqual(before, after)

    def test_range_only_neutralizes_flows_after_its_start(self):
        # Old card added long before the range; new card added mid-range.
        perf = Performance(
            [
                lot("OLD", [(0, 1000), (200, 1100)], added=0),
                lot("NEW", [(0, 400)], added=300),
            ]
        )
        report = perf.report(at(250), at(350), timedelta(hours=10))
        self.assertAlmostEqual(report["start_value"], 1100)
        self.assertAlmostEqual(report["net_flows"], 400)
        self.assertAlmostEqual(report["change"], 0)
        # The old card's earlier gain isn't in this range at all.
        self.assertNotIn(1000, [p["adjusted_value"] for p in report["points"]])

    def test_removed_card_keeps_gains_it_had_while_owned(self):
        perf = Performance(
            [
                lot("A", [(0, 100)], added=0),
                lot("B", [(0, 100), (5, 150)], added=0, removals=[(10, 1)]),
            ]
        )
        report = perf.report(at(0), at(20), timedelta(hours=1))
        self.assertAlmostEqual(report["change"], 50)
        self.assertAlmostEqual(report["end_raw_value"], 100)

    def test_card_owned_only_while_held(self):
        # Price history before the card was added, and after it was removed,
        # must not count.
        perf = Performance(
            [lot("A", [(0, 100), (5, 300), (20, 900)], added=10, removals=[(15, 1)])]
        )
        self.assertEqual(perf.raw_value(at(9)), 0)
        self.assertAlmostEqual(perf.raw_value(at(12)), 300)
        self.assertEqual(perf.raw_value(at(25)), 0)
        self.assertAlmostEqual(perf.report(at(10), at(30), timedelta(hours=1))["change"], 0)

    def test_twr_not_distorted_by_late_large_contribution(self):
        perf = Performance(
            [
                lot("A", [(0, 1000), (10, 1100)], added=0),
                lot("B", [(0, 10000), (30, 10100)], added=20),
            ]
        )
        # +10% on $1,000, then a $10,000 card is added and the portfolio goes
        # $11,100 -> $11,200. Dollar gain is $200 (20% of the starting $1,000),
        # but the return is 1.10 x (11,200 / 11,100) - 1 = 10.99%.
        report = perf.report(at(0), at(40), timedelta(hours=5))
        expected = (1.10 * (11200 / 11100) - 1) * 100
        self.assertAlmostEqual(report["change"], 200)
        self.assertAlmostEqual(report["change_pct"], round(expected, 2), places=2)

    def test_card_added_before_it_had_a_price(self):
        # PSA 10 with no price when added; you enter one 2 days later.
        series = PriceSeries([snap(40, 48, "PSA_10", "psa", manual=True)], "PSA_10")
        perf = Performance(
            [Lot("G", {"id": "G"}, "PSA_10", at(0), 1, [], series)]
        )
        self.assertAlmostEqual(perf.report(at(0), at(60), timedelta(hours=6))["change"], 0)

    def test_first_manual_price_corrects_automated_guess(self):
        # TCGGO guessed $233.35 at add; your first manual entry says $321.
        series = PriceSeries(
            [
                snap(233.35, 1, "PSA_10", "tcggo"),
                snap(233.35, 30, "PSA_10", "tcggo"),
                snap(321, 96, "PSA_10", "psa", manual=True),
                snap(233.35, 100, "PSA_10", "tcggo"),
            ],
            "PSA_10",
        )
        perf = Performance([Lot("G", {"id": "G"}, "PSA_10", at(0), 1, [], series)])
        report = perf.report(at(0), at(120), timedelta(hours=6))
        self.assertAlmostEqual(report["change"], 0)
        self.assertAlmostEqual(report["end_raw_value"], 321)

    def test_price_entered_right_after_adding_is_the_entry_value(self):
        # Card already had an older manual price ($67.75). You re-add it and
        # enter $75 a minute later: that's its entry value, not a gain.
        snapshots = [
            snap(67.75, 0, "PSA_10", "psa", manual=True),
            snap(75, 100 + 1 / 60, "PSA_10", "psa", manual=True),
        ]
        series = PriceSeries(snapshots, "PSA_10")
        perf = Performance([Lot("M", {"id": "M"}, "PSA_10", at(100), 1, [], series)])
        report = perf.report(at(100), at(120), timedelta(hours=1))
        self.assertAlmostEqual(report["change"], 0)
        # Continuous through the settling entry.
        self.assertAlmostEqual(
            perf.adjusted_value(at(100), at(100)),
            perf.adjusted_value(at(100), at(100 + 2 / 60)),
        )

    def test_manual_update_after_settlement_window_is_a_real_change(self):
        later = ENTRY_SETTLEMENT + timedelta(hours=48)
        snapshots = [
            snap(67.75, 0, "PSA_10", "psa", manual=True),
            snap(75, 100 + later.total_seconds() / 3600, "PSA_10", "psa", manual=True),
        ]
        series = PriceSeries(snapshots, "PSA_10")
        perf = Performance([Lot("M", {"id": "M"}, "PSA_10", at(100), 1, [], series)])
        report = perf.report(at(100), at(200), timedelta(hours=5))
        self.assertAlmostEqual(report["change"], 7.25)


class DashboardShape(unittest.TestCase):
    def test_dashboard_from_rows(self):
        now = at(40)
        items = [
            {"id": "i1", "card_id": "c1", "ownership_grade": "RAW", "quantity": 1,
             "created_at": at(0).isoformat()},
            {"id": "i2", "card_id": "c2", "ownership_grade": "RAW", "quantity": 1,
             "created_at": at(10).isoformat()},
        ]
        # c3 was added then fully removed (only its events remain).
        events = [
            {"id": 1, "collection_item_id": "i3", "card_id": "c3", "grade": "RAW",
             "event_type": "ADD", "quantity": 1, "occurred_at": at(0).isoformat(),
             "market_value_per_card": 300, "recorded_by": "app"},
            {"id": 2, "collection_item_id": "i3", "card_id": "c3", "grade": "RAW",
             "event_type": "REMOVE", "quantity": 1, "occurred_at": at(30).isoformat(),
             "market_value_per_card": 300, "recorded_by": "app"},
        ]
        cards = {cid: {"id": cid, "name": cid} for cid in ("c1", "c2", "c3")}
        snapshots = {
            "c1": [snap(700, 0), snap(625, 35)],
            "c2": [snap(500, 0), snap(550, 20)],
            "c3": [snap(300, 0)],
        }

        result = build_dashboard(items, events, cards, snapshots, now=now)
        full = result["ranges"]["ALL"]

        self.assertAlmostEqual(result["current_total_value"], 1175)
        self.assertAlmostEqual(full["start_value"], 1000)
        self.assertAlmostEqual(full["end_adjusted_value"], 975)
        self.assertAlmostEqual(full["change"], -25)
        self.assertEqual(result["tracking"]["lots_without_recorded_add"], 2)

        for point in full["points"]:
            self.assertAlmostEqual(
                point["adjusted_value"],
                point["raw_value"] - point["cumulative_net_flow"],
                places=2,
            )

        # Only price moves change the line: +50 (c2) then -75 (c1).
        steps = {
            round(b["adjusted_value"] - a["adjusted_value"], 2)
            for a, b in zip(full["points"], full["points"][1:])
        }
        self.assertTrue(steps <= {0.0, 50.0, -75.0}, steps)


if __name__ == "__main__":
    unittest.main()
