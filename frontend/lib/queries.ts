/**
 * All Supabase queries used by pages live here.
 * Keeping them in one file means we can swap the data layer (e.g. move
 * behind a server-side API) without touching the page components.
 */

import { supabase } from "./supabase";

// --- Types -----------------------------------------------------------

export type Sentiment = "bullish" | "bearish" | "neutral" | "mixed";
export type ClaimType = "analyst_target" | "macro" | "stock_event";

export type SourceStats = {
  source: string;
  articles_total: number;
  articles_with_meta: number;
  avg_quality: number | null;
  claims_total: number;
  claims_per_article: number | null;
};

export type Article = {
  id: number;
  source: string;
  url: string;
  title: string;
  published_at: string | null;
  language: string;
};

export type TopTicker = {
  ticker: string;
  mentions: number;
  bull: number;
  bear: number;
  neutral: number;
  mixed: number;
  primary_count: number;
};

export type MentionGrowth = {
  ticker: string;
  mentions_24h: number;
  avg_daily_7d: number;
  growth_ratio: number | null;
};

export type TopTag = { sector?: string; tag?: string; mentions: number };

export type ArticleWithMeta = {
  id: number;
  source: string;
  url: string;
  title: string;
  published_at: string | null;
  meta_event_type: string | null;
  meta_sentiment: Sentiment | null;
};

/** Display list — current live sources (Reuters disabled, see backend/ingest/sources.py) */
export const SOURCES = [
  { id: "cnbc", name: "CNBC", language: "en" },
  { id: "yahoo_finance", name: "Yahoo Finance", language: "en" },
  { id: "yahoo_tw", name: "Yahoo Finance TW", language: "zh-TW" },
  { id: "ltn", name: "自由時報 商業", language: "zh-TW" },
] as const;

// --- Source stats ----------------------------------------------------

export async function getSourceStats(): Promise<SourceStats[]> {
  const { data, error } = await supabase
    .from("v_source_stats")
    .select("source, articles_total, articles_with_meta, avg_quality, claims_total, claims_per_article");
  if (error) throw error;
  return (data ?? []) as SourceStats[];
}

// --- Trending dashboards (last 7 days) ------------------------------

export async function getTopTickers(limit = 15): Promise<TopTicker[]> {
  const { data, error } = await supabase
    .from("v_top_tickers_7d")
    .select("ticker, mentions, bull, bear, neutral, mixed, primary_count")
    .limit(limit);
  if (error) throw error;
  return (data ?? []) as TopTicker[];
}

export async function getMentionGrowth(limit = 10): Promise<MentionGrowth[]> {
  const { data, error } = await supabase
    .from("v_ticker_mention_growth_24h")
    .select("ticker, mentions_24h, avg_daily_7d, growth_ratio")
    .limit(limit);
  if (error) throw error;
  return (data ?? []) as MentionGrowth[];
}

export async function getTopSectors(limit = 10): Promise<TopTag[]> {
  const { data, error } = await supabase
    .from("v_top_sectors_7d")
    .select("sector, mentions")
    .limit(limit);
  if (error) throw error;
  return (data ?? []) as TopTag[];
}

export async function getTopNarratives(limit = 10): Promise<TopTag[]> {
  const { data, error } = await supabase
    .from("v_top_narratives_7d")
    .select("tag, mentions")
    .limit(limit);
  if (error) throw error;
  return (data ?? []) as TopTag[];
}

// --- Articles --------------------------------------------------------

export async function getRecentArticles(opts: { source?: string; limit?: number } = {}): Promise<Article[]> {
  const limit = Math.min(opts.limit ?? 50, 200);
  let q = supabase
    .from("articles")
    .select("id, source, url, title, published_at, language")
    .order("published_at", { ascending: false, nullsFirst: false })
    .limit(limit);
  if (opts.source) q = q.eq("source", opts.source);
  const { data, error } = await q;
  if (error) throw error;
  return (data ?? []) as Article[];
}

/** Get articles mentioning a specific ticker, with their sentiment for that ticker. */
export async function getArticlesForTicker(ticker: string, limit = 50): Promise<
  Array<Article & { ticker_sentiment: Sentiment | null; is_primary: boolean }>
> {
  // Join article_tickers -> articles
  const { data, error } = await supabase
    .from("article_tickers")
    .select(
      `sentiment, is_primary,
       articles!inner ( id, source, url, title, published_at, language )`,
    )
    .eq("ticker", ticker)
    .order("articles(published_at)", { ascending: false, nullsFirst: false })
    .limit(limit);
  if (error) throw error;
  type Row = {
    sentiment: Sentiment | null;
    is_primary: boolean;
    articles: {
      id: number;
      source: string;
      url: string;
      title: string;
      published_at: string | null;
      language: string;
    };
  };
  return ((data ?? []) as unknown as Row[]).map((r) => ({
    ...r.articles,
    ticker_sentiment: r.sentiment,
    is_primary: r.is_primary,
  }));
}

/** Per-day mention counts for a ticker over the last N days. */
export async function getTickerTimeSeries(ticker: string, days = 30) {
  const { data, error } = await supabase
    .from("article_tickers")
    .select(
      `sentiment, articles!inner ( published_at )`,
    )
    .eq("ticker", ticker)
    .gte("articles.published_at", new Date(Date.now() - days * 86400_000).toISOString());
  if (error) throw error;

  type Row = { sentiment: Sentiment | null; articles: { published_at: string | null } };
  const buckets = new Map<string, { day: string; bull: number; bear: number; neu: number; mixed: number; total: number }>();
  for (const r of (data ?? []) as unknown as Row[]) {
    const ts = r.articles?.published_at;
    if (!ts) continue;
    const day = ts.slice(0, 10); // YYYY-MM-DD
    const b = buckets.get(day) ?? { day, bull: 0, bear: 0, neu: 0, mixed: 0, total: 0 };
    if (r.sentiment === "bullish") b.bull++;
    else if (r.sentiment === "bearish") b.bear++;
    else if (r.sentiment === "neutral") b.neu++;
    else if (r.sentiment === "mixed") b.mixed++;
    b.total++;
    buckets.set(day, b);
  }
  return Array.from(buckets.values()).sort((a, b) => a.day.localeCompare(b.day));
}
