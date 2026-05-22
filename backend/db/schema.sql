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

-- Table-level GRANTs -------------------------------------------------
-- Required even with RLS: PostgREST checks GRANT first, then policy.
GRANT SELECT ON articles, claims, claim_outcomes, source_scores TO anon, authenticated;

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

-- No INSERT/UPDATE/DELETE policies → all writes from anon/authenticated
-- are blocked. The Python pipeline uses the `postgres` superuser via
-- DATABASE_URL, which bypasses RLS entirely.
