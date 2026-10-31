-- Small Hits (2026-10-30). Safe to re-run.

-- Cards kept in the Small Hits drawer: out of the main collection table
-- and the regular syncs, priced only by SYNC SMALL HITS. They still count
-- toward the collection's value.
alter table public.collection_items
    add column if not exists small_hit boolean not null default false;

-- The 42 cards worth under $2 each when Small Hits was set up.
update public.collection_items
set small_hit = true
where id in (
    '2a69ec16-408c-4e11-9e0e-013974868d33',  -- Adversity Policy 074/086 ($0.18)
    '11ea0be4-9ac5-418f-8b3a-b282fc89996b',  -- Aurorus 024/088 ($1.58)
    'eee1da13-a801-4f36-ac9c-aa3196eed3e9',  -- Beedrill ex 003/086 ($0.50)
    '3a6c04c4-60e5-465a-8394-93eecd8c9e50',  -- Charmander 004/165 ($0.42)
    '95cb737f-32ab-4b73-80fa-f2d9721117d9',  -- Chesnaught 007/086 ($0.24)
    'a84c30d1-ef3c-4274-9dc4-fb55d7af942d',  -- Chesnaught 007/086 ($0.24)
    'e33fc154-eb80-4778-87dc-370aa6381c51',  -- Chespin 005/086 ($0.20)
    '3a2b682a-1135-4b04-9b09-00cf4cd0c98a',  -- Cramorant V - SWSH086 SWSH086 ($0.99)
    'cf610db1-3559-42ac-95dc-29699db304c8',  -- Delphox 013/086 ($0.30)
    '4fe44b08-7b4f-4061-ba15-3a7049fa7a2b',  -- Gengar 050/088 ($1.79)
    '26d866fa-62a9-4960-9807-d9e4fae84e2e',  -- Gengar (2021 Copyright Date) 057/198 ($1.43)
    '20b87f47-fcce-493e-8df4-a3eeaf679f69',  -- Goodra 068/086 ($0.17)
    '1410f6de-4aaf-41ec-851a-b037ca70064b',  -- Ho-Oh 010/086 ($0.20)
    '9b2a0367-5a3d-4919-b651-9047dcc072ac',  -- Keldeo 019/086 ($0.11)
    '45bea421-6554-4883-a0ed-d639f9b3454a',  -- Keldeo 019/086 ($0.24)
    '97807622-8645-4b94-9298-8131f4b70c61',  -- Krookodile 117/198 ($0.10)
    '50d028ef-a1e1-4550-9e91-a15ab8e879b8',  -- Lotad 035/159 ($0.14)
    'd535498c-c8fb-4a65-81db-646a1ff99887',  -- Lugia 022/025 ($0.58)
    '7ec6aa5d-0c67-4bcb-b30f-b82aae3a3f8c',  -- Magnetic Metal Energy 085/086 ($0.30)
    '40fd294e-a8a1-42d8-be6c-fd0288e1dfea',  -- Mankey 106/193 ($0.13)
    '837e5287-6423-43ad-abf6-96a376695804',  -- Mega Floette ex 035/086 ($0.48)
    '62de2f5c-3131-4087-8473-028b5bd32369',  -- Mega Greninja ex 022/086 ($0.84)
    'd2b43f35-94ce-4b75-b043-72ea348547b2',  -- Metagross 061/086 ($0.23)
    '97ab36a3-652a-4dd8-8737-6e249604d9c5',  -- Metang 094/086 ($1.73)
    '835abdfe-bb62-4e34-a853-9f37177d375b',  -- Minccino 072/086 ($0.24)
    '8bbc27f2-0c45-4f19-b839-d655d1a89774',  -- Phanpy 044/086 ($0.29)
    'fd10e574-6af0-4d42-931a-3364b05a94cb',  -- Pikachu 20/108 ($1.47)
    'b16c2c35-0e1d-4baf-bb90-45dbeb924efd',  -- Pikachu ex 028/131 ($1.91)
    '73205e0e-e9bc-4c58-8a24-98b13e1beb14',  -- Pumpkaboo 040/086 ($0.21)
    'b49b95a7-1d62-4e46-8c34-8deec5cbe56c',  -- Quilladin 006/086 ($0.25)
    'b9b620df-9638-4dbc-ad8f-81a4e3a41e4a',  -- Roaring Moon (Poke Ball Pattern) 065/131 ($0.28)
    '4815c32e-5ab2-4a96-9507-013a6f2882ad',  -- Sliggoo 067/086 ($0.28)
    'bc692832-f2a8-4258-a2b8-47cb18084de4',  -- Spearow 102/132 ($0.12)
    '612c3c8d-46f2-4b87-80d2-d7253bb66d6b',  -- Toxtricity 103/094 ($1.82)
    '94ed8d45-52c9-4508-99b9-c13580ba3263',  -- Trevenant 039/086 ($0.14)
    '4a45e974-9104-4df5-aff4-25b0ffb40b3b',  -- Trubbish 056/086 ($0.25)
    'b1d087c0-50c0-4dfe-9610-378a2a549179',  -- Uxie 078/191 ($0.24)
    '0d1e12c2-ff4a-49e7-a05f-b44f8e867e90',  -- Vulpix 008/086 ($0.29)
    '8a8778b5-a70a-4f0d-ab7c-f8c0f95753ef',  -- Watchog 097/086 ($1.25)
    '9a2d1f0a-42c1-4f3c-8c0b-3e18970b40bb',  -- Weedle 001/086 ($0.21)
    '8cf56c89-ae42-467c-b3a2-be6a17eba6cf',  -- Xerneas 042/086 ($0.17)
    '4f97de30-9d5d-4440-9f29-15e5a1c6fb21'  -- Zubat 049/086 ($0.23)
);
