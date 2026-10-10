"""Pricing pipeline: variant identity, raw/graded valuation, owned-grade
trends, and API quota safety. No network or database access - every PokeTrace,
TCGGO and Supabase call is faked.

Run from backend/:  python -m unittest discover -t . -s tests -v
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from app.services import poketrace, price_sync, state_store, tcggo
from app.services.portfolio import value_change
from app.services.valuation import build_market_values, evaluate


NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)


def tier(**fields):
    base = {
        "avg": None, "low": None, "high": None, "avg1d": None, "avg7d": None,
        "avg30d": None, "median3d": None, "median7d": None, "median30d": None,
        "saleCount": None, "approxSaleCount": None, "lastUpdated": "2026-10-09T00:00:00.000Z",
    }
    return {**base, **fields}


def remote_card(poketrace_id, variant, tcgplayer_id="516663", number="094/165",
                set_slug="sv-scarlet-and-violet-151", tcg=None, ebay=None, ebay_lp=None):
    prices = {}
    if tcg is not None:
        prices["tcgplayer"] = {"NEAR_MINT": tier(avg=tcg, saleCount=1000)}
    if ebay is not None or ebay_lp is not None:
        prices["ebay"] = {}
        if ebay is not None:
            prices["ebay"]["NEAR_MINT"] = tier(avg=ebay * 2, median30d=ebay, saleCount=200)
        if ebay_lp is not None:
            prices["ebay"]["LIGHTLY_PLAYED"] = tier(avg=ebay_lp, median30d=ebay_lp)
    return {
        "id": poketrace_id,
        "name": "Gengar",
        "cardNumber": number,
        "set": {"slug": set_slug, "name": "SV: Scarlet & Violet 151"},
        "variant": variant,
        "rarity": "Rare",
        "image": f"https://img/{poketrace_id}.webp",
        "refs": {"tcgplayerId": tcgplayer_id},
        "marketplaceUrls": {"tcgplayer": "https://tcg", "ebay": "https://ebay"},
        "prices": prices,
        "lastUpdated": "2026-10-09T23:54:31.483Z",
    }


def card_row(card_id, poketrace_id, variant, tcgplayer_id="516663", number="094/165",
             set_slug="sv-scarlet-and-violet-151", siblings=None):
    return {
        "id": card_id, "poketrace_id": poketrace_id, "name": "Gengar",
        "card_number": number, "set_slug": set_slug, "variant": variant,
        "tcgplayer_id": tcgplayer_id, "variant_siblings": siblings,
    }


def snap(value, kind, hours=0, grade="RAW", **meta):
    providers = {
        "tcgplayer": ("TCGPlayer raw market (via PokeTrace)", {"provider": "PokeTrace", "underlying_source": "tcgplayer", "tier": "NEAR_MINT"}),
        "ebay": ("eBay raw sold average (via PokeTrace)", {"provider": "PokeTrace", "underlying_source": "ebay", "tier": "NEAR_MINT"}),
        "tcggo": ("eBay graded sold median (via TCGGO)", {"provider": "TCGGO", "underlying_source": "ebay"}),
        "manual": ("PSA CardFacts — Average Price", {"entry_method": "manual"}),
    }
    source, base = providers[kind]
    return {
        "grade": grade,
        "source": source,
        "value": value,
        "source_url": None,
        "observed_at": (NOW + timedelta(hours=hours)).isoformat(),
        "metadata": {**base, **meta},
    }


class FakeState:
    """In-memory stand-in for the Supabase-backed state store."""

    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value):
        self.data[key] = value


class StateIsolated(unittest.TestCase):
    def setUp(self):
        self.state = FakeState()
        for patcher in (
            mock.patch.object(state_store, "get", self.state.get),
            mock.patch.object(state_store, "set", self.state.set),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        poketrace._quota = None
        tcggo._quota = None


class RawSourceExtraction(StateIsolated):
    def test_near_mint_only_never_falls_back_to_other_conditions(self):
        # eBay has only Lightly Played sales: no eBay snapshot at all.
        rows = poketrace.extract_raw_sources(remote_card("a", "Holofoil", tcg=2.64, ebay_lp=1.10))
        self.assertEqual([r["metadata"]["underlying_source"] for r in rows], ["tcgplayer"])
        self.assertEqual(rows[0]["metadata"]["tier"], "NEAR_MINT")

    def test_ebay_uses_robust_sold_median_and_records_variant(self):
        rows = poketrace.extract_raw_sources(remote_card("a", "Holofoil", tcg=2.64, ebay=4.93))
        ebay = next(r for r in rows if r["metadata"]["underlying_source"] == "ebay")
        tcg = next(r for r in rows if r["metadata"]["underlying_source"] == "tcgplayer")

        self.assertEqual(ebay["value"], 4.93)  # median30d, not the 9.86 latest-day avg
        self.assertEqual(ebay["metadata"]["value_basis"], "median30d")
        self.assertEqual(tcg["value"], 2.64)
        self.assertEqual(tcg["metadata"]["value_basis"], "avg")
        for row in rows:
            self.assertEqual(row["metadata"]["variant"], "Holofoil")
            self.assertEqual(row["metadata"]["poketrace_id"], "a")


class VariantMatching(StateIsolated):
    def setUp(self):
        super().setUp()
        self.reverse = remote_card("rh", "Reverse_Holofoil", tcg=8.52, ebay=9.97)
        self.holo = remote_card("holo", "Holofoil", tcg=2.64, ebay=4.93)

    def test_selects_exact_saved_variant_from_siblings(self):
        match, problem = poketrace.match_exact(card_row("c1", "holo", "Holofoil"), [self.reverse, self.holo])
        self.assertIsNone(problem)
        self.assertEqual(match["id"], "holo")

    def test_never_falls_back_to_another_variant(self):
        # Saved card missing from the response: no sibling stands in for it.
        match, problem = poketrace.match_exact(card_row("c1", "holo", "Holofoil"), [self.reverse])
        self.assertIsNone(match)
        self.assertEqual(problem, "not_returned")

    def test_refuses_when_poketrace_relabels_the_variant(self):
        relabeled = remote_card("holo", "Reverse_Holofoil", tcg=8.52)
        match, problem = poketrace.match_exact(card_row("c1", "holo", "Holofoil"), [relabeled])
        self.assertIsNone(match)
        self.assertIn("confirmed", problem)

    def test_siblings_only_include_the_same_printing(self):
        other_number = remote_card("x", "Holofoil", number="095/165")
        siblings = poketrace.variant_siblings(
            poketrace.saved_printing_key(card_row("c1", "holo", "Holofoil")),
            [self.reverse, self.holo, other_number],
        )
        self.assertEqual({v["variant"] for v in siblings["variants"]}, {"Holofoil", "Reverse_Holofoil"})


class RawPull(unittest.IsolatedAsyncioTestCase):
    """price_sync._pull_raw against fake PokeTrace and database calls."""

    def setUp(self):
        self.state = FakeState()
        self.inserted = []
        self.batch_calls = []
        self.single_calls = []
        self.siblings_saved = {}
        self.remote = {
            "516663": [
                remote_card("rh", "Reverse_Holofoil", tcg=8.52, ebay=9.97),
                remote_card("holo", "Holofoil", tcg=2.64, ebay=4.93),
            ],
            "516713": [remote_card("mew", "Holofoil", tcgplayer_id="516713", number="150/165", tcg=2.26)],
        }
        self.cards = {
            "c-gengar": card_row("c-gengar", "holo", "Holofoil"),
            "c-mewtwo": card_row("c-mewtwo", "mew", "Holofoil", tcgplayer_id="516713", number="150/165"),
        }
        self.items = [
            {"id": "i1", "card_id": "c-gengar", "ownership_grade": "RAW", "quantity": 1},
            {"id": "i2", "card_id": "c-gengar", "ownership_grade": "PSA_10", "quantity": 2},
            {"id": "i3", "card_id": "c-mewtwo", "ownership_grade": "RAW", "quantity": 1},
        ]

        async def fake_batch(ids, reserve=2):
            self.batch_calls.append(list(ids))
            return [card for tcg_id in ids for card in self.remote.get(tcg_id, [])]

        async def fake_single(poketrace_id, reserve=2):
            self.single_calls.append(poketrace_id)
            for cards in self.remote.values():
                for card in cards:
                    if card["id"] == poketrace_id:
                        return card
            raise poketrace.PokeTraceError("not found")

        patches = [
            mock.patch.object(state_store, "get", self.state.get),
            mock.patch.object(state_store, "set", self.state.set),
            mock.patch.object(price_sync.db, "get_collection_items", lambda: list(self.items)),
            mock.patch.object(price_sync.db, "get_cards", lambda ids: {i: self.cards[i] for i in ids if i in self.cards}),
            mock.patch.object(price_sync.db, "insert_price_snapshot", lambda **kw: self.inserted.append(kw)),
            mock.patch.object(price_sync.db, "save_card", lambda card: card),
            mock.patch.object(price_sync.db, "update_card_variant_siblings", lambda cid, s: self.siblings_saved.__setitem__(cid, s)),
            mock.patch.object(poketrace, "get_cards_by_tcgplayer_ids", fake_batch),
            mock.patch.object(poketrace, "get_card", fake_single),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_prices_the_exact_variant_and_never_the_sibling(self):
        record = price_sync._new_raw_record()
        await price_sync._pull_raw(record)

        gengar = [row for row in self.inserted if row["card_id"] == "c-gengar"]
        self.assertEqual({row["value"] for row in gengar}, {2.64, 4.93})  # Holo, not RH 8.52/9.97
        self.assertTrue(all(row["metadata"]["variant"] == "Holofoil" for row in gengar))

    async def test_duplicate_collection_rows_cost_one_lookup(self):
        record = price_sync._new_raw_record()
        await price_sync._pull_raw(record)

        # Two rows hold Gengar; both cards fit one batched request.
        self.assertEqual(len(self.batch_calls), 1)
        self.assertEqual(sorted(self.batch_calls[0]), ["516663", "516713"])
        self.assertEqual(self.single_calls, [])
        self.assertEqual((record["attempted"], record["succeeded"]), (2, 2))
        self.assertEqual(len([r for r in self.inserted if r["card_id"] == "c-gengar"]), 2)

    async def test_records_variant_siblings(self):
        await price_sync._pull_raw(price_sync._new_raw_record())
        variants = {v["variant"] for v in self.siblings_saved["c-gengar"]["variants"]}
        self.assertEqual(variants, {"Holofoil", "Reverse_Holofoil"})
        self.assertEqual(len(self.siblings_saved["c-mewtwo"]["variants"]), 1)

    async def test_missing_from_batch_is_looked_up_alone_not_substituted(self):
        self.remote["516663"] = [remote_card("rh", "Reverse_Holofoil", tcg=8.52)]
        self.remote["extra"] = [remote_card("holo", "Holofoil", tcg=2.64)]
        record = price_sync._new_raw_record()
        await price_sync._pull_raw(record)

        self.assertEqual(self.single_calls, ["holo"])
        gengar = [row for row in self.inserted if row["card_id"] == "c-gengar"]
        self.assertEqual([row["value"] for row in gengar], [2.64])

    async def test_variant_mismatch_stores_nothing(self):
        self.remote["516663"] = [remote_card("holo", "Reverse_Holofoil", tcg=8.52)]
        record = price_sync._new_raw_record()
        await price_sync._pull_raw(record)

        self.assertEqual([r for r in self.inserted if r["card_id"] == "c-gengar"], [])
        self.assertEqual(record["failed"], 1)
        self.assertIn("Gengar", record["issues"][0]["name"])

    async def test_poketrace_quota_reserve_stops_raw_sync(self):
        async def exhausted(ids, reserve=2):
            raise poketrace.PokeTraceRateLimited("quota")

        with mock.patch.object(poketrace, "get_cards_by_tcgplayer_ids", exhausted):
            record = price_sync._new_raw_record()
            await price_sync._pull_raw(record)

        self.assertEqual(record["stopped_early"], "quota")
        self.assertEqual(self.inserted, [])


class PokeTraceRequests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = FakeState()
        for patcher in (
            mock.patch.object(state_store, "get", self.state.get),
            mock.patch.object(state_store, "set", self.state.set),
            mock.patch.object(poketrace, "REQUEST_SPACING_SECONDS", 0),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        poketrace._quota = None

    async def test_refuses_before_sending_when_quota_at_reserve(self):
        poketrace._quota = {"remaining": 15, "limit": 250, "resets_at": "2999-01-01T00:00:00Z"}
        with mock.patch.object(poketrace.httpx, "AsyncClient", side_effect=AssertionError("sent")):
            with self.assertRaises(poketrace.PokeTraceRateLimited):
                await poketrace.get_cards_by_tcgplayer_ids(["1"], reserve=20)

    async def test_batch_follows_pagination(self):
        pages = [
            {"data": [{"id": "a"}], "pagination": {"hasMore": True, "nextCursor": "c2"}},
            {"data": [{"id": "b"}], "pagination": {"hasMore": False, "nextCursor": None}},
        ]
        calls = []

        async def fake_request(path, params=None, reserve=2):
            calls.append(params)
            return pages[len(calls) - 1]

        with mock.patch.object(poketrace, "_request", fake_request):
            cards = await poketrace.get_cards_by_tcgplayer_ids(["1", "2"])

        self.assertEqual([c["id"] for c in cards], ["a", "b"])
        self.assertEqual(calls[0]["tcgplayer_ids"], "1,2")
        self.assertEqual(calls[1]["cursor"], "c2")


class RawValuation(unittest.TestCase):
    def test_divergent_sources_use_tcgplayer_and_flag(self):
        # Gengar Holo: TCGPlayer $2.64 vs eBay 30d median $4.93 (87% apart).
        result = evaluate("RAW", [snap(2.64, "tcgplayer"), snap(4.93, "ebay", value_basis="median30d")])
        self.assertEqual(result["estimate"], 2.64)
        self.assertEqual(result["method"], "tcgplayer_primary")
        self.assertTrue(result["divergent"])
        self.assertEqual(result["confidence"], "LOW")
        roles = {c["kind"]: c["role"] for c in result["components"]}
        self.assertEqual(roles, {"tcgplayer": "primary", "ebay": "excluded"})

    def test_agreeing_sources_blend(self):
        result = evaluate("RAW", [snap(10.0, "tcgplayer"), snap(10.0, "ebay", value_basis="median30d")])
        self.assertEqual(result["estimate"], 10.0)
        self.assertEqual(result["confidence"], "HIGH")

        result = evaluate("RAW", [snap(10.0, "tcgplayer"), snap(11.0, "ebay", value_basis="median30d")])
        # 10% apart: eBay weight 0.5 * (1 - 0.1/0.5) = 0.4.
        self.assertAlmostEqual(result["estimate"], 10.4)
        self.assertEqual(result["method"], "tcgplayer_ebay_blend")

    def test_blend_weight_is_continuous(self):
        def estimate(ebay):
            return evaluate("RAW", [snap(10.0, "tcgplayer"), snap(ebay, "ebay", value_basis="median30d")])["estimate"]

        # Crossing the divergence limit never jumps the price.
        self.assertAlmostEqual(estimate(14.99), 10.0, places=1)
        self.assertEqual(estimate(15.01), 10.0)

    def test_old_ebay_rows_use_their_30_day_average(self):
        old = snap(6.48, "ebay", avg_30d=4.49)  # pre-change row: value was a 1-day avg
        result = evaluate("RAW", [snap(4.68, "tcgplayer"), old])
        ebay = next(c for c in result["components"] if c["kind"] == "ebay")
        self.assertEqual(ebay["value"], 4.49)

    def test_ebay_only_and_tcgplayer_only(self):
        self.assertEqual(evaluate("RAW", [snap(3.0, "ebay", value_basis="median30d")])["confidence"], "LOW")
        self.assertEqual(evaluate("RAW", [snap(3.0, "tcgplayer")])["method"], "tcgplayer_only")

    def test_other_variant_and_condition_rows_are_excluded(self):
        rows = [snap(8.52, "tcgplayer", variant="Reverse_Holofoil"), snap(2.0, "ebay", tier="LIGHTLY_PLAYED")]
        result = evaluate("RAW", rows, variant="Holofoil")
        self.assertIsNone(result["estimate"])
        self.assertTrue(all(c["role"] == "excluded" for c in result["components"]))

    def test_manual_overrides_automated(self):
        result = evaluate("RAW", [snap(2.64, "tcgplayer"), snap(5.0, "manual")])
        self.assertEqual(result["estimate"], 5.0)
        self.assertEqual(result["method"], "manual")


class GradedValuation(unittest.TestCase):
    def test_too_few_graded_sales_are_not_a_price(self):
        # Mega Gengar ex: TCGGO median of 2 sales.
        result = evaluate("PSA_10", [snap(169.5, "tcggo", grade="PSA_10", sample_size=2)])
        self.assertIsNone(result["estimate"])
        self.assertIn("Only 2 recent sales", result["summary"])

    def test_graded_median_of_enough_sales_is_labeled_as_such(self):
        result = evaluate("PSA_10", [snap(233.35, "tcggo", grade="PSA_10", sample_size=5)])
        self.assertEqual(result["estimate"], 233.35)
        self.assertIn("last 5 eBay PSA 10 sales", result["summary"])

    def test_variant_ambiguous_product_is_not_used(self):
        result = evaluate(
            "PSA_9", [snap(45.0, "tcggo", grade="PSA_9", sample_size=5)], variant_ambiguous=True
        )
        self.assertIsNone(result["estimate"])
        self.assertIn("variants apart", result["summary"])

    def test_manual_graded_value_wins(self):
        rows = [
            snap(169.5, "tcggo", grade="PSA_10", sample_size=5),
            snap(61.51, "manual", grade="PSA_10"),
        ]
        result = evaluate("PSA_10", rows)
        self.assertEqual(result["estimate"], 61.51)
        self.assertEqual(result["confidence"], "MANUAL")

    def test_ambiguity_comes_from_the_card(self):
        card = card_row("c", "rh", "Reverse_Holofoil", siblings={
            "variants": [{"variant": "Reverse_Holofoil"}, {"variant": "Holofoil"}]
        })
        values = build_market_values([snap(45.0, "tcggo", grade="PSA_9", sample_size=5)], card)
        self.assertIsNone(values["PSA_9"]["estimate"])


class OwnedGradeTrend(unittest.TestCase):
    def setUp(self):
        self.snapshots = [
            snap(2.0, "tcgplayer", hours=-24 * 9),
            snap(4.0, "tcgplayer", hours=-1),  # raw doubled this week
            snap(30.0, "manual", hours=-24 * 9, grade="PSA_10"),
            snap(33.0, "manual", hours=-2, grade="PSA_10"),
        ]

    def test_psa_owner_gets_psa_movement_not_raw(self):
        psa = value_change(self.snapshots, "PSA_10", now=NOW)
        raw = value_change(self.snapshots, "RAW", now=NOW)
        self.assertEqual((psa["grade"], psa["change"], psa["change_pct"]), ("PSA_10", 3.0, 10.0))
        self.assertEqual(raw["change"], 2.0)
        self.assertEqual(psa["grade_label"], "PSA 10")

    def test_no_history_for_owned_grade_is_reported_not_substituted(self):
        only_raw = [s for s in self.snapshots if s["grade"] == "RAW"]
        result = value_change(only_raw, "PSA_9", now=NOW)
        self.assertFalse(result["has_price"])
        self.assertFalse(result["has_history"])
        self.assertIsNone(result["change"])

        single = [snap(30.0, "manual", hours=-1, grade="PSA_9")]
        result = value_change(single, "PSA_9", now=NOW)
        self.assertTrue(result["has_price"])
        self.assertFalse(result["has_history"])


class GradedPullQuotaIsolation(unittest.IsolatedAsyncioTestCase):
    """A graded-provider problem never affects raw prices."""

    async def test_graded_quota_exhaustion_leaves_raw_intact(self):
        state = FakeState()
        inserted = []
        items = [{"id": "i1", "card_id": "c1", "ownership_grade": "PSA_10", "quantity": 1}]
        cards = {"c1": card_row("c1", "holo", "Holofoil")}

        async def fake_batch(ids, reserve=2):
            return [remote_card("holo", "Holofoil", tcg=2.64)]

        async def graded_exhausted(card):
            raise tcggo.TcggoRateLimited("TCGGO daily quota nearly used up")

        patches = [
            mock.patch.object(state_store, "get", state.get),
            mock.patch.object(state_store, "set", state.set),
            mock.patch.object(price_sync.db, "get_collection_items", lambda: items),
            mock.patch.object(price_sync.db, "get_cards", lambda ids: {i: cards[i] for i in ids}),
            mock.patch.object(price_sync.db, "get_card", lambda cid: cards[cid]),
            mock.patch.object(price_sync.db, "insert_price_snapshot", lambda **kw: inserted.append(kw)),
            mock.patch.object(price_sync.db, "save_card", lambda card: card),
            mock.patch.object(price_sync.db, "update_card_variant_siblings", lambda *a: True),
            mock.patch.object(poketrace, "get_cards_by_tcgplayer_ids", fake_batch),
            mock.patch.object(price_sync, "pull_card_graded_prices", graded_exhausted),
            mock.patch.object(tcggo, "is_configured", lambda: True),
            mock.patch.object(tcggo, "requests_remaining", lambda: None),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

        price_sync._progress.update(running=False)
        await price_sync._manual_sync()

        manual = state.data["price_sync"]["manual"]
        self.assertEqual(manual["raw"]["succeeded"], 1)
        self.assertEqual(len(inserted), 1)
        self.assertIn("quota", manual["graded"]["stopped_early"])

    async def test_variant_ambiguous_cards_skip_tcggo_without_a_request(self):
        state = FakeState()
        items = [{"id": "i1", "card_id": "c1", "ownership_grade": "PSA_9", "quantity": 1}]
        cards = {"c1": card_row("c1", "rh", "Reverse_Holofoil")}
        pulled = []

        async def track(card):
            pulled.append(card["id"])
            return 1

        siblings = {"c1": {"variants": [{"variant": "Holofoil"}, {"variant": "Reverse_Holofoil"}]}}
        patches = [
            mock.patch.object(state_store, "get", state.get),
            mock.patch.object(state_store, "set", state.set),
            mock.patch.object(price_sync.db, "get_collection_items", lambda: items),
            mock.patch.object(price_sync.db, "get_card", lambda cid: cards[cid]),
            mock.patch.object(price_sync, "pull_card_graded_prices", track),
            mock.patch.object(tcggo, "requests_remaining", lambda: None),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

        record = price_sync._new_graded_record()
        await price_sync._pull_graded(record, siblings)

        self.assertEqual(pulled, [])
        self.assertEqual(record["skipped_variant_ambiguous"], 1)


if __name__ == "__main__":
    unittest.main()
