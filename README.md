# Pokémon Card Market Tracker

A free-first personal Pokémon card collection tracker built with:

- React + Vite + JavaScript
- Python + FastAPI
- Supabase PostgreSQL
- PokeTrace Free API for raw US market pricing

## What this first version does

- Search Pokémon cards.
- Add raw or PSA 7/8/9/10 copies to your collection.
- Automatically refresh raw pricing from:
  - eBay raw sold-sale averages returned by PokeTrace.
  - TCGPlayer raw market data returned by PokeTrace.
- Store every price refresh as a historical snapshot.
- Calculate a current market estimate from the newest value from each source.
- Manually add PSA 7–10 values from legitimate public comps without scraping.
- Calculate your known collection value from the grade you actually own.
- Refresh one card or your entire collection.

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
Refresh All endpoint deliberately waits between requests.

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

# 9. Refresh prices

## One card

Click:

```text
Refresh raw
```

beside a collection card.

## Entire collection

Click:

```text
Refresh All Raw Prices
```

The backend waits 2.1 seconds between PokeTrace calls so the free burst limit
is respected.

The endpoint also caps a single full refresh at 220 unique cards so you do not
accidentally consume the entire 250-request daily free allowance.

---

# 10. Refresh from a command

Keep the FastAPI backend running.

From the project root, with the Python virtual environment active:

```powershell
python .\backend\scripts\refresh_all.py
```

Optional alternate backend URL:

```powershell
$env:CARD_TRACKER_API_URL="http://localhost:8000"
python .\backend\scripts\refresh_all.py
```

---

# 11. How the valuation works

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

# 12. Current project structure

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
│   │       └── valuation.py
│   └── scripts/
│       └── refresh_all.py
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

# 13. Security notes

- Never commit `backend/.env`.
- Never put `SUPABASE_SECRET_KEY` in a Vite variable.
- Never put `POKETRACE_API_KEY` in the React frontend.
- All third-party API calls happen in FastAPI.
- The Supabase tables have Row Level Security enabled and no public policies.
- The backend's Supabase secret key is intentionally server-only.

---

# 14. What I would build next

The next version should add:

- card detail pages
- price-history charts from our own saved snapshots
- profit/loss vs purchase price
- collection sorting/filtering
- exact card-variant confirmation
- CSV import/export
- automatic scheduled refresh after the app is deployed
- additional free/authorized pricing adapters
- sports-card support using the same collection and snapshot model
