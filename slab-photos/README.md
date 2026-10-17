# Slab photos

Drop photos of your graded slabs here (JPG, PNG or WebP, any size), named
by the slab's PSA cert number:

- `PSA-<cert>-front.jpg`, e.g. `PSA-126308727-front.jpg`
- `PSA-<cert>-back.jpg` (optional)

Then:

1. Run `python3 scripts/slab_photos.py` from the project root. It writes
   shrunk copies to `frontend/public/slabs/` and lists them in
   `frontend/src/slabPhotos.json`. The originals here aren't committed.
2. Set the same cert number on the card in Supabase (`psa_cert_number` in
   its `collection_items` row).
3. Deploy the frontend.
