-- Variant identity (2026-10-10). Safe to re-run; changes no existing data.

-- Every variant PokeTrace lists for a card's printing (same set, number and
-- TCGPlayer product), filled in by the price sync:
-- {"checked_at": ..., "variants": [{"poketrace_id", "variant", ...}]}.
-- More than one variant means you should confirm which one you own, and that
-- graded data keyed only by the TCGPlayer product can't be trusted for it.
alter table public.cards
    add column if not exists variant_siblings jsonb;

-- When you confirmed (or corrected) which variant this copy is. Null means
-- unconfirmed; cards whose printing has only one variant never need it.
alter table public.collection_items
    add column if not exists variant_confirmed_at timestamptz;
