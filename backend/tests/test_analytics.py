"""Portfolio value attribution and grading opportunities.

Run from backend/:  python -m unittest discover -t . -s tests -v
"""

import unittest
from datetime import datetime, timedelta, timezone

from unittest import mock

from app.services import grading, price_sync, pricecharting
from app.services.portfolio import (
    Lot,
    Performance,
    PriceSeries,
    build_dashboard,
    start_of_today,
    value_changes,
)
from app.services.valuation import build_market_values


T0 = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


def at(hours: float) -> datetime:
    return T0 + timedelta(hours=hours)


def lot(lot_id, prices, added, quantity=1, removals=(), card_id=None):
    """prices: [(hours, value)] for the card's market price over time."""
    series = PriceSeries(
        [
            {"grade": "RAW", "source": "market", "value": value,
             "observed_at": at(hours).isoformat(), "metadata": {}}
            for hours, value in prices
        ],
        "RAW",
    )
    return Lot(
        lot_id=lot_id,
        card={"id": card_id or lot_id, "name": card_id or lot_id},
        grade="RAW",
        added_at=at(added),
        quantity=quantity,
        removals=[(at(hours), qty) for hours, qty in removals],
        series=series,
    )


class Attribution(unittest.TestCase):
    def attribute(self, lots, start=0, end=20):
        return Performance(lots).attribution(at(start), at(end))

    def assertSplit(self, result, total, market, added, removed):
        self.assertAlmostEqual(result["total_change"], total)
        self.assertAlmostEqual(result["market_change"], market)
        self.assertAlmostEqual(result["acquisitions"], added)
        self.assertAlmostEqual(result["removals"], removed)

    def test_1_adding_a_card_is_not_a_gain(self):
        result = self.attribute([
            lot("A", [(0, 1000)], added=0),
            lot("B", [(0, 100)], added=10),
        ])
        self.assertSplit(result, total=100, market=0, added=100, removed=0)

    def test_2_price_rise_of_a_held_card_is_market(self):
        result = self.attribute([lot("A", [(0, 100), (10, 120)], added=0)])
        self.assertSplit(result, total=20, market=20, added=0, removed=0)

    def test_3_added_at_its_value_then_rises(self):
        result = self.attribute([lot("B", [(0, 100), (10, 130)], added=5)])
        self.assertSplit(result, total=130, market=30, added=100, removed=0)

    def test_4_removal_is_not_a_market_loss(self):
        result = self.attribute([
            lot("A", [(0, 1000)], added=0),
            lot("C", [(0, 50)], added=0, removals=[(10, 1)]),
        ])
        self.assertSplit(result, total=-50, market=0, added=0, removed=-50)

    def test_5_second_copy_is_an_acquisition(self):
        # The app books another copy as its own lot.
        result = self.attribute([
            lot("first", [(0, 100), (15, 110)], added=0, card_id="X"),
            lot("second", [(0, 100), (15, 110)], added=10, card_id="X"),
        ])
        # +100 for the second copy; then +$10 on each of the two copies.
        self.assertSplit(result, total=120, market=20, added=100, removed=0)
        [contributor] = result["contributors"]
        self.assertEqual(contributor["quantity"], 2)
        self.assertAlmostEqual(contributor["market_change"], 20)

    def test_6_only_movement_after_acquisition_counts(self):
        # It rose 100 -> 150 before you bought it, then to 160.
        result = self.attribute([lot("B", [(0, 100), (5, 150), (15, 160)], added=10)])
        self.assertSplit(result, total=160, market=10, added=150, removed=0)

    def test_removed_cards_stop_counting(self):
        result = self.attribute([
            lot("C", [(0, 50), (15, 80)], added=0, removals=[(10, 1)]),
        ])
        self.assertSplit(result, total=-50, market=0, added=0, removed=-50)

    def test_contributors_rank_by_dollars_not_percent(self):
        result = self.attribute([
            lot("cheap", [(0, 2), (10, 4)], added=0),
            lot("pricey", [(0, 300), (10, 330)], added=0),
            lot("faller", [(0, 50), (10, 45)], added=0),
        ])
        self.assertEqual(
            [(c["card"]["id"], c["market_change"]) for c in result["contributors"]],
            [("pricey", 30), ("faller", -5), ("cheap", 2)],
        )

    def test_matches_the_charts_market_change(self):
        lots = [
            lot("A", [(0, 700), (40, 625)], added=0),
            lot("B", [(0, 500), (20, 550)], added=10),
            lot("C", [(0, 300)], added=0, removals=[(30, 1)]),
        ]
        performance = Performance(lots)
        result = performance.attribution(at(0), at(50))
        chart = performance.report(at(0), at(50), timedelta(hours=1))
        self.assertAlmostEqual(result["market_change"], chart["change"])
        # $1,000 (A 700 + C 300) -> $1,175 (A 625 + B 550): B added at 500
        # and rose 50, C left at 300, A fell 75.
        self.assertSplit(result, total=175, market=-25, added=500, removed=-300)


def entry(raw, psa9, psa10, sales=5, card_id="c1"):
    def value(amount):
        return {"value": amount, "method": "graded_sales_median", "summary": None,
                "as_of": "2026-10-24T00:00:00+00:00", "sales": sales if amount is not None else None}

    return {
        "collection_item_id": f"item-{card_id}",
        "card": {"id": card_id, "name": card_id},
        "language": "English",
        "quantity": 1,
        "small_hit": False,
        "raw": {**value(raw), "method": "tcgplayer"},
        "psa9": value(psa9),
        "psa10": value(psa10),
    }


class GradingOpportunities(unittest.TestCase):
    def test_spec_example(self):
        result = grading.evaluate(entry(raw=10, psa9=20, psa10=150), grading_cost=30)
        self.assertTrue(result["available"])
        self.assertEqual(result["psa10_multiple"], 15.0)
        self.assertEqual(result["psa10_net"], 110)
        self.assertEqual(result["psa9_net"], -20)
        self.assertEqual(result["psa10_premium"], 140)
        self.assertEqual(result["psa10_premium_pct"], 1400.0)
        self.assertEqual(result["label"], "High Risk")
        self.assertIn("PSA 10 dependent", result["risk_note"])

    def test_psa9_that_covers_costs_is_a_safer_candidate(self):
        result = grading.evaluate(entry(raw=25, psa9=75, psa10=180), grading_cost=30)
        # 60 x 125/150 = 50 upside, 30 safety (75 >= 55), 10 data.
        self.assertEqual(result["score"], 90)
        self.assertEqual(result["label"], "Excellent Opportunity")

    def test_not_worth_grading_when_psa10_doesnt_pay(self):
        result = grading.evaluate(entry(raw=40, psa9=45, psa10=60), grading_cost=30)
        self.assertEqual(result["label"], "Not Worth Grading")

    def test_missing_graded_prices_are_unavailable(self):
        result = grading.evaluate(entry(raw=10, psa9=None, psa10=None), grading_cost=30)
        self.assertFalse(result["available"])
        self.assertIn("Graded pricing unavailable", result["unavailable_reason"])
        self.assertIsNone(result["psa10_net"])

    def test_zero_raw_value_is_unavailable(self):
        result = grading.evaluate(entry(raw=0, psa9=20, psa10=150), grading_cost=30)
        self.assertFalse(result["available"])

    def test_unknown_psa9_is_high_risk(self):
        result = grading.evaluate(entry(raw=10, psa9=None, psa10=150), grading_cost=30)
        self.assertEqual(result["label"], "High Risk")
        self.assertIsNone(result["psa9_net"])

    def test_variant_mismatch_never_borrows_another_variants_price(self):
        # TCGGO's PSA 10 sales for a product shared by Holo and Reverse Holo.
        card = {
            "id": "rh", "name": "Gengar", "variant": "Reverse_Holofoil",
            "variant_siblings": {"variants": [{"variant": "Holofoil"}, {"variant": "Reverse_Holofoil"}]},
        }
        snapshots = [
            {"grade": "PSA_10", "source": "eBay graded sold median (via TCGGO)", "value": 233.0,
             "observed_at": at(0).isoformat(), "metadata": {"provider": "TCGGO", "sample_size": 5}},
            {"grade": "RAW", "source": "TCGPlayer raw market (via PokeTrace)", "value": 12.0,
             "observed_at": at(0).isoformat(),
             "metadata": {"provider": "PokeTrace", "underlying_source": "tcgplayer", "variant": "Reverse_Holofoil"}},
        ]
        item = {"id": "i1", "card": card, "ownership_grade": "RAW", "quantity": 1,
                "market_values": build_market_values(snapshots, card)}

        [only] = grading.inputs_from_collection([item])
        result = grading.evaluate(only, grading_cost=30)
        self.assertFalse(result["available"])
        self.assertIsNone(result["psa10_value"])

    def test_graded_items_are_excluded_and_copies_are_one_row(self):
        items = [
            {"id": "a", "card": {"id": "c1", "name": "c1"}, "ownership_grade": "RAW", "quantity": 1, "market_values": {}},
            {"id": "b", "card": {"id": "c1", "name": "c1"}, "ownership_grade": "RAW", "quantity": 2, "market_values": {}},
            {"id": "c", "card": {"id": "c2", "name": "c2"}, "ownership_grade": "PSA_10", "quantity": 1, "market_values": {}},
        ]
        inputs = grading.inputs_from_collection(items)
        self.assertEqual([(i["card"]["id"], i["quantity"]) for i in inputs], [("c1", 3)])

    def test_report_puts_available_opportunities_first(self):
        report = grading.report(
            [entry(raw=10, psa9=None, psa10=None, card_id="none"),
             entry(raw=10, psa9=20, psa10=150, card_id="some")],
            grading_cost=30,
        )
        self.assertEqual([row["card"]["id"] for row in report["opportunities"]], ["some", "none"])
        self.assertEqual(report["available"], 1)


class TodayWindow(unittest.TestCase):
    # 10:00 AM Eastern on Jan 2 (the schedule's time zone).
    NOW = datetime(2026, 1, 2, 15, tzinfo=timezone.utc)

    def snapshots(self):
        return [
            {"grade": "RAW", "source": "market", "value": value,
             "observed_at": moment.isoformat(), "metadata": {}}
            for moment, value in (
                (datetime(2026, 1, 1, 20, tzinfo=timezone.utc), 100),  # 3 PM yesterday
                (datetime(2026, 1, 2, 4, tzinfo=timezone.utc), 110),   # 11 PM yesterday
                (datetime(2026, 1, 2, 13, tzinfo=timezone.utc), 120),  # 8 AM today
            )
        ]

    def test_1d_starts_at_midnight_not_24_hours_ago(self):
        self.assertEqual(
            start_of_today(self.NOW), datetime(2026, 1, 2, 5, tzinfo=timezone.utc)
        )
        changes = value_changes(self.snapshots(), "RAW", now=self.NOW)
        # Against 11 PM's $110 (the price at midnight), not 24 hours ago's $100.
        self.assertEqual(changes["1D"]["change"], 10)
        self.assertEqual(changes["1W"]["change"], 20)

    def test_dashboard_1d_range_is_today(self):
        items = [{"id": "i1", "card_id": "c1", "ownership_grade": "RAW", "quantity": 1,
                  "created_at": datetime(2026, 1, 1, 20, tzinfo=timezone.utc).isoformat()}]
        result = build_dashboard(
            items, [], {"c1": {"id": "c1", "name": "c1"}}, {"c1": self.snapshots()}, now=self.NOW
        )
        self.assertEqual(result["ranges"]["1D"]["change"], 10)
        self.assertEqual(result["ranges"]["1D"]["attribution"]["market_change"], 10)


class PricesCountWhenSynced(unittest.TestCase):
    def test_a_price_synced_today_counts_today_even_if_dated_yesterday(self):
        now = datetime(2026, 1, 2, 15, tzinfo=timezone.utc)  # 10 AM Eastern
        snapshots = [
            {"grade": "RAW", "source": "market", "value": 100,
             "observed_at": datetime(2026, 1, 1, 13, tzinfo=timezone.utc).isoformat(),
             "created_at": datetime(2026, 1, 1, 13, tzinfo=timezone.utc).isoformat(),
             "metadata": {}},
            # PokeTrace updated it at 11 PM yesterday; this morning's sync fetched it.
            {"grade": "RAW", "source": "market", "value": 98,
             "observed_at": datetime(2026, 1, 2, 4, tzinfo=timezone.utc).isoformat(),
             "created_at": datetime(2026, 1, 2, 13, tzinfo=timezone.utc).isoformat(),
             "metadata": {}},
        ]
        self.assertEqual(value_changes(snapshots, "RAW", now=now)["1D"]["change"], -2)


class PriceChartingGradingPrices(unittest.TestCase):
    LINK = "https://www.pricecharting.com/game/pokemon-astral-radiance/hoothoot-tg12"

    def snapshots(self):
        return [
            {"grade": "PSA_10", "source": price_sync.PRICE_GUIDE_SOURCE, "value": 410.0,
             "observed_at": at(1).isoformat(),
             "metadata": {"provider": "PriceCharting", "price_label": "PSA 10", "page_url": self.LINK}},
            {"grade": "PSA_10", "source": "eBay graded sold median (via TCGGO)", "value": 400.0,
             "observed_at": at(0).isoformat(), "metadata": {"provider": "TCGGO", "sample_size": 4}},
        ]

    def card(self, link):
        return {"id": "c1", "name": "Hoothoot", "variant": "Holofoil",
                "marketplace_urls": {"pricecharting_url": link} if link else {}}

    def test_linked_page_is_used_over_tcggo(self):
        values = build_market_values(self.snapshots(), self.card(self.LINK + "?q=hoothoot"))
        self.assertEqual(values["PSA_10"]["estimate"], 410.0)
        self.assertEqual(values["PSA_10"]["method"], "pricecharting_guide")

    def test_prices_from_a_removed_or_changed_link_are_ignored(self):
        for link in (None, "https://www.pricecharting.com/game/pokemon-x/other-1"):
            values = build_market_values(self.snapshots(), self.card(link))
            self.assertEqual(values["PSA_10"]["estimate"], 400.0)  # back to TCGGO

    def test_small_hits_are_left_out(self):
        items = [{"id": "a", "card": {"id": "c1", "name": "c1"}, "ownership_grade": "RAW",
                  "quantity": 1, "small_hit": True, "market_values": {}}]
        self.assertEqual(grading.inputs_from_collection(items), [])


class GradingDataRefresh(unittest.IsolatedAsyncioTestCase):
    async def test_reads_each_linked_raw_card_once(self):
        link = "https://www.pricecharting.com/game/pokemon-astral-radiance/hoothoot-tg12"
        items = [
            {"id": "i1", "card_id": "linked", "ownership_grade": "RAW"},
            {"id": "i2", "card_id": "linked", "ownership_grade": "RAW"},
            {"id": "i3", "card_id": "unlinked", "ownership_grade": "RAW"},
            {"id": "i4", "card_id": "small", "ownership_grade": "RAW", "small_hit": True},
            {"id": "i5", "card_id": "graded", "ownership_grade": "PSA_10"},
        ]
        cards = {
            "linked": {"id": "linked", "marketplace_urls": {"pricecharting_url": link + "?q=x"}},
            "unlinked": {"id": "unlinked", "marketplace_urls": {}},
            "small": {"id": "small", "marketplace_urls": {"pricecharting_url": link}},
            "graded": {"id": "graded", "marketplace_urls": {"pricecharting_url": link}},
        }
        fetched, inserted = [], []

        async def fake_prices(url):
            fetched.append(url)
            return {"PSA_8": 150.0, "PSA_9": 164.03, "PSA_10": 400.0}

        with mock.patch.object(price_sync.db, "get_collection_items", lambda: items), \
             mock.patch.object(price_sync.db, "get_cards", lambda ids: {i: cards[i] for i in ids}), \
             mock.patch.object(price_sync.db, "insert_price_snapshots", lambda rows: inserted.extend(rows)), \
             mock.patch.object(pricecharting, "get_prices", fake_prices):
            record = price_sync._new_grading_record()
            await price_sync._pull_grading_data(record)

        self.assertEqual(fetched, [link])
        self.assertEqual(sorted(row["grade"] for row in inserted), ["PSA_10", "PSA_9"])
        self.assertEqual({row["card_id"] for row in inserted}, {"linked"})
        self.assertEqual((record["updated"], record["no_link"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
