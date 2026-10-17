-- Slab photos (2026-10-17). Safe to re-run; changes no existing data.

-- The PSA cert number on this copy's label, and the slab photos PSA has for
-- it: {"front": url, "back": url, "fetched_at": ...} (urls null when PSA
-- has none).
alter table public.collection_items
    add column if not exists psa_cert_number text,
    add column if not exists slab_images jsonb;
