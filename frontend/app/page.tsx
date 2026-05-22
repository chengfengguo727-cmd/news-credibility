import { SOURCES, getRecentArticles, getScores } from "@/lib/queries";

export const revalidate = 60; // ISR: re-fetch from Supabase at most once a minute

export default async function Page() {
  const [scores, recent] = await Promise.all([
    getScores().catch(() => []),
    getRecentArticles({ limit: 10 }).catch(() => []),
  ]);

  return (
    <main className="space-y-10">
      <section>
        <h2 className="mb-2 text-lg font-medium">Credibility ranking</h2>
        <p className="mb-4 text-sm text-neutral-500">
          Scored against real market outcomes. Sources with fewer than 30 resolved
          claims are marked <span className="font-mono">n&lt;30</span> — treat as
          provisional.
        </p>
        {scores.length === 0 ? (
          <div className="rounded-md border border-dashed p-6 text-sm text-neutral-500">
            No scores yet — the pipeline needs to run for at least one full validation
            cycle before rankings appear here. Check back after the first batch of
            claims resolves.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-neutral-500">
              <tr>
                <th className="py-2">Source</th>
                <th>Type</th>
                <th>Score</th>
                <th>95% CI</th>
                <th>n</th>
              </tr>
            </thead>
            <tbody>
              {scores.map((s) => (
                <tr key={`${s.source}-${s.type}`} className="border-t">
                  <td className="py-2 font-medium">{s.source}</td>
                  <td className="text-neutral-500">{s.type}</td>
                  <td>{(s.score * 100).toFixed(1)}%</td>
                  <td className="text-neutral-500">
                    [{(s.ci_low * 100).toFixed(0)}, {(s.ci_high * 100).toFixed(0)}]
                  </td>
                  <td className={s.n < 30 ? "text-amber-600" : ""}>
                    {s.n}
                    {s.n < 30 ? " *" : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <h2 className="mb-2 text-lg font-medium">Tracked sources</h2>
        <ul className="text-sm">
          {SOURCES.map((s) => (
            <li key={s.id} className="border-t py-2">
              <span className="font-medium">{s.name}</span>{" "}
              <span className="text-neutral-500">({s.language})</span>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h2 className="mb-2 text-lg font-medium">Recently ingested</h2>
        {recent.length === 0 ? (
          <p className="text-sm text-neutral-500">
            Nothing yet. Run <code className="rounded bg-neutral-200 px-1 dark:bg-neutral-800">python -m tasks.run ingest</code>{" "}
            from <code>backend/</code> to pull the first batch.
          </p>
        ) : (
          <ul className="text-sm">
            {recent.map((a) => (
              <li key={a.id} className="border-t py-2">
                <a
                  href={a.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hover:underline"
                >
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
    </main>
  );
}
