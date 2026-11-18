-- Quantity changes (2026-11-17). Safe to re-run; changes no existing data.

-- Raising a card's quantity is recorded as an INCREASE (the extra copies
-- at their market value then), so the chart and Portfolio Change count
-- them as cards added, never as a gain. Lowering it is a REMOVE, as before.
alter table public.collection_events
    drop constraint if exists collection_events_event_type_check;

alter table public.collection_events
    add constraint collection_events_event_type_check
    check (event_type in ('ADD', 'REMOVE', 'INCREASE'));
