# Pokémon Card Market Tracker

A free-first personal Pokémon card collection tracker built with:

- React + Vite + JavaScript
- Python + FastAPI
- Supabase PostgreSQL
- PokeTrace Free API for raw US market pricing

## What this first version does

- Search Pokémon cards.
- Add raw or PSA 7/8/9/10 copies to your collection.
- Pull raw pricing automatically twice a day (once AM, once PM) from:
  - eBay raw sold-sale averages returned by PokeTrace.
  - TCGPlayer raw market data returned by PokeTrace.
- Store every price pull as a historical snapshot.
- Calculate a current market estimate from the newest value from each source.
- Manually add PSA 7–10 values from legitimate public comps without scraping.
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
   - PSA 7
   - PSA 8
   - PSA 9
   - PSA 10
6. Enter quantity.
7. Optionally enter what you paid.
8. Click **Add Card**.

When a card is added, the backend immediately asks PokeTrace for current raw
data and stores separate source snapshots.

---

# 8. Add PSA 7–10 values for free

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
9. Enter the values for PSA 7, PSA 8, PSA 9 and PSA 10.
10. Click **Save PSA Values**.

If you later add a second legitimate source, use a different source name.
The app will calculate the median of the newest value from each source.

---

# 9. How prices refresh

Market prices are pulled **twice a day** — once in the AM and once in the PM —
by a scheduler that runs inside the FastAPI backend
(`backend/app/services/price_sync.py`):

- **Raw** prices come from PokeTrace.
- **Graded** (PSA 7–10) prices come from TCGGO's eBay sold medians, and are
  only pulled when `RAPIDAPI_KEY` is set in `backend/.env`. Each value is the
  median of up to the last 5 eBay sales for that grade, in USD. Cards with no
  recent graded sales get nothing, so manual PSA entry still matters.

Each source is tracked separately and runs at most once per slot. Nothing
else pulls prices: the
**Refresh Prices** button only re-reads what is already stored in your
database, and adding a card does not trigger a pull (its price appears at the
next scheduled pull).

Default times are 8:00 AM and 8:00 PM (server-local). Change them in
`backend/.env`:

```text
REFRESH_AM_TIME=08:00
REFRESH_PM_TIME=20:00
```

Details:

- The pull runs only while the backend is running. If the backend was off at
  a scheduled time, it catches up once on startup, then resumes the schedule.
- Which slot last ran is saved in `backend/.price_sync_state.json`, so
  restarts (including `uvicorn --reload`) never trigger a second pull for the
  same slot.
- A failed pull is not retried until the next slot. If PokeTrace reports a
  rate limit, the pull stops immediately instead of spending more requests.
- The raw pull waits 2.1 seconds between calls (free burst limit) and is
  capped at 100 unique cards per pull so two pulls stay under PokeTrace's
  250-request daily allowance.
- The graded pull is capped at 40 cards per pull. TCGGO's free plan is a hard
  100 requests/day, and a card costs two requests the first time it is seen
  (id lookup + prices). Resolved ids are cached in `backend/.tcggo_ids.json`,
  so later pulls cost one request per card.
- Searching is still live: each search is one PokeTrace request.

---

# 10. How the valuation works

For each grade, the app looks at the newest saved value from each source.

Example raw snapshots:

```text
eBay raw sold average (via PokeTrace)    $285
TCGPlayer raw market (via PokeTrace)     $299
```

The current estimate is the median:

```text
$292
```

If you eventually enter three independent PSA 10 sources:

```text
PSA CardFacts              $300
Source B                   $287
Source C                   $295
```

the estimate is:

```text
$295
```

Using only the newest value per source prevents one source from receiving more
weight merely because it was refreshed more often.

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
