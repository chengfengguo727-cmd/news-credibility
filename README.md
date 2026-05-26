# News Credibility Tracker

[![Live](https://img.shields.io/badge/live-news--credibility.vercel.app-black?logo=vercel)](https://news-credibility.vercel.app)
[![Ingest](https://github.com/chengfengguo727-cmd/news-credibility/actions/workflows/ingest.yml/badge.svg)](https://github.com/chengfengguo727-cmd/news-credibility/actions/workflows/ingest.yml)

MVP for tracking how well financial news sources predict the market.
See [`plans/reuters-bloomberg-prancy-widget.md`](../../Users/hfkuo/.claude/plans/reuters-bloomberg-prancy-widget.md) for the full design.

## Architecture

```
Browser ── HTTPS ──► Vercel (Next.js)
                       │
                       ▼ supabase-js with anon key
                    Supabase (Postgres + PostgREST + RLS)
                       ▲
                       │ DATABASE_URL (postgres superuser)
                       │
       ┌───────────────┴───────────────┐
       │                               │
GitHub Actions cron            Local machine
(ingest, hourly, free)         (extract, on-demand)
python -m tasks.run ingest     python -m tasks.run extract
                               └─ shells out to `claude` CLI
                                  (OAuth via Claude subscription —
                                   no API key needed)
```

- **No backend server.** Frontend reads Supabase directly via the anon
  key; RLS policies only permit `SELECT`. Writes happen exclusively
  from the Python pipeline.
- **Ingest runs on GitHub Actions** — pure HTTP/SQL, no auth needed.
- **Extract runs locally** — uses the `claude` CLI's OAuth session so
  it charges against your Claude Pro/Max subscription instead of a
  pay-as-you-go API key. Trade-off: needs a machine with `claude`
  installed + logged in (no GitHub Actions, no Vercel cron).

```
backend/   Python — ingest / extract / validate / score (CLI via Typer)
frontend/  Next.js 14 — public ranking site (talks to Supabase directly)
.github/   Hourly ingest cron
```

## Status

**Week 1 — done:**
- Idempotent Supabase schema with RLS + public-read policies
- RSS ingest (`backend/ingest/rss.py`) for Reuters / CNBC / Yahoo Finance
- Bayesian scoring helper + unit tests
- Typer CLI (`python -m tasks.run ingest|extract`)
- GitHub Actions hourly ingest cron
- Next.js frontend on Vercel reading Supabase via supabase-js

**Week 2 — done:**
- Claim extractor (`backend/extract/claim_extractor.py`) that shells out
  to the `claude` CLI with `--json-schema` for structured output
- Auth via Claude Code OAuth (no `ANTHROPIC_API_KEY` required)

**TODO:**
- Week 3: yfinance / FRED / FinMind adapters
- Week 4: Scoring task wiring (Bayesian credibility update)
- Week 5+: more sources, frontend drilldown, public launch

## Setup (Windows / PowerShell)

### 1. Create the Supabase project

1. <https://supabase.com/dashboard> → **New project**
2. Name `news-credibility`, generate a database password (save it!),
   region `Northeast Asia (Tokyo)` or `Southeast Asia (Singapore)`.
3. On the Security step:
   - **Enable Data API** → **ON**
   - **Automatically expose new tables** → OFF
   - **Enable automatic RLS** → ON
4. Wait ~2 min for the project to provision.

### 2. Apply the schema

1. Left sidebar → **SQL Editor → New query**.
2. Paste the entire contents of [`backend/db/schema.sql`](backend/db/schema.sql).
3. **Run**. Expect `Success. No rows returned.` It's idempotent, so
   re-running is safe.
4. **Table Editor** → confirm the four tables exist: `articles`,
   `claims`, `claim_outcomes`, `source_scores`. Each should show a
   green RLS shield icon.

### 3. Grab the keys

**Connection string (for the Python pipeline):** click the green
**Connect** button at the top of the project page. In the modal, pick
the **Session pooler** tab (works from anywhere — laptop, GitHub
Actions, Vercel) or **Direct connection** for laptop-only. Replace
`[YOUR-PASSWORD]` with the DB password from Step 1, and rewrite the
scheme `postgresql://` → `postgresql+psycopg://` for SQLAlchemy.

**Frontend keys:** in the same **Connect** modal, **Framework** tab,
the page literally prints two env lines ready to paste:

```
NEXT_PUBLIC_SUPABASE_URL=https://<ref>.supabase.co
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=sb_publishable_...
```

The `publishable` key (Supabase's new name for what used to be called
the `anon` key) is safe to expose — it can only do what RLS policies
allow. Do **not** use the `service_role` / `secret` key anywhere in
the frontend or commit it; it bypasses RLS.

### 4. Backend (pipeline)

```powershell
cd D:\claude_code\news-credibility\backend

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item .env.example .env
# Edit .env, fill:
#   DATABASE_URL=postgresql+psycopg://postgres:YOUR_PW@db.YOUR_REF.supabase.co:5432/postgres
# ANTHROPIC_API_KEY is NOT required — extract uses the `claude` CLI's OAuth

# First ingest — pulls latest items from the three RSS feeds
python -m tasks.run ingest

# Extract claims — requires `claude` CLI installed and logged in.
# Run `claude` once interactively first to complete the OAuth login.
python -m tasks.run extract --limit 50

# Run tests
pytest
```

After `ingest` finishes, open the **Table Editor → articles** in
Supabase. You should see ~50–100 freshly inserted rows. After
`extract`, the `claims` table will have any forward-looking predictions
the model identified.

### 5. Frontend

```powershell
cd D:\claude_code\news-credibility\frontend
npm.cmd install

Copy-Item .env.local.example .env.local
# Edit .env.local, fill:
#   NEXT_PUBLIC_SUPABASE_URL=https://YOURREF.supabase.co
#   NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJ...

npm.cmd run dev
# open http://localhost:3000
```

Expected on the page:
- **Credibility ranking** → "No scores yet…" placeholder (normal until
  Week 4 scoring lands).
- **Tracked sources** → Reuters / CNBC / Yahoo Finance.
- **Recently ingested** → 10 most recent articles from Step 4.

### 6a. Hourly ingest cron (free, via GitHub Actions)

```powershell
cd D:\claude_code\news-credibility
git init
git add .
git commit -m "Week 1: skeleton + Supabase + RSS ingest"
```

Create a **Private** repo at <https://github.com/new>, then:

```powershell
git remote add origin https://github.com/<you>/news-credibility.git
git branch -M main
git push -u origin main
```

On GitHub: **Settings → Secrets and variables → Actions → New repository secret**

| Name | Value |
|---|---|
| `DATABASE_URL` | Session-pooler URL with `postgresql+psycopg://` scheme |
| `RSS_USER_AGENT` | `NewsCredibilityBot/0.1 (+https://github.com/<you>/news-credibility)` |

Then **Actions → Hourly ingest → Run workflow** to verify. After the
green checkmark, it runs every hour automatically.

> ⚠️ The workflow only runs **ingest**. Extract requires the `claude`
> CLI's OAuth session which only exists locally — Actions can't run it.

### 6b. Local extract (manual, or Windows Task Scheduler)

Once a day or so, run from your dev machine:

```powershell
cd D:\claude_code\news-credibility\backend
.\.venv\Scripts\Activate.ps1
python -m tasks.run extract --limit 50
```

The first article in a batch costs ~$0.05 against your Claude
subscription (cache miss on the system prompt); subsequent articles
within the same 5-minute window drop to ~$0.005 thanks to Claude
Code's automatic prompt caching.

**Want it scheduled?** Windows Task Scheduler can run it hourly when
your machine is on:

1. Open Task Scheduler → **Create Basic Task…**
2. Trigger: **Daily**, repeat **every 1 hour** for 1 day, indefinitely
3. Action: **Start a program**
   - Program: `powershell.exe`
   - Arguments: `-NoProfile -ExecutionPolicy Bypass -Command "cd D:\claude_code\news-credibility\backend; .\.venv\Scripts\Activate.ps1; python -m tasks.run extract --limit 50 *>> D:\claude_code\news-credibility\extract.log"`
4. Conditions tab → uncheck *"Start only if computer is on AC power"*
   if you want it to run on battery too.

The task only succeeds while you're logged in (your `claude` OAuth
session is per-user). That's fine for personal/MVP use.

### 7. Deploy frontend to Vercel

1. <https://vercel.com/new> → import the GitHub repo.
2. **Root Directory** → `frontend`.
3. **Environment Variables** → add `NEXT_PUBLIC_SUPABASE_URL` and
   `NEXT_PUBLIC_SUPABASE_ANON_KEY` (same values as `.env.local`).
4. Deploy. The build serves the same page you saw locally, backed by
   the live Supabase project.

## Data & legal notes

- Sources used are free and public: RSS feeds, GDELT, yfinance, FRED,
  SEC EDGAR, FinMind.
- We do **not** bypass paywalls. Paywalled articles are scored on
  headline + RSS summary only, marked as such in the UI.
- Article URLs and short quotes (fair use) are stored; we never
  republish full body text publicly.
- Each fetcher sets `RSS_USER_AGENT` and respects a 1 req/sec rate
  limit (`RSS_RATE_LIMIT_SEC`).
