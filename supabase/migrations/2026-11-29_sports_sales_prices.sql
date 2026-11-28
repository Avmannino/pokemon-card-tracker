-- Values from recent eBay sales (The Card API) for sports cards CardSight
-- can't value: cards added by hand and cards with fewer than 3 CardSight
-- sales. Only the app's own figures per grade (median price, number of
-- sales, last sale date) are kept, never the sales themselves, and the
-- daily refresh replaces them.
--
-- Safe to re-run.

alter table public.sports_cards
    add column if not exists sales_prices jsonb,
    add column if not exists sales_priced_at timestamptz;
