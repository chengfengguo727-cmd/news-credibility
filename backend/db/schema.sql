-- News Source Credibility Tracker — Supabase schema bootstrap
-- Paste into Supabase Dashboard → SQL Editor → Run.
-- Safe to re-run: idempotent.
--
-- Architecture (Option B):
--   - Frontend (Next.js) reads via supabase-js with the `anon` key,
--     hitting Supabase's auto-generated PostgREST API.
--   - Pipeline (GitHub Actions) writes via DATABASE_URL using the
--     `postgres` superuser, which bypasses RLS.
--   - All four tables hold *public* data, so SELECT is wide open;
--     no INSERT/UPDATE/DELETE policy → writes from anon are blocked.

-- Enums --------------------------------------------------------------
DO $$ BEGIN
    CREATE TYPE claim_type AS ENUM ('analyst_target', 'macro', 'stock_event');
EXCEPTION WHEN duplicate_object THEN null; END $$;

DO $$ BEGIN
    CREATE TYPE outcome_t AS ENUM ('pending', 'hit', 'partial', 'miss');
EXCEPTION WHEN duplicate_object THEN null; END $$;

DO $$ BEGIN
    CREATE TYPE sentiment_t AS ENUM ('bullish', 'bearish', 'neutral', 'mixed');
EXCEPTION WHEN duplicate_object THEN null; END $$;

DO $$ BEGIN
    CREATE TYPE event_type_t AS ENUM (
        'earnings', 'm_and_a', 'regulatory', 'exec_change',
        'product_launch', 'macro_release', 'market_summary',
        'opinion', 'lawsuit', 'guidance', 'other'
    );
EXCEPTION WHEN duplicate_object THEN null; END $$;

-- Tables -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS articles (
    id              BIGSERIAL PRIMARY KEY,
    source          VARCHAR(64) NOT NULL,
    url             VARCHAR(2048) NOT NULL UNIQUE,
    title           VARCHAR(1024) NOT NULL,
    body            TEXT,
    summary         TEXT,
    published_at    TIMESTAMPTZ,
    language        VARCHAR(8) NOT NULL DEFAULT 'en',
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    extracted       BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS idx_articles_source ON articles (source);
CREATE INDEX IF NOT EXISTS idx_articles_published_at ON articles (published_at);
CREATE INDEX IF NOT EXISTS idx_articles_extracted ON articles (extracted) WHERE extracted = false;

CREATE TABLE IF NOT EXISTS claims (
    id              BIGSERIAL PRIMARY KEY,
    article_id      BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    type            claim_type NOT NULL,
    ticker          VARCHAR(16),
    topic           VARCHAR(64),
    predicted_value DOUBLE PRECISION,
    predicted_text  VARCHAR(256),
    deadline        TIMESTAMPTZ,
    raw_text        TEXT NOT NULL,
    llm_confidence  DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_claims_article ON claims (article_id);
CREATE INDEX IF NOT EXISTS idx_claims_type ON claims (type);
CREATE INDEX IF NOT EXISTS idx_claims_ticker ON claims (ticker);
CREATE INDEX IF NOT EXISTS idx_claims_deadline ON claims (deadline);

CREATE TABLE IF NOT EXISTS claim_outcomes (
    id              BIGSERIAL PRIMARY KEY,
    claim_id        BIGINT NOT NULL UNIQUE REFERENCES claims(id) ON DELETE CASCADE,
    actual_value    DOUBLE PRECISION,
    outcome         outcome_t NOT NULL DEFAULT 'pending',
    resolved_at     TIMESTAMPTZ,
    notes           TEXT
);
CREATE INDEX IF NOT EXISTS idx_outcomes_outcome ON claim_outcomes (outcome);

-- Metadata extracted alongside claims (one row per processed article) -
CREATE TABLE IF NOT EXISTS article_meta (
    article_id        BIGINT PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
    overall_sentiment sentiment_t,
    event_type        event_type_t,
    article_quality   DOUBLE PRECISION,   -- 0..1, 1 = original reporting, 0 = bot rephrase
    is_breaking       BOOLEAN,
    extracted_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_meta_sentiment ON article_meta (overall_sentiment);
CREATE INDEX IF NOT EXISTS idx_meta_event_type ON article_meta (event_type);

CREATE TABLE IF NOT EXISTS article_tickers (
    id          BIGSERIAL PRIMARY KEY,
    article_id  BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    ticker      VARCHAR(16) NOT NULL,
    sentiment   sentiment_t,
    is_primary  BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS idx_at_ticker  ON article_tickers (ticker);
CREATE INDEX IF NOT EXISTS idx_at_article ON article_tickers (article_id);

CREATE TABLE IF NOT EXISTS article_sectors (
    id          BIGSERIAL PRIMARY KEY,
    article_id  BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    sector      VARCHAR(64) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_as_sector  ON article_sectors (sector);
CREATE INDEX IF NOT EXISTS idx_as_article ON article_sectors (article_id);

CREATE TABLE IF NOT EXISTS article_narrative_tags (
    id          BIGSERIAL PRIMARY KEY,
    article_id  BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    tag         VARCHAR(64) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ant_tag     ON article_narrative_tags (tag);
CREATE INDEX IF NOT EXISTS idx_ant_article ON article_narrative_tags (article_id);

CREATE TABLE IF NOT EXISTS source_scores (
    id              BIGSERIAL PRIMARY KEY,
    source          VARCHAR(64) NOT NULL,
    type            claim_type NOT NULL,
    alpha           DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    beta            DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    score           DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    ci_low          DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    ci_high         DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    n               INTEGER NOT NULL DEFAULT 0,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_source_type UNIQUE (source, type)
);
CREATE INDEX IF NOT EXISTS idx_scores_source ON source_scores (source);

-- View: 24h ticker mention growth rate vs 7-day baseline -------------
-- Use case: "which companies has the news cycle suddenly latched onto?"
-- Excludes tickers with <3 mentions in 24h (noise floor) and reports both
-- the raw 24h count and the ratio against the 7-day daily average.
CREATE OR REPLACE VIEW v_ticker_mention_growth_24h AS
WITH recent AS (
    SELECT at.ticker, COUNT(*) AS mentions_24h
    FROM article_tickers at
    JOIN articles a ON a.id = at.article_id
    WHERE a.published_at > now() - interval '24 hours'
    GROUP BY at.ticker
),
baseline AS (
    -- 7-day average daily rate, computed from the 7 days BEFORE the last 24h.
    SELECT at.ticker, COUNT(*)::float / 7 AS avg_daily_7d
    FROM article_tickers at
    JOIN articles a ON a.id = at.article_id
    WHERE a.published_at BETWEEN now() - interval '8 days'
                              AND now() - interval '24 hours'
    GROUP BY at.ticker
)
SELECT
    r.ticker,
    r.mentions_24h,
    COALESCE(b.avg_daily_7d, 0)        AS avg_daily_7d,
    CASE
        WHEN COALESCE(b.avg_daily_7d, 0) = 0 THEN NULL  -- new ticker, no baseline
        ELSE r.mentions_24h / b.avg_daily_7d
    END AS growth_ratio
FROM recent r
LEFT JOIN baseline b USING (ticker)
WHERE r.mentions_24h >= 3
ORDER BY growth_ratio DESC NULLS LAST, mentions_24h DESC;

-- Table-level GRANTs -------------------------------------------------
-- Required even with RLS: PostgREST checks GRANT first, then policy.
GRANT SELECT ON articles, claims, claim_outcomes, source_scores TO anon, authenticated;
GRANT SELECT ON article_meta, article_tickers, article_sectors, article_narrative_tags
    TO anon, authenticated;
GRANT SELECT ON v_ticker_mention_growth_24h TO anon, authenticated;

-- Row-Level Security -------------------------------------------------
ALTER TABLE articles       ENABLE ROW LEVEL SECURITY;
ALTER TABLE claims         ENABLE ROW LEVEL SECURITY;
ALTER TABLE claim_outcomes ENABLE ROW LEVEL SECURITY;
ALTER TABLE source_scores  ENABLE ROW LEVEL SECURITY;

-- Policies (drop-then-create so re-runs are clean) -------------------
DROP POLICY IF EXISTS "public read"  ON articles;
DROP POLICY IF EXISTS "public read"  ON claims;
DROP POLICY IF EXISTS "public read"  ON claim_outcomes;
DROP POLICY IF EXISTS "public read"  ON source_scores;

CREATE POLICY "public read" ON articles
    FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY "public read" ON claims
    FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY "public read" ON claim_outcomes
    FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY "public read" ON source_scores
    FOR SELECT TO anon, authenticated USING (true);

ALTER TABLE article_meta            ENABLE ROW LEVEL SECURITY;
ALTER TABLE article_tickers         ENABLE ROW LEVEL SECURITY;
ALTER TABLE article_sectors         ENABLE ROW LEVEL SECURITY;
ALTER TABLE article_narrative_tags  ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "public read" ON article_meta;
DROP POLICY IF EXISTS "public read" ON article_tickers;
DROP POLICY IF EXISTS "public read" ON article_sectors;
DROP POLICY IF EXISTS "public read" ON article_narrative_tags;
CREATE POLICY "public read" ON article_meta            FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY "public read" ON article_tickers         FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY "public read" ON article_sectors         FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY "public read" ON article_narrative_tags  FOR SELECT TO anon, authenticated USING (true);

-- No INSERT/UPDATE/DELETE policies → all writes from anon/authenticated
-- are blocked. The Python pipeline uses the `postgres` superuser via
-- DATABASE_URL, which bypasses RLS entirely.
