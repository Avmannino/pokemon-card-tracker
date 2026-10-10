"""API endpoints against an in-memory database and fake pricing APIs.

Run from backend/:  python -m unittest discover -t . -s tests -v
"""

import itertools
import time
import unittest
from datetime import datetime, timezone
from unittest import mock

from fastapi.testclient import TestClient

from app import db, main
from app.services import poketrace, price_sync, state_store, tcggo
from tests.test_pricing import FakeState, remote_card


class FakeDB:
    """Just enough of app.db, in memory."""

    def __init__(self):
        self.ids = itertools.count(1)
        self.cards = {}
        self.items = {}
        self.snapshots = []
        self.events = []
        self.confirmed = set()

    def _now(self):
        return datetime.now(timezone.utc).isoformat()

    def get_card(self, card_id):
        return self.cards.get(card_id)

    def save_card(self, card):
        for row in self.cards.values():
            if row["poketrace_id"] == card["poketrace_id"]:
                row.update({k: v for k, v in card.items() if k in row})
                return row
        row = {
            "id": f"card-{next(self.ids)}", "variant_siblings": None,
            **{k: card.get(k) for k in (
                "poketrace_id", "name", "card_number", "set_name", "set_slug",
                "variant", "rarity", "image_url", "tcgplayer_id",
            )},
            "marketplace_urls": card.get("marketplace_urls") or {},
        }
        self.cards[row["id"]] = row
        return row

    def add_collection_item(self, card_id, quantity, ownership_grade, purchase_price, notes):
        item = {
            "id": f"item-{next(self.ids)}", "card_id": card_id, "quantity": quantity,
            "ownership_grade": ownership_grade, "purchase_price": purchase_price,
            "notes": notes, "created_at": self._now(), "variant_confirmed_at": None,
        }
        self.items[item["id"]] = item
        return item

    def get_collection_item(self, item_id):
        return self.items.get(item_id)

    def get_collection_items(self):
        return list(self.items.values())

    def delete_collection_item(self, item_id):
        self.items.pop(item_id, None)

    def insert_price_snapshot(self, card_id, grade, source, value, source_url=None, observed_at=None, metadata=None):
        row = {
            "id": next(self.ids), "card_id": card_id, "grade": grade, "source": source,
            "value": value, "source_url": source_url, "observed_at": observed_at or self._now(),
            "metadata": metadata or {},
        }
        self.snapshots.append(row)
        return row

    def get_price_snapshots(self, card_id, limit=500):
        rows = [r for r in self.snapshots if r["card_id"] == card_id]
        return sorted(rows, key=lambda r: (r["observed_at"], r["id"]), reverse=True)

    def get_collection_events(self, collection_item_id=None):
        return [e for e in self.events if collection_item_id in (None, e["collection_item_id"])]

    def insert_collection_event(self, **event):
        row = {"id": next(self.ids), **event}
        self.events.append(row)
        return row

    def delete_collection_event(self, event_id):
        self.events = [e for e in self.events if e["id"] != event_id]

    def update_card_variant_siblings(self, card_id, siblings):
        self.cards[card_id]["variant_siblings"] = siblings
        return True

    def set_item_variant_confirmed(self, item_id, confirmed=True):
        self.items[item_id]["variant_confirmed_at"] = self._now()
        return True


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDB()
        self.state = FakeState()
        self.api_calls = []

        async def no_network(*args, **kwargs):
            self.api_calls.append(args)
            raise AssertionError("unexpected pricing API call")

        patches = [
            mock.patch.object(state_store, "get", self.state.get),
            mock.patch.object(state_store, "set", self.state.set),
            mock.patch.object(poketrace, "_request", no_network),
            mock.patch.object(tcggo, "_get", no_network),
        ]
        for name in (
            "get_card", "save_card", "add_collection_item", "get_collection_item",
            "get_collection_items", "delete_collection_item", "insert_price_snapshot",
            "get_price_snapshots", "get_collection_events", "insert_collection_event",
            "delete_collection_event", "update_card_variant_siblings",
            "set_item_variant_confirmed",
        ):
            patches.append(mock.patch.object(db, name, getattr(self.fake, name)))

        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

        self.client = TestClient(main.app)

    def own_reverse_holo_gengar(self, grade="RAW"):
        """A Gengar saved as Reverse Holo, with its RH prices and a manual
        PSA 9 price, plus its ADD event."""
        card = self.fake.save_card({
            "poketrace_id": "rh-variant-id", "name": "Gengar", "card_number": "094/165",
            "set_slug": "sv-scarlet-and-violet-151", "set_name": "151",
            "variant": "Reverse_Holofoil", "tcgplayer_id": "516663",
        })
        card["variant_siblings"] = {"variants": [
            {"poketrace_id": "holo-variant-id", "variant": "Holofoil"},
            {"poketrace_id": "rh-variant-id", "variant": "Reverse_Holofoil"},
        ]}
        price_sync.store_raw_prices(card, remote_card("rh-variant-id", "Reverse_Holofoil", tcg=8.52))
        self.fake.insert_price_snapshot(
            card["id"], "PSA_9", "PSA CardFacts — Average Price", 21.71,
            source_url="https://psa.example/lugia", observed_at="2026-10-01T05:00:00+00:00",
            metadata={"entry_method": "manual"},
        )
        item = self.fake.add_collection_item(card["id"], 2, grade, 3.0, "binder")
        self.fake.insert_collection_event(
            collection_item_id=item["id"], card_id=card["id"], grade=grade,
            event_type="ADD", quantity=2, occurred_at=item["created_at"],
            market_value_per_card=8.52, recorded_by="app",
        )
        return card, item


class VariantEndpoint(ApiTestCase):
    def test_confirming_the_saved_variant(self):
        card, item = self.own_reverse_holo_gengar()
        response = self.client.post(f"/api/collection/{item['id']}/variant", json={"poketrace_id": "rh-variant-id"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["changed"])
        self.assertIsNotNone(self.fake.items[item["id"]]["variant_confirmed_at"])
        self.assertEqual(self.api_calls, [])

    def test_correcting_to_the_holo_swaps_lots_without_a_gain(self):
        card, item = self.own_reverse_holo_gengar(grade="PSA_9")

        async def fetch(poketrace_id, reserve=2):
            return remote_card("holo-variant-id", "Holofoil", tcg=2.64)

        with mock.patch.object(poketrace, "get_card", fetch):
            response = self.client.post(f"/api/collection/{item['id']}/variant", json={"poketrace_id": "holo-variant-id"})

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        new_item = body["item"]

        self.assertTrue(body["changed"])
        self.assertEqual(new_item["card"]["variant"], "Holofoil")
        self.assertEqual(new_item["quantity"], 2)
        self.assertEqual(new_item["purchase_price"], 3.0)
        self.assertNotIn(item["id"], self.fake.items)
        self.assertIsNotNone(self.fake.items[new_item["id"]]["variant_confirmed_at"])

        # The manual PSA 9 price came along (with provenance); RH raw prices didn't.
        holo_id = new_item["card"]["id"]
        self.assertEqual(new_item["market_values"]["PSA_9"]["estimate"], 21.71)
        self.assertEqual(new_item["market_values"]["RAW"]["estimate"], 2.64)
        carried = [s for s in self.fake.snapshots if s["card_id"] == holo_id and s["grade"] == "PSA_9"]
        self.assertEqual(carried[0]["metadata"]["carried_from_card_id"], card["id"])
        # Old snapshots stay on the old card, untouched.
        self.assertTrue(any(s["card_id"] == card["id"] and s["value"] == 8.52 for s in self.fake.snapshots))

        # Booked like a trade: the old lot leaves at its value, the new joins.
        kinds = [(e["collection_item_id"], e["event_type"]) for e in self.fake.events]
        self.assertIn((item["id"], "REMOVE"), kinds)
        self.assertIn((new_item["id"], "ADD"), kinds)

    def test_rejects_a_card_that_is_not_the_same_printing(self):
        card, item = self.own_reverse_holo_gengar()

        async def fetch(poketrace_id, reserve=2):
            return remote_card("other-variant-id", "Holofoil", number="095/165")

        with mock.patch.object(poketrace, "get_card", fetch):
            response = self.client.post(f"/api/collection/{item['id']}/variant", json={"poketrace_id": "other-variant-id"})

        self.assertEqual(response.status_code, 400)
        self.assertIn(item["id"], self.fake.items)


class CollectionEndpoint(ApiTestCase):
    def test_trend_uses_the_owned_grade_and_flags_unconfirmed_variant(self):
        card, item = self.own_reverse_holo_gengar(grade="PSA_9")

        with mock.patch.object(db, "get_cards", lambda ids: {i: self.fake.cards[i] for i in ids}), \
             mock.patch.object(db, "get_price_snapshots_for_cards", lambda ids: {
                 i: list(reversed(self.fake.get_price_snapshots(i))) for i in ids
             }):
            response = self.client.get("/api/collection")

        self.assertEqual(response.status_code, 200)
        row = response.json()["items"][0]
        self.assertEqual(row["owned_grade_change"]["grade"], "PSA_9")
        self.assertNotIn("week_change", row)
        self.assertTrue(row["variant_info"]["needs_confirmation"])
        self.assertEqual(row["variant_info"]["label"], "Reverse Holo")
        self.assertEqual(self.api_calls, [])


class RefreshNow(ApiTestCase):
    def test_starts_the_manual_sync(self):
        started = []

        async def fake_sync():
            started.append(True)
            price_sync._progress.update(running=False, phase=None)

        with mock.patch.object(price_sync, "_manual_sync", fake_sync):
            response = self.client.post("/api/refresh-now")
            for _ in range(50):
                if started:
                    break
                time.sleep(0.01)

        self.assertEqual(response.status_code, 202)
        self.assertEqual(started, [True])


if __name__ == "__main__":
    unittest.main()
