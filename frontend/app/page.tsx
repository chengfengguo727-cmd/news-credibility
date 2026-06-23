import {
  SOURCES,
  getMentionGrowth,
  getRecentArticles,
  getSourceStats,
  getTopNarratives,
  getTopSectors,
  getTopTickers,
} from "@/lib/queries";

export const revalidate = 60; // ISR: re-fetch at most once a minute

function pct(n: number, total: number) {
  return total > 0 ? Math.round((100 * n) / total) : 0;
}

function SentimentBar({ bull, bear, neu, mixed }: { bull: number; bear: number; neu: number; mixed: number }) {
  const total = bull + bear + neu + mixed;
  if (total === 0) return null;
  return (
    <div className="flex h-2 w-32 overflow-hidden rounded bg-neutral-200 dark:bg-neutral-800">
      <div style={{ width: `${pct(bull, total)}%` }} className="bg-emerald-500" title={`bullish ${bull}`} />
      <div style={{ width: `${pct(neu, total)}%` }} className="bg-neutral-400" title={`neutral ${neu}`} />
      <div style={{ width: `${pct(mixed, total)}%` }} className="bg-amber-400" title={`mixed ${mixed}`} />
      <div style={{ width: `${pct(bear, total)}%` }} className="bg-rose-500" title={`bearish ${bear}`} />
    </div>
  );
}

export default async function Page() {
  const [tickers, growth, sectors, narratives, sources, recent] = await Promise.all([
    getTopTickers(15).catch(() => []),
    getMentionGrowth(10).catch(() => []),
    getTopSectors(12).catch(() => []),
    getTopNarratives(12).catch(() => []),
    getSourceStats().catch(() => []),
    getRecentArticles({ limit: 10 }).catch(() => []),
  ]);

  return (
    <main className="space-y-12">
      <section>
        <header className="mb-3 flex items-baseline justify-between">
          <h2 className="text-lg font-medium">🔥 Trending tickers (14d)</h2>
          <span className="text-xs text-neutral-500">color: bullish · neutral · mixed · bearish</span>
        </header>
        {tickers.length === 0 ? (
          <p className="text-sm text-neutral-500">No data yet — run extract from backend/.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-neutral-500">
              <tr>
                <th className="py-1">Ticker</th>
                <th>Sentiment mix</th>
                <th className="text-right">Bull</th>
                <th className="text-right">Bear</th>
                <th className="text-right">Primary</th>
                <th className="text-right">Total</th>
              </tr>
            </thead>
            <tbody>
              {tickers.map((t) => (
                <tr key={t.ticker} className="border-t">
                  <td className="py-2">
                    <a className="font-mono font-semibold hover:underline" href={`/ticker/${t.ticker}`}>
                      {t.ticker}
                    </a>
                  </td>
                  <td><SentimentBar bull={t.bull} bear={t.bear} neu={t.neutral} mixed={t.mixed} /></td>
                  <td className="text-right text-emerald-600">{t.bull || ""}</td>
                  <td className="text-right text-rose-600">{t.bear || ""}</td>
                  <td className="text-right text-neutral-500">{t.primary_count}</td>
                  <td className="text-right font-medium">{t.mentions}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <h2 className="mb-3 text-lg font-medium">📈 Mention growth — last 24h vs 7-day baseline</h2>
        {growth.length === 0 ? (
          <p className="text-sm text-neutral-500">
            View is empty when 24h ingestion is sparse. The cron writes hourly; come back tomorrow if this is blank today.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-neutral-500">
              <tr>
                <th className="py-1">Ticker</th>
                <th className="text-right">24h mentions</th>
                <th className="text-right">7d daily avg</th>
                <th className="text-right">Growth</th>
              </tr>
            </thead>
            <tbody>
              {growth.map((g) => (
                <tr key={g.ticker} className="border-t">
                  <td className="py-2">
                    <a className="font-mono font-semibold hover:underline" href={`/ticker/${g.ticker}`}>
                      {g.ticker}
                    </a>
                  </td>
                  <td className="text-right">{g.mentions_24h}</td>
                  <td className="text-right text-neutral-500">{g.avg_daily_7d.toFixed(2)}</td>
                  <td className="text-right">
                    {g.growth_ratio == null ? (
                      <span className="rounded bg-blue-100 px-1.5 text-xs font-medium text-blue-700 dark:bg-blue-900 dark:text-blue-200">
                        NEW
                      </span>
                    ) : (
                      <span className={g.growth_ratio >= 2 ? "font-semibold text-emerald-600" : ""}>
                        {g.growth_ratio.toFixed(1)}×
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <div className="grid gap-8 md:grid-cols-2">
        <section>
          <h2 className="mb-3 text-lg font-medium">🏷️ Trending narratives (14d)</h2>
          <ul className="space-y-1 text-sm">
            {narratives.map((n) => (
              <li key={n.tag} className="flex justify-between border-t py-1.5">
                <span className="font-mono">{n.tag}</span>
                <span className="text-neutral-500">{n.mentions}</span>
              </li>
            ))}
            {narratives.length === 0 && (
              <li className="text-sm text-neutral-500">No data yet.</li>
            )}
          </ul>
        </section>

        <section>
          <h2 className="mb-3 text-lg font-medium">🏢 Hot sectors (14d)</h2>
          <ul className="space-y-1 text-sm">
            {sectors.map((s) => (
              <li key={s.sector} className="flex justify-between border-t py-1.5">
                <span className="font-mono">{s.sector}</span>
                <span className="text-neutral-500">{s.mentions}</span>
              </li>
            ))}
            {sectors.length === 0 && (
              <li className="text-sm text-neutral-500">No data yet.</li>
            )}
          </ul>
        </section>
      </div>

      <section>
        <h2 className="mb-3 text-lg font-medium">📊 Per-source coverage</h2>
        {sources.length === 0 ? (
          <p className="text-sm text-neutral-500">No data yet.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-neutral-500">
              <tr>
                <th className="py-1">Source</th>
                <th className="text-right">Articles</th>
                <th className="text-right">With meta</th>
                <th className="text-right">Quality</th>
                <th className="text-right">Claims</th>
                <th className="text-right">Claims/article</th>
              </tr>
            </thead>
            <tbody>
              {sources.map((s) => (
                <tr key={s.source} className="border-t">
                  <td className="py-2 font-medium">{s.source}</td>
                  <td className="text-right">{s.articles_total.toLocaleString()}</td>
                  <td className="text-right text-neutral-500">{s.articles_with_meta.toLocaleString()}</td>
                  <td className="text-right">{s.avg_quality?.toFixed(2) ?? "—"}</td>
                  <td className="text-right">{s.claims_total}</td>
                  <td className="text-right">{s.claims_per_article?.toFixed(2) ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <h2 className="mb-3 text-lg font-medium">🗞️ Recently ingested</h2>
        {recent.length === 0 ? (
          <p className="text-sm text-neutral-500">
            Run <code className="rounded bg-neutral-200 px-1 dark:bg-neutral-800">python -m tasks.run ingest</code>{" "}
            to pull the first batch.
          </p>
        ) : (
          <ul className="text-sm">
            {recent.map((a) => (
              <li key={a.id} className="border-t py-2">
                <a href={a.url} target="_blank" rel="noopener noreferrer" className="hover:underline">
                  {a.title}
                </a>{" "}
                <span className="text-neutral-500">
                  — {a.source}
                  {a.published_at ? ` · ${new Date(a.published_at).toLocaleString()}` : ""}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="border-t pt-6 text-xs text-neutral-500">
        Tracked sources: {SOURCES.map((s) => s.name).join(" · ")} · refreshed every minute
      </section>
    </main>
  );
}
