-- Slab photos (2026-10-17). Safe to re-run; changes no existing data.

-- The PSA cert number on this copy's label. Your slab photos are matched to
-- the card by it (frontend/src/slabPhotos.json).
alter table public.collection_items
    add column if not exists psa_cert_number text;
