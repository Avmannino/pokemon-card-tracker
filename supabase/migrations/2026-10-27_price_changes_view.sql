-- Faster loads (2026-10-27). Safe to re-run; changes and deletes no data.

-- Most saved prices repeat the one before them (a sync re-saves unchanged
-- prices). This view is the price history without those repeats: each
-- source's first price, every change, and its newest row (which carries
-- the latest sale counts and dates). The app reads this instead of every
-- row, about a quarter as many.
create index if not exists idx_price_snapshots_series
    on public.price_snapshots(card_id, grade, source, observed_at, id);

create or replace view public.price_changes
with (security_invoker = true) as
select id, card_id, grade, source, value, source_url, observed_at, metadata, created_at
from (
    select
        s.*,
        lag(s.value) over (
            partition by s.card_id, s.grade, s.source
            order by s.observed_at, s.id
        ) as previous_value,
        row_number() over (
            partition by s.card_id, s.grade, s.source
            order by s.observed_at desc, s.id desc
        ) as newest_first
    from public.price_snapshots s
) history
where previous_value is distinct from value
   or newest_first = 1;

-- Like the tables, only the backend (secret key) can read it.
revoke all on public.price_changes from anon, authenticated;
