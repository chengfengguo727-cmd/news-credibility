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

-- Views for the frontend dashboards ----------------------------------

-- Pipeline status (single row) — drives the top status bar on the
-- homepage. Cheap to compute (a handful of COUNTs / MAXes); refreshed
-- whenever the page revalidates.
CREATE OR REPLACE VIEW v_pipeline_status AS
SELECT
    (SELECT COUNT(*) FROM articles)                                  AS articles_total,
    (SELECT COUNT(*) FROM article_meta)                              AS articles_with_meta,
    (SELECT COUNT(*) FROM articles WHERE body IS NOT NULL)           AS articles_with_body,
    (SELECT COUNT(*) FROM articles
     WHERE body IS NOT NULL
       AND id NOT IN (SELECT article_id FROM article_meta))          AS pending_with_body,
    (SELECT MAX(fetched_at)   FROM articles)                         AS latest_ingest_at,
    (SELECT MAX(extracted_at) FROM article_meta)                     AS latest_extract_at,
    (SELECT MAX(published_at) FROM articles)                         AS latest_article_published_at,
    (SELECT MAX(a.published_at)
     FROM article_meta m JOIN articles a ON a.id = m.article_id)     AS latest_extracted_article_published_at
;


-- Top tickers by mentions in last 7 days, with sentiment breakdown
CREATE OR REPLACE VIEW v_top_tickers_7d AS
SELECT
    t.ticker,
    COUNT(*)                                     AS mentions,
    COUNT(*) FILTER (WHERE t.sentiment='bullish') AS bull,
    COUNT(*) FILTER (WHERE t.sentiment='bearish') AS bear,
    COUNT(*) FILTER (WHERE t.sentiment='neutral') AS neutral,
    COUNT(*) FILTER (WHERE t.sentiment='mixed')   AS mixed,
    COUNT(*) FILTER (WHERE t.is_primary)         AS primary_count
FROM article_tickers t
JOIN articles a ON a.id = t.article_id
WHERE a.published_at > now() - interval '14 days'
  AND t.ticker NOT LIKE '^%'   -- skip indices
GROUP BY t.ticker
ORDER BY mentions DESC, primary_count DESC;

-- Top sectors in last 7 days
CREATE OR REPLACE VIEW v_top_sectors_7d AS
SELECT s.sector, COUNT(*) AS mentions
FROM article_sectors s
JOIN articles a ON a.id = s.article_id
WHERE a.published_at > now() - interval '14 days'
GROUP BY s.sector
ORDER BY mentions DESC;

-- Top narrative tags in last 7 days
CREATE OR REPLACE VIEW v_top_narratives_7d AS
SELECT n.tag, COUNT(*) AS mentions
FROM article_narrative_tags n
JOIN articles a ON a.id = n.article_id
WHERE a.published_at > now() - interval '14 days'
GROUP BY n.tag
ORDER BY mentions DESC;

-- Per-source lifetime stats
CREATE OR REPLACE VIEW v_source_stats AS
SELECT
    a.source,
    COUNT(*)                                      AS articles_total,
    COUNT(m.article_id)                           AS articles_with_meta,
    ROUND(AVG(m.article_quality)::numeric, 2)     AS avg_quality,
    COUNT(c.id)                                   AS claims_total,
    ROUND(COUNT(c.id)::numeric / NULLIF(COUNT(*),0), 3) AS claims_per_article
FROM articles a
LEFT JOIN article_meta m ON m.article_id = a.id
LEFT JOIN claims c        ON c.article_id = a.id
GROUP BY a.source
ORDER BY articles_total DESC;

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
GRANT SELECT ON v_ticker_mention_growth_24h, v_top_tickers_7d, v_top_sectors_7d,
                 v_top_narratives_7d, v_source_stats, v_pipeline_status
    TO anon, authenticated;

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
