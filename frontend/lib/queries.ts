/**
 * All Supabase queries used by pages live here.
 * Keeping them in one file means we can swap the data layer (e.g. move
 * behind a server-side API) without touching the page components.
 */

import { supabase } from "./supabase";

export type ClaimType = "analyst_target" | "macro" | "stock_event";

export type SourceScore = {
  source: string;
  type: ClaimType;
  score: number;
  ci_low: number;
  ci_high: number;
  n: number;
  updated_at: string;
};

export type Article = {
  id: number;
  source: string;
  url: string;
  title: string;
  published_at: string | null;
  language: string;
};

/** Hardcoded display list — sources are configured in backend/ingest/sources.py */
export const SOURCES = [
  { id: "reuters", name: "Reuters", language: "en" },
  { id: "cnbc", name: "CNBC", language: "en" },
  { id: "yahoo_finance", name: "Yahoo Finance", language: "en" },
] as const;

export async function getScores(type?: ClaimType): Promise<SourceScore[]> {
  let q = supabase
    .from("source_scores")
    .select("source, type, score, ci_low, ci_high, n, updated_at")
    .order("score", { ascending: false });
  if (type) q = q.eq("type", type);
  const { data, error } = await q;
  if (error) throw error;
  return (data ?? []) as SourceScore[];
}

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
