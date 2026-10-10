# Pokémon Card Market Tracker

A free-first personal Pokémon card collection tracker built with:

- React + Vite + JavaScript
- Python + FastAPI
- Supabase PostgreSQL
- PokeTrace Free API for raw US market pricing

## What this first version does

- Search Pokémon cards.
- Add raw or PSA 8/9/10 copies to your collection.
- Pull raw pricing automatically twice a day (once AM, once PM) from:
  - eBay raw sold-sale averages returned by PokeTrace.
  - TCGPlayer raw market data returned by PokeTrace.
- Store every price pull as a historical snapshot.
- Calculate a current market estimate from the newest value from each source.
- Manually add PSA 8–10 values from legitimate public comps without scraping.
- Calculate your known collection value from the grade you actually own.

## Important free-tier limitation

PokeTrace Free includes raw US pricing but not graded pricing. The project does
not scrape PSA because you should not build the project around prohibited
automated access. PSA values are therefore manual in this MVP.

The graded-value portion is already modeled as source snapshots, so a future
free/authorized graded API can be plugged in without changing the database or
frontend concept.

---

# 1. Install prerequisites

On Windows, install:

1. Python 3.12 or newer.
2. Node.js LTS.
3. Git is optional but recommended.

Verify in PowerShell:

```powershell
python --version
node --version
npm --version
```

---

# 2. Create the free Supabase project

Go to:

https://supabase.com/dashboard

1. Sign in or create an account.
2. Click **New project**.
3. Pick or create an organization.
4. Project name: `pokemon-card-tracker`
5. Choose a strong database password and save it somewhere secure.
6. Pick a nearby region.
7. Make sure the project is on the **Free** plan.
8. Create the project.

## Run the database schema

In your Supabase project:

1. Open **SQL Editor** in the left sidebar.
2. Click **New query**.
3. Open this local file:
   `supabase/schema.sql`
4. Copy the entire file into the SQL editor.
5. Click **Run**.
6. You should see a successful completion message.

## Get the Supabase URL and secret key

Supabase has moved to newer API key types.

1. In the project dashboard, click **Connect**.
2. Copy your **Project URL**.
3. Then go to **Settings → API Keys**.
4. Look for **Publishable and secret API keys**.
5. If your project does not have them, click **Create new API keys**.
6. Copy the key beginning with:
   `sb_secret_`
7. Never put that secret key in the React frontend or commit it to GitHub.

You will use:

```text
SUPABASE_URL=https://YOUR_PROJECT_REF.supabase.co
SUPABASE_SECRET_KEY=sb_secret_...
```

---

# 3. Create the free PokeTrace account

Go to:

https://poketrace.com/dashboard

1. Create an account.
2. Confirm your email if requested.
3. Stay on the **Free** plan.
4. In the dashboard, create a new API key.
5. Name it:
   `pokemon-card-tracker`
6. Copy the key. It should begin with:
   `pc_`
7. No paid subscription is required for the raw-price functionality used here.

You will use:

```text
POKETRACE_API_KEY=pc_...
```

The Free plan has a daily request limit and a burst rate limit. The project's
scheduled price pull deliberately waits between requests.

---

# 4. Configure the backend

Open PowerShell in the project root.

Create a Python virtual environment:

```powershell
python -m venv .venv
```

If PowerShell blocks activation, run this for the current terminal only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Activate the environment:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install the backend packages:

```powershell
pip install -r .\backend\requirements.txt
```

Copy the environment template:

```powershell
Copy-Item .\backend\.env.example .\backend\.env
```

Open:

```text
backend/.env
```

Replace the placeholders with your real values:

```env
SUPABASE_URL=https://YOUR_PROJECT_REF.supabase.co
SUPABASE_SECRET_KEY=sb_secret_YOUR_REAL_SECRET_KEY
POKETRACE_API_KEY=pc_YOUR_REAL_POKETRACE_KEY
FRONTEND_ORIGIN=http://localhost:5173
```

Save the file.

---

# 5. Start the backend

In PowerShell:

```powershell
cd .\backend
```

Make sure the virtual environment is still active.

Run:

```powershell
uvicorn app.main:app --reload --port 8000
```

Leave this terminal open.

Test it in your browser:

```text
http://localhost:8000/health
```

Expected response:

```json
{"status":"ok"}
```

FastAPI's local interactive API docs are at:

```text
http://localhost:8000/docs
```

---

# 6. Configure and start the React frontend

Open a SECOND PowerShell window.

Go to the frontend folder:

```powershell
cd PATH\TO\pokemon-card-tracker\frontend
```

Install packages:

```powershell
npm install
```

Copy the frontend environment template:

```powershell
Copy-Item .\.env.example .\.env
```

The default file should contain:

```env
VITE_API_BASE_URL=http://localhost:8000
```

Start Vite:

```powershell
npm run dev
```

Open the URL Vite prints, normally:

```text
http://localhost:5173
```

---

# 7. Add your first card

1. Use the search box.
2. Search for something specific, such as:
   `Glaceon GX`
3. Find the correct set/card number.
4. Click **Add**.
5. Choose what you own:
   - Raw
   - PSA 8
   - PSA 9
   - PSA 10
6. Enter quantity.
7. Optionally enter what you paid.
8. Click **Add Card**.

When a card is added, the backend immediately asks PokeTrace for current raw
data and stores separate source snapshots.

---

# 8. Add PSA 8–10 values for free

This part is manual in the first version because the free PokeTrace API does
not return graded values.

A practical public source is PSA CardFacts.

Go to:

https://www.psacard.com/cardfacts

1. Search for the exact card.
2. Be extremely careful about:
   - year
   - language
   - set
   - card number
   - holo/non-holo
   - 1st Edition
   - Unlimited
   - Shadowless
3. Open the exact CardFacts page.
4. Find **Prices By Grade**.
5. For the first version, use the **Average Price** column.
6. In this app, click **PSA values** beside the card.
7. Leave the source name as:
   `PSA CardFacts — Average Price`
8. Paste the exact CardFacts page URL into **Source page URL**.
9. Enter the values for PSA 8, PSA 9 and PSA 10.
10. Click **Save PSA Values**.

If you later add a second legitimate source, use a different source name.
The app will calculate the median of the newest value from each source.

---

# 9. How prices refresh

Prices are pulled from the pricing APIs **only** in these cases. Loading or
refreshing the page never calls them; it only reads what's saved in Supabase.

1. **Twice a day** by the GitHub Action (`.github/workflows/price-sync.yml`),
   which wakes the Render backend and calls `POST /api/refresh-now`.
2. When you press **Refresh Prices** (same endpoint, 15-minute cooldown).
3. When you add a card or change its variant (one PokeTrace request for that
   card).

Searching is also live: each search is one PokeTrace request.

What a sync does:

- **Raw** prices come from PokeTrace, looked up 20 TCGPlayer products per
  request. A product's response includes every variant, and each card is
  matched to its exact saved PokeTrace id and variant. A different variant
  is never used in its place. A card held in several collection rows is
  looked up once.
- **Graded** (PSA 8–10) prices come from TCGGO's eBay sold medians, only when
  `RAPIDAPI_KEY` is set. Cards you own graded are pulled first. TCGGO matches
  on the TCGPlayer product, so cards whose product has several variants
  (e.g. Holo and Reverse Holo) are skipped without a request.
- **Quota safety:** both APIs report remaining requests in response headers,
  and the app stores them. A sync stops when PokeTrace is down to 20
  requests, leaving room for searches and new cards. Graded pulls stop
  before TCGGO's daily quota would go into paid overage, keeping enough for
  the scheduled syncs before the reset. A graded failure never affects raw
  prices that were already saved.

---

# 10. How the valuation works

Only the newest snapshot from each source counts. Then, per grade:

1. **Your manual entry wins.** A PSA value you enter overrides automated data
   for that grade. Several manual entries are blended by median.
2. **Raw cards** use Near Mint prices for the exact variant only:
   - TCGPlayer's Near Mint market price is the primary signal.
   - eBay's Near Mint 30-day sold median confirms it. When the two agree,
     eBay is blended in, up to 50/50. The further apart they are, the less
     eBay counts. Past a 50% difference it isn't used at all, and the value is
     flagged as low-confidence. The weighting is continuous, so a value
     never jumps just because two sources crossed a threshold.
   - Other conditions (LP, MP, ...) and other variants are never used.
3. **Graded cards** without a manual entry use TCGGO's median only if it comes
   from at least 3 sales and the card's product has a single variant.
   Otherwise the value is left blank, with the reason.

**Details** on a collection card shows how each value was reached: the
method, confidence, each source's value, sale count and date, and whether it
was used, blended in or excluded (and why).

**Variants:** every PokeTrace card id is one variant. Search results, the Add
form and the collection show the variant, and warn when the same card also
comes as another variant. A card whose printing has other variants shows
**Confirm variant** until you confirm or correct it. Correcting it is booked
like a trade: the wrong variant leaves at its value and the right one joins
at its own. The performance chart shows no gain or loss from the
correction, and each card's price history stays a single variant. Old
snapshots are left untouched on the old card. Prices you entered by hand for
graded grades carry over, since they describe the card you own.

---

# 11. Current project structure

```text
pokemon-card-tracker/
├── .gitignore
├── README.md
├── supabase/
│   └── schema.sql
├── backend/
│   ├── .env.example
│   ├── requirements.txt
│   ├── app/
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── db.py
│   │   ├── main.py
│   │   ├── schemas.py
│   │   └── services/
│   │       ├── __init__.py
│   │       ├── poketrace.py
│   │       ├── state_store.py
│   │       ├── tcggo.py
│   │       ├── portfolio.py
│   │       ├── price_sync.py
│   │       └── valuation.py
└── frontend/
    ├── .env.example
    ├── index.html
    ├── package.json
    ├── vite.config.js
    └── src/
        ├── App.css
        ├── App.jsx
        ├── api.js
        └── main.jsx
```

---

# 12. Security notes

- Never commit `backend/.env`.
- Never put `SUPABASE_SECRET_KEY` in a Vite variable.
- Never put `POKETRACE_API_KEY` in the React frontend.
- All third-party API calls happen in FastAPI.
- The Supabase tables have Row Level Security enabled and no public policies.
- The backend's Supabase secret key is intentionally server-only.

---

# 13. What I would build next

The next version should add:

- card detail pages
- price-history charts from our own saved snapshots
- profit/loss vs purchase price
- collection sorting/filtering
- exact card-variant confirmation
- CSV import/export
- additional free/authorized pricing adapters
- sports-card support using the same collection and snapshot model
