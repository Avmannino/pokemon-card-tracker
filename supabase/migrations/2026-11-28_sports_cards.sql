-- Sports section: your sports cards, priced by CardSight AI.
--
-- sports_cards holds one row per CardSight card you own. CardSight's terms
-- let you keep its card id; everything else from CardSight (name, set,
-- prices, photo) is a short-term cache that the daily refresh overwrites,
-- so no price history is kept. A card's row is deleted when you remove the
-- last copy.
--
-- sports_items is what you own of each card: quantity, grade, and your own
-- value for cards CardSight has no sales for.
--
-- Safe to re-run.

create table if not exists public.sports_cards (
    id uuid primary key default gen_random_uuid(),
    cardsight_id text not null unique,
    name text not null,
    number text,
    year text,
    release_name text,
    set_name text,
    attributes jsonb not null default '[]'::jsonb,
    -- Latest CardSight price summary (cardsight.summarize); replaced on
    -- every refresh.
    prices jsonb,
    priced_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.sports_items (
    id uuid primary key default gen_random_uuid(),
    sports_card_id uuid not null references public.sports_cards(id) on delete cascade,
    quantity integer not null default 1 check (quantity > 0),
    ownership_grade text not null default 'RAW'
        check (ownership_grade in ('RAW', 'PSA_7', 'PSA_8', 'PSA_9', 'PSA_10')),
    -- Your own value per card (e.g. no recent sales); used instead of
    -- CardSight's when set.
    manual_value numeric(12, 2) check (manual_value is null or manual_value >= 0),
    manual_source_url text,
    notes text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_sports_items_card
    on public.sports_items(sports_card_id);

-- Same as the other tables: no public policies, only the backend's secret
-- key reads and writes them.
alter table public.sports_cards enable row level security;
alter table public.sports_items enable row level security;
