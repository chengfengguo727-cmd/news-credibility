import { notFound } from "next/navigation";

import { getArticlesForTicker, getTickerTimeSeries } from "@/lib/queries";

export const revalidate = 60;

const SENTIMENT_DOT = {
  bullish: "bg-emerald-500",
  bearish: "bg-rose-500",
  neutral: "bg-neutral-400",
  mixed: "bg-amber-400",
} as const;

export default async function Page({ params }: { params: Promise<{ symbol: string }> }) {
  const { symbol: raw } = await params;
  const symbol = decodeURIComponent(raw).toUpperCase();
  if (!symbol || symbol.length > 16) notFound();

  const [series, articles] = await Promise.all([
    getTickerTimeSeries(symbol, 30).catch(() => []),
    getArticlesForTicker(symbol, 50).catch(() => []),
  ]);

  if (articles.length === 0) {
    return (
      <main>
        <h2 className="text-2xl font-semibold tracking-tight">{symbol}</h2>
        <p className="mt-4 text-sm text-neutral-500">
          No mentions yet in our corpus. Try a different ticker, or wait for the
          next ingest cycle.
        </p>
        <p className="mt-4 text-sm">
          <a href="/" className="text-blue-600 hover:underline">← Back to dashboard</a>
        </p>
      </main>
    );
  }

  const totalMentions = series.reduce((a, b) => a + b.total, 0);
  const bull = series.reduce((a, b) => a + b.bull, 0);
  const bear = series.reduce((a, b) => a + b.bear, 0);
  const neu = series.reduce((a, b) => a + b.neu, 0);
  const mixed = series.reduce((a, b) => a + b.mixed, 0);
  const sentimentSkew = bull - bear;

  // Compute simple max for the bar chart
  const maxDaily = Math.max(1, ...series.map((s) => s.total));

  return (
    <main className="space-y-8">
      <header>
        <div className="flex items-baseline gap-3">
          <h2 className="font-mono text-3xl font-bold tracking-tight">{symbol}</h2>
          <span
            className={`rounded px-2 py-0.5 text-sm font-medium ${
              sentimentSkew > 0
                ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
                : sentimentSkew < 0
                ? "bg-rose-100 text-rose-700 dark:bg-rose-950 dark:text-rose-300"
                : "bg-neutral-100 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300"
            }`}
          >
            {sentimentSkew > 0 ? `+${sentimentSkew}` : sentimentSkew} net sentiment
          </span>
        </div>
        <p className="mt-2 text-sm text-neutral-500">
          {totalMentions} mentions over the last 30 days · {bull} bullish · {bear} bearish · {neu} neutral · {mixed} mixed
        </p>
      </header>

      <section>
        <h3 className="mb-2 text-sm font-medium text-neutral-600 dark:text-neutral-400">
          Daily mentions (30d) — colored by sentiment
        </h3>
        <div className="flex h-32 items-end gap-px">
          {series.map((d) => {
            const h = (d.total / maxDaily) * 100;
            return (
              <div
                key={d.day}
                className="group flex flex-1 flex-col-reverse"
                style={{ height: "100%" }}
                title={`${d.day} — ${d.total} mentions (${d.bull}↑ ${d.bear}↓ ${d.neu}· ${d.mixed}~)`}
              >
                {d.bear > 0 && (
                  <div className="bg-rose-500" style={{ height: `${(d.bear / d.total) * h}%` }} />
                )}
                {d.mixed > 0 && (
                  <div className="bg-amber-400" style={{ height: `${(d.mixed / d.total) * h}%` }} />
                )}
                {d.neu > 0 && (
                  <div className="bg-neutral-400" style={{ height: `${(d.neu / d.total) * h}%` }} />
                )}
                {d.bull > 0 && (
                  <div className="bg-emerald-500" style={{ height: `${(d.bull / d.total) * h}%` }} />
                )}
              </div>
            );
          })}
        </div>
        {series.length > 0 && (
          <div className="mt-1 flex justify-between text-xs text-neutral-500">
            <span>{series[0].day}</span>
            <span>{series[series.length - 1].day}</span>
          </div>
        )}
      </section>

      <section>
        <h3 className="mb-3 text-sm font-medium text-neutral-600 dark:text-neutral-400">
          Recent articles mentioning {symbol}
        </h3>
        <ul>
          {articles.map((a) => (
            <li key={a.id} className="border-t py-2 text-sm">
              <div className="flex items-center gap-2">
                <span
                  className={`inline-block h-2 w-2 rounded-full ${
                    a.ticker_sentiment ? SENTIMENT_DOT[a.ticker_sentiment] : "bg-neutral-300"
                  }`}
                  title={a.ticker_sentiment ?? "unknown"}
                />
                {a.is_primary && (
                  <span className="rounded bg-blue-100 px-1 text-xs font-medium text-blue-700 dark:bg-blue-950 dark:text-blue-300">
                    primary
                  </span>
                )}
                <a href={a.url} target="_blank" rel="noopener noreferrer" className="hover:underline">
                  {a.title}
                </a>
              </div>
              <div className="ml-4 text-xs text-neutral-500">
                {a.source}
                {a.published_at ? ` · ${new Date(a.published_at).toLocaleString()}` : ""}
              </div>
            </li>
          ))}
        </ul>
      </section>

      <p className="text-sm">
        <a href="/" className="text-blue-600 hover:underline">← Back to dashboard</a>
      </p>
    </main>
  );
}
