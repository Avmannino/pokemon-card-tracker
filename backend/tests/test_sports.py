"""The Sports section: CardSight price summaries, search, call budget,
valuation, grading and the /api/sports endpoints (no network, no database).

Run from backend/:  python -m unittest discover -t . -s tests -v
"""

import asyncio
import itertools
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from fastapi.testclient import TestClient

from app import db, main, sports_api
from app.services import cardapi, cardsight, sports, state_store
from tests.test_pricing import FakeState


BO = "6927b8b8-2f70-4f87-b273-e4982dc62ee6"
GRIFFEY = "380da6da-7b74-4e31-8418-f23e07653cc3"


def sale(price, day, title="1991 Stadium Club Bo Jackson #224", image=None):
    return {
        "title": title,
        "price": price,
        "date": f"2026-11-{day:02d}T12:00:00Z",
        "source": "ebay",
        "listing_type": "auction",
        "url": f"https://example.test/{day}",
        "image_url": image,
    }


def pricing_data(raw=(), psa10=(), psa9=(), bgs95=()):
    graded = []
    psa = [
        {"grade_value": value, "grade_id": value, "count": len(records), "records": list(records)}
        for value, records in (("10", psa10), ("9", psa9))
        if records
    ]
    if psa:
        graded.append({"company_name": "PSA", "company_id": "psa", "grades": psa})
    if bgs95:
        graded.append(
            {
                "company_name": "BGS",
                "company_id": "bgs",
                "grades": [{"grade_value": "9.5", "count": len(bgs95), "records": list(bgs95)}],
            }
        )
    return {
        "card": {
            "card_id": BO,
            "name": "Bo Jackson",
            "number": "224",
            "set": {"set_id": "s", "name": "Base Set", "year": "1991", "release": "Stadium Club"},
        },
        "raw": {"count": len(raw), "records": list(raw)},
        "graded": graded,
        "meta": {"last_sale_date": "2026-11-18T01:12:30Z"},
    }


class SummarizeTests(unittest.TestCase):
    def test_values_are_medians_of_recent_single_card_sales(self):
        raw = [sale(1.0 + day / 10, day, image=f"https://i.ebayimg.com/{day}.jpg") for day in range(1, 15)]
        raw.append(sale(40.0, 28, title="1991 Stadium Club lot of 25 Bo Jackson"))
        summary = cardsight.summarize(
            pricing_data(raw=raw, psa10=[sale(230, 3), sale(240, 9)], bgs95=[sale(90, 2)])
        )

        # The lot is ignored; the 10 newest (days 5-14) give a median of 1.95.
        self.assertEqual(summary["grades"]["RAW"]["value"], 1.95)
        self.assertEqual(summary["grades"]["RAW"]["sales"], 14)
        self.assertEqual(summary["grades"]["RAW"]["used"], 10)
        self.assertEqual(summary["grades"]["RAW"]["last_sale"], "2026-11-14T12:00:00Z")
        self.assertEqual(summary["grades"]["PSA_10"]["value"], 235)
        self.assertEqual(summary["grades"]["BGS_9.5"]["value"], 90)
        self.assertNotIn("PSA_9", summary["grades"])
        self.assertEqual(summary["photo_url"], "https://i.ebayimg.com/14.jpg")
        self.assertEqual(summary["card"]["release_name"], "Stadium Club")

    def test_grade_keys(self):
        self.assertEqual(cardsight.grade_key("PSA", "10"), "PSA_10")
        self.assertEqual(cardsight.grade_key("psa", "9.0"), "PSA_9")
        self.assertEqual(cardsight.grade_key("BGS", "9.5"), "BGS_9.5")

    def test_league_from_team_attribute(self):
        self.assertEqual(cardsight.league(["RC", "MLB-KCR"]), "MLB")
        self.assertEqual(cardsight.league(["NFL-LV"]), "NFL")
        self.assertIsNone(cardsight.league(["RC"]))


def catalog_card(card_id, name, year="1991", release="Stadium Club", number="270", set_name="Base Set"):
    return {
        "id": card_id, "name": name, "number": number, "releaseYear": year,
        "releaseName": release, "setName": set_name, "attributes": ["MLB-SEA"],
    }


class SearchTests(unittest.TestCase):
    def test_retries_by_surname_when_the_full_name_finds_nothing(self):
        calls = []

        async def fake_catalog(params):
            calls.append(params)
            if params["name"] == "Griffey":
                return [
                    catalog_card(GRIFFEY, "Ken Griffey, Jr."),
                    catalog_card("x" * 36, "Craig Griffey"),
                ]
            return []

        with mock.patch.object(cardsight, "_catalog", fake_catalog):
            results = asyncio.run(cardsight.search_cards("Ken Griffey Jr.", "1991", "#270"))

        self.assertEqual([call["name"] for call in calls], ["Ken Griffey Jr.", "Griffey"])
        self.assertEqual(calls[0]["number"], "270")
        self.assertEqual([card["cardsight_id"] for card in results], [GRIFFEY])
        self.assertEqual(results[0]["league"], "MLB")

    def test_set_words_rank_matching_releases_first(self):
        async def fake_catalog(params):
            return [
                catalog_card("a" * 36, "Bo Jackson", release="Topps", number="170"),
                catalog_card("b" * 36, "Bo Jackson", release="Stadium Club", number="224"),
            ]

        with mock.patch.object(cardsight, "_catalog", fake_catalog):
            results = asyncio.run(cardsight.search_cards("Bo Jackson", set_query="stadium club"))

        self.assertEqual(results[0]["cardsight_id"], "b" * 36)


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.state = FakeState()
        for patcher in (
            mock.patch.object(state_store, "get", self.state.get),
            mock.patch.object(state_store, "set", self.state.set),
            mock.patch.object(cardsight.settings, "cardsight_api_key", "test-key"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_counts_by_month_and_stops_near_the_limit(self):
        self.state.set(cardsight.USAGE_KEY, {"month": "2020-01", "calls": 700})
        self.assertEqual(cardsight.usage()["calls"], 0)

        cardsight._count_call()
        self.assertEqual(cardsight.usage()["calls"], 1)

        self.state.set(cardsight.USAGE_KEY, {"month": cardsight._month(), "calls": cardsight.STOP_AT})
        with self.assertRaises(cardsight.CardSightUnavailable):
            cardsight._check_budget()

    def test_no_key_means_unavailable(self):
        with mock.patch.object(cardsight.settings, "cardsight_api_key", ""):
            with self.assertRaises(cardsight.CardSightUnavailable):
                cardsight._check_budget()


def priced_card(card_id="card-1", raw=3.0, psa9=96.0, psa10=946.0, sales=10):
    grades = {}
    for key, value in (("RAW", raw), ("PSA_9", psa9), ("PSA_10", psa10)):
        if value is not None:
            grades[key] = {"value": value, "sales": sales, "used": min(sales, 10), "last_sale": "2026-11-20T00:00:00Z"}
    return {
        "id": card_id, "cardsight_id": BO, "name": "Randy Johnson", "number": "287",
        "year": "1989", "release_name": "Topps Big", "set_name": "Base Set",
        "attributes": ["MLB-MON"],
        "prices": {"grades": grades, "photo_url": "https://i.ebayimg.com/rj.jpg"},
        "priced_at": "2026-11-28T09:17:00+00:00",
    }


class ValuationTests(unittest.TestCase):
    def test_owned_grade_value_and_manual_override(self):
        card = priced_card()
        items = [
            {"id": "i1", "sports_card_id": "card-1", "quantity": 2, "ownership_grade": "RAW", "manual_value": None},
            {"id": "i2", "sports_card_id": "card-1", "quantity": 1, "ownership_grade": "PSA_9", "manual_value": None},
            {"id": "i3", "sports_card_id": "card-1", "quantity": 1, "ownership_grade": "PSA_8", "manual_value": "45.00"},
            {"id": "i4", "sports_card_id": "card-1", "quantity": 1, "ownership_grade": "PSA_7", "manual_value": None},
        ]
        result = sports.build_collection(items, {"card-1": card}, grading_cost=30)
        rows = {row["id"]: row for row in result["items"]}

        self.assertEqual(rows["i1"]["value_total"], 6.0)
        self.assertEqual(rows["i2"]["value_each"], 96.0)
        self.assertEqual((rows["i3"]["value_each"], rows["i3"]["value_source"]), (45.0, "manual"))
        self.assertIsNone(rows["i4"]["value_total"])
        self.assertEqual(result["summary"]["total_value"], 147.0)
        self.assertEqual(result["summary"]["missing_values"], 1)
        self.assertEqual(result["summary"]["card_count"], 5)

        # Only raw cards get a grading outlook.
        self.assertTrue(rows["i1"]["grading"]["available"])
        self.assertEqual(rows["i1"]["grading"]["psa10_net"], 913.0)
        self.assertIsNone(rows["i2"]["grading"])

    def test_missing_graded_sales_explained(self):
        outlook = sports.grading_outlook(priced_card(psa10=None), 30)
        self.assertFalse(outlook["available"])
        self.assertEqual(outlook["unavailable_reason"], "No recent PSA 10 sales.")

        outlook = sports.grading_outlook(priced_card(raw=None), 30, raw_override=5.0)
        self.assertTrue(outlook["available"])
        self.assertEqual(outlook["raw_value"], 5.0)


class FakeSportsDB:
    def __init__(self):
        self.ids = itertools.count(1)
        self.cards = {}
        self.items = {}
        self.history = []

    def save_sports_card(self, card, prices=None):
        payload = db._sports_card_payload(card, prices)
        for row in self.cards.values():
            if row["cardsight_id"] == card["cardsight_id"]:
                row.update(payload)
                return row
        row = {"id": f"sc-{next(self.ids)}", "prices": None, "priced_at": None, **payload}
        self.cards[row["id"]] = row
        return row

    def save_sports_card_prices(self, updates):
        for card, prices in updates:
            self.save_sports_card(card, prices)

    def get_sports_cards(self, card_ids=None):
        return {k: v for k, v in self.cards.items() if card_ids is None or k in set(card_ids)}

    def get_sports_cards_by_cardsight_ids(self, ids):
        return {row["cardsight_id"]: row for row in self.cards.values() if row["cardsight_id"] in set(ids)}

    def delete_sports_card(self, card_id):
        self.cards.pop(card_id, None)

    def get_sports_items(self):
        return list(self.items.values())

    def get_sports_item(self, item_id):
        return self.items.get(item_id)

    def add_sports_item(self, sports_card_id, quantity, ownership_grade, notes=None, manual_value=None, manual_source_url=None):
        item = {
            "id": f"si-{next(self.ids)}", "sports_card_id": sports_card_id, "quantity": quantity,
            "ownership_grade": ownership_grade, "notes": notes, "manual_value": manual_value,
            "manual_source_url": manual_source_url, "created_at": "2026-11-28T00:00:00+00:00",
        }
        self.items[item["id"]] = item
        return item

    def update_sports_item(self, item_id, fields):
        self.items[item_id].update(fields)
        return self.items[item_id]

    def delete_sports_item(self, item_id):
        self.items.pop(item_id, None)

    def sports_card_in_use(self, card_id):
        return any(item["sports_card_id"] == card_id for item in self.items.values())

    def update_sports_card_details(self, card_id, details):
        self.cards[card_id].update(details)

    def save_sports_sales_prices(self, results):
        for card_id, summary in results.items():
            self.cards[card_id]["sales_prices"] = summary
            self.cards[card_id]["sales_priced_at"] = summary.get("priced_at")

    def get_last_sports_value(self):
        return self.history[-1] if self.history else None

    def insert_sports_value(self, total_value, flow, kind):
        self.history.append(
            {"recorded_at": datetime.now(timezone.utc).isoformat(), "total_value": round(total_value, 2),
             "flow": round(flow, 2), "kind": kind}
        )

    def get_sports_value_history(self):
        return list(self.history)


BO_REF = {
    "cardsight_id": BO, "name": "Bo Jackson", "number": "224", "year": "1991",
    "release_name": "Stadium Club", "set_name": "Base Set", "attributes": ["MLB-KCR"],
}


class SportsApiTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeSportsDB()
        self.state = FakeState()
        self.pricing_calls = []

        async def fake_get_prices(ids):
            self.pricing_calls.append(list(ids))
            return {
                cardsight_id: cardsight.summarize(
                    pricing_data(raw=[sale(1.29, 18)], psa9=[sale(22.75, 10)], psa10=[sale(233.25, 12)])
                )
                for cardsight_id in ids
                if cardsight_id == BO
            }

        # The Card API: what each card's title search finds (by card name).
        self.sales_found = {}
        self.sales_lookups = []

        async def fake_price_cards(cards):
            self.sales_lookups.append([card["name"] for card in cards])
            return {
                card["id"]: cardapi.summarize(self.sales_found.get(card["name"], []), card)
                for card in cards
            }, None

        patches = [
            mock.patch.object(state_store, "get", self.state.get),
            mock.patch.object(state_store, "set", self.state.set),
            mock.patch.object(cardsight, "get_prices", fake_get_prices),
            mock.patch.object(cardsight.settings, "cardsight_api_key", "test-key"),
            mock.patch.object(cardapi.settings, "cardapi_key", "test-key"),
            mock.patch.object(cardapi, "price_cards", fake_price_cards),
        ]
        for name in (
            "save_sports_card", "save_sports_card_prices", "get_sports_cards",
            "get_sports_cards_by_cardsight_ids", "delete_sports_card",
            "get_sports_items", "get_sports_item", "add_sports_item", "update_sports_item",
            "delete_sports_item", "sports_card_in_use", "get_last_sports_value",
            "insert_sports_value", "get_sports_value_history", "update_sports_card_details",
            "save_sports_sales_prices",
        ):
            patches.append(mock.patch.object(db, name, getattr(self.fake, name)))
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

        sports_api._price_cache.clear()
        sports_api._search_cache.clear()
        sports._refreshing = False
        self.client = TestClient(main.app)

    def test_check_then_add_uses_one_pricing_call(self):
        response = self.client.post("/api/sports/prices", json={"cardsight_ids": [BO, GRIFFEY]})
        self.assertEqual(response.status_code, 200)
        prices = response.json()["prices"]
        self.assertEqual(prices[BO]["grades"]["PSA_10"]["value"], 233.25)
        self.assertTrue(prices[BO]["grading"]["available"])
        self.assertEqual(prices[GRIFFEY]["grades"], {})

        response = self.client.post("/api/sports/items", json={"card": BO_REF, "quantity": 2})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.pricing_calls, [[BO, GRIFFEY]])

        collection = self.client.get("/api/sports/collection").json()
        self.assertEqual(collection["summary"]["total_value"], 2.58)
        self.assertEqual(collection["items"][0]["card"]["league"], "MLB")
        self.assertEqual(self.pricing_calls, [[BO, GRIFFEY]])

    def test_adding_the_same_card_and_grade_adds_to_quantity(self):
        self.client.post("/api/sports/items", json={"card": BO_REF})
        response = self.client.post("/api/sports/items", json={"card": BO_REF, "quantity": 2})
        self.assertTrue(response.json()["merged"])
        self.assertEqual([item["quantity"] for item in self.fake.items.values()], [3])

        self.client.post("/api/sports/items", json={"card": BO_REF, "ownership_grade": "PSA_9"})
        self.assertEqual(len(self.fake.items), 2)

    def test_manual_value_and_removal(self):
        item = self.client.post("/api/sports/items", json={"card": BO_REF}).json()["item"]

        response = self.client.patch(
            f"/api/sports/items/{item['id']}",
            json={"manual_value": 12.5, "manual_source_url": " https://example.test/comp "},
        )
        self.assertEqual(response.json()["item"]["manual_source_url"], "https://example.test/comp")
        collection = self.client.get("/api/sports/collection").json()
        self.assertEqual(collection["items"][0]["value_source"], "manual")

        response = self.client.patch(f"/api/sports/items/{item['id']}", json={"manual_value": None})
        self.assertIsNone(response.json()["item"]["manual_source_url"])

        self.assertEqual(self.client.delete(f"/api/sports/items/{item['id']}").status_code, 200)
        self.assertEqual(self.fake.cards, {})

    def test_refresh_reprices_owned_cards_with_a_cooldown_for_the_button(self):
        self.client.post("/api/sports/items", json={"card": BO_REF})
        self.pricing_calls.clear()

        response = self.client.post("/api/sports/refresh")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["priced"], 1)
        self.assertEqual(self.pricing_calls, [[BO]])

        self.assertEqual(self.client.post("/api/sports/refresh").status_code, 429)
        self.assertEqual(self.client.post("/api/sports/refresh?reason=scheduled").status_code, 200)

    def test_refresh_lists_what_changed(self):
        raw = self.client.post("/api/sports/items", json={"card": BO_REF, "quantity": 2}).json()["item"]
        self.client.post("/api/sports/items", json={"card": BO_REF, "ownership_grade": "PSA_9"})
        manual = self.client.post(
            "/api/sports/items", json={"card": BO_REF, "ownership_grade": "PSA_8", "manual_value": 40}
        ).json()["item"]
        # Raw was $1.29 and PSA 9 $22.75 when added; now $1.50 and $20.
        summary = cardsight.summarize(
            pricing_data(raw=[sale(1.50, 20)], psa9=[sale(20.0, 21)], psa10=[sale(233.25, 12)])
        )

        async def new_prices(ids):
            return {BO: summary}

        with mock.patch.object(cardsight, "get_prices", new_prices):
            record = self.client.post("/api/sports/refresh").json()

        changes = {change["grade"]: change for change in record["changes"]}
        self.assertEqual(set(changes), {"RAW", "PSA_9"})
        self.assertEqual(record["changes"][0]["grade"], "PSA_9")
        self.assertEqual(changes["RAW"]["change"], 0.42)
        self.assertEqual(changes["RAW"]["item_id"], raw["id"])
        self.assertEqual(changes["PSA_9"]["change"], -2.75)
        self.assertEqual(record["net_change"], -2.33)
        self.assertNotIn(manual["id"], [change["item_id"] for change in record["changes"]])

        collection = self.client.get("/api/sports/collection").json()
        self.assertEqual(collection["last_refresh"]["net_change"], -2.33)

    def test_missing_tables_and_key_are_explained(self):
        def missing():
            raise db.SportsTablesMissing("Run supabase/migrations/2026-11-28_sports_cards.sql in Supabase first.")

        with mock.patch.object(db, "get_sports_items", missing):
            response = self.client.get("/api/sports/collection")
        self.assertEqual(response.status_code, 503)
        self.assertIn("2026-11-28_sports_cards.sql", response.json()["detail"])

        with mock.patch.object(cardsight.settings, "cardsight_api_key", ""):
            response = self.client.get("/api/sports/search?name=Bo%20Jackson")
        self.assertEqual(response.status_code, 503)

    def test_sports_writes_refresh_the_dashboard_but_price_checks_dont(self):
        with mock.patch.object(main.cache, "invalidate") as invalidate:
            self.client.post("/api/sports/prices", json={"cardsight_ids": [BO]})
            invalidate.assert_not_called()
            self.client.post("/api/sports/items", json={"card": BO_REF})
        invalidate.assert_called()

    def test_every_change_records_the_sports_total(self):
        # Added at $1.29 x 2: all of it is a flow (a deposit).
        item = self.client.post("/api/sports/items", json={"card": BO_REF, "quantity": 2}).json()["item"]
        self.assertEqual(self.fake.history[-1], {**self.fake.history[-1], "total_value": 2.58, "flow": 2.58, "kind": "add"})

        # One more copy: another deposit.
        self.client.patch(f"/api/sports/items/{item['id']}", json={"quantity": 3})
        self.assertEqual((self.fake.history[-1]["flow"], self.fake.history[-1]["kind"]), (1.29, "quantity"))

        # The refresh's price move is market movement, not a flow.
        summary = cardsight.summarize(pricing_data(raw=[sale(1.50, 20)]))

        async def new_prices(ids):
            return {BO: summary}

        with mock.patch.object(cardsight, "get_prices", new_prices):
            self.client.post("/api/sports/refresh")
        self.assertEqual(self.fake.history[-1], {**self.fake.history[-1], "total_value": 4.5, "flow": 0.0, "kind": "refresh"})

        self.client.delete(f"/api/sports/items/{item['id']}")
        self.assertEqual((self.fake.history[-1]["total_value"], self.fake.history[-1]["flow"]), (0.0, -4.5))


    def test_cards_added_by_hand_use_your_value_and_never_go_to_cardsight(self):
        self.client.post("/api/sports/items", json={"card": BO_REF})
        response = self.client.post(
            "/api/sports/manual-items",
            json={
                "name": "Derek Jeter", "year": "2003", "release_name": "Upper Deck Yankees 100th Anniversary",
                "number": "#12", "league": "MLB", "quantity": 2, "manual_value": 4.5,
                "manual_source_url": "https://www.sportscardspro.com/game/x",
            },
        )
        self.assertEqual(response.status_code, 200)
        item = response.json()["item"]

        collection = self.client.get("/api/sports/collection").json()
        row = next(row for row in collection["items"] if row["id"] == item["id"])
        self.assertTrue(row["card"]["manual"])
        self.assertEqual((row["card"]["number"], row["card"]["league"]), ("12", "MLB"))
        self.assertEqual((row["value_total"], row["value_source"]), (9.0, "manual"))
        self.assertFalse(row["grading"]["available"])
        self.assertEqual(self.fake.history[-1]["flow"], 9.0)

        self.pricing_calls.clear()
        self.client.post("/api/sports/refresh")
        self.assertEqual(self.pricing_calls, [[BO]])

        # Its details can be fixed (and are looked up again).
        response = self.client.patch(
            f"/api/sports/items/{item['id']}",
            json={"card": {"name": "Derek Jeter", "year": "2003", "release_name": "Upper Deck Yankees 100th Anniversary", "number": "13"}},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.fake.cards[item["sports_card_id"]]["number"], "13")
        self.assertIn(["Derek Jeter"], self.sales_lookups)

        bo = next(row for row in self.fake.items.values() if row["id"] != item["id"])
        self.assertEqual(
            self.client.patch(f"/api/sports/items/{bo['id']}", json={"card": {"name": "Bo"}}).status_code, 400
        )


    def test_hand_added_card_without_a_value_is_valued_from_recent_sales(self):
        self.sales_found["Frank Thomas"] = [
            card_api_sale(9.99, 14, "1990-91 Topps Major League Debut 1990 - Frank Thomas #153 Rookie RC"),
            card_api_sale(12.0, 20, "Frank Thomas 1991 Topps Major League Debut #153"),
            card_api_sale(8.0, 21, "Frank Thomas 1990 Topps Debut #153", confirmed=False),
        ]
        response = self.client.post(
            "/api/sports/manual-items",
            json={"name": "Frank Thomas", "year": "1991", "release_name": "'90 Major League Debut", "number": "153"},
        )
        self.assertTrue(response.json()["sales_found"])

        row = self.client.get("/api/sports/collection").json()["items"][0]
        self.assertEqual((row["value_each"], row["value_source"]), (11.0, "cardapi"))
        self.assertEqual(row["prices"]["RAW"]["sales"], 2)

    def test_thin_cardsight_cards_use_whichever_source_has_more_sales(self):
        # CardSight has 1 raw sale for Bo ($1.29); the title search finds 3.
        self.sales_found["Bo Jackson"] = [
            card_api_sale(price, day, "1991 Stadium Club Bo Jackson #224 Royals")
            for price, day in ((2.0, 10), (2.5, 12), (3.0, 14))
        ]
        self.client.post("/api/sports/items", json={"card": BO_REF})
        self.assertEqual(self.sales_lookups, [["Bo Jackson"]])

        row = self.client.get("/api/sports/collection").json()["items"][0]
        self.assertEqual((row["value_each"], row["value_source"]), (2.5, "cardapi"))
        # PSA 10 still from CardSight, the only source with sales for it.
        self.assertEqual(row["prices"]["PSA_10"]["source"], "cardsight")

        # A refresh finding a lower price is a market move.
        self.sales_found["Bo Jackson"] = [
            card_api_sale(price, day, "1991 Stadium Club Bo Jackson #224 Royals")
            for price, day in ((1.75, 20), (2.0, 21), (2.25, 22))
        ]
        record = self.client.post("/api/sports/refresh").json()
        self.assertEqual(record["sales_checked"], 1)
        self.assertEqual(record["changes"][0]["change"], -0.5)
        self.assertEqual(self.fake.history[-1]["flow"], 0.0)


def card_api_sale(price, day, title, confirmed=True, grader=None, grade=None, number=None, year=None):
    return {
        "title": title, "price": price, "price_confirmed": confirmed,
        "sale_date": f"2026-11-{day:02d}", "sold_at": f"2026-11-{day:02d}T12:00:00Z",
        "listing_type": "auction", "grader": grader, "grade": grade,
        "card_number": number, "year": year,
    }


class CardApiTests(unittest.TestCase):
    THOMAS = {"id": "c1", "name": "Frank Thomas", "number": "153", "year": "1991",
              "release_name": "'90 Major League Debut", "set_name": None}

    def test_queries(self):
        self.assertEqual(cardapi.query_for(self.THOMAS), "thomas major league debut 153")
        griffey = {"name": "Ken Griffey, Jr. MC", "number": "603", "release_name": "Stadium Club", "set_name": "Base Set"}
        self.assertEqual(cardapi.query_for(griffey), "griffey stadium club 603")
        nolan = {"name": "Nolan Ryan", "number": "605", "release_name": "Topps Stadium Club", "set_name": "Members Choice"}
        self.assertEqual(cardapi.query_for(nolan), "ryan members choice 605")

    def test_only_this_cards_confirmed_single_sales_count_and_grades_are_split(self):
        records = [
            card_api_sale(10.0, 20, "1990-91 Topps Major League Debut Frank Thomas #153 RC"),
            card_api_sale(12.0, 21, "Frank Thomas Major League Debut #153", number="153", year="1990"),
            card_api_sale(99.0, 22, "Frank Thomas Major League Debut #153", confirmed=False),
            card_api_sale(5.0, 22, "Frank Thomas Major League Debut #15"),
            card_api_sale(5.0, 22, "Major League Debut lot of 10 Frank Thomas #153"),
            card_api_sale(5.0, 22, "Frank Thomas Major League Debut #153 reprint"),
            card_api_sale(5.0, 22, "Frank Thomas Major League Debut", number="154"),
            card_api_sale(5.0, 22, "Frank Thomas 1984 Debut #153", number="153", year="1984"),
            card_api_sale(80.0, 23, "Frank Thomas Major League Debut #153", grader="PSA", grade="9"),
            card_api_sale(150.0, 24, "Frank Thomas Major League Debut #153 PSA 10 GEM MINT"),
            card_api_sale(60.0, 24, "Frank Thomas Major League Debut #153 BGS 9.5"),
        ]
        grades = cardapi.summarize(records, self.THOMAS)["grades"]
        self.assertEqual(grades["RAW"], {**grades["RAW"], "value": 11.0, "sales": 2})
        self.assertEqual(grades["PSA_9"]["value"], 80.0)
        self.assertEqual(grades["PSA_10"]["value"], 150.0)
        self.assertEqual(grades["BGS_9.5"]["value"], 60.0)
        self.assertNotIn("recent", grades["RAW"])

    def test_merged_grades_prefer_more_sales_and_cardsight_on_ties(self):
        card = {
            "prices": {"grades": {"RAW": {"value": 0.45, "sales": 1}, "PSA_9": {"value": 10.5, "sales": 3}}},
            "sales_prices": {"grades": {"RAW": {"value": 6.5, "sales": 19}, "PSA_9": {"value": 139.25, "sales": 3}}},
        }
        merged = sports._grades(card)
        self.assertEqual((merged["RAW"]["value"], merged["RAW"]["source"]), (6.5, "cardapi"))
        self.assertEqual((merged["PSA_9"]["value"], merged["PSA_9"]["source"]), (10.5, "cardsight"))


class SportsInPortfolioTests(unittest.TestCase):
    def test_sports_total_counts_in_value_chart_and_attribution(self):
        start = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
        rows = [
            {"recorded_at": start.isoformat(), "total_value": 100, "flow": 100, "kind": "add"},
            # Refresh: +20 market and a $10 card priced for the first time.
            {"recorded_at": (start + timedelta(days=1)).isoformat(), "total_value": 130, "flow": 10, "kind": "refresh"},
            {"recorded_at": (start + timedelta(days=2)).isoformat(), "total_value": 160, "flow": 30, "kind": "add"},
        ]
        result = main.build_dashboard([], [], {}, {}, now=start + timedelta(days=3), sports_history=rows)

        self.assertEqual(result["current_total_value"], 160)
        everything = result["ranges"]["ALL"]
        self.assertEqual(everything["change"], 20)
        self.assertEqual(everything["change_pct"], 20.0)
        attribution = everything["attribution"]
        self.assertEqual(
            (attribution["market_change"], attribution["acquisitions"], attribution["total_change"]),
            (20, 40, 60),
        )
        self.assertEqual(attribution["contributors"][0]["kind"], "sports")
        self.assertEqual(attribution["contributors"][0]["market_change"], 20)

    def test_history_is_brought_up_to_the_current_total_as_a_flow(self):
        now = datetime(2026, 11, 29, tzinfo=timezone.utc)
        self.assertEqual(sports.history_rows([], 0.0, now), [])
        rows = sports.history_rows([{"recorded_at": "2026-11-28T00:00:00+00:00", "total_value": 50, "flow": 50}], 75.0, now)
        self.assertEqual((rows[-1]["total_value"], rows[-1]["flow"]), (75.0, 25.0))


if __name__ == "__main__":
    unittest.main()
