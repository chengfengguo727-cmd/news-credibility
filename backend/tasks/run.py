"""CLI runner for pipeline stages.

Replaces Celery for MVP. Each command is idempotent and short-lived so it
can be triggered by GitHub Actions cron, a Vercel cron, or run manually.

Usage:
    python -m tasks.run ingest
    python -m tasks.run extract    # Week 2
    python -m tasks.run validate   # Week 3
    python -m tasks.run rescore    # Week 4
"""

from __future__ import annotations

import logging

import typer

app = typer.Typer(no_args_is_help=True, add_completion=False)
log = logging.getLogger(__name__)


@app.command()
def ingest() -> None:
    """Pull the latest entries from all RSS sources into `articles`."""
    from ingest.rss import ingest_all

    stats = ingest_all()
    for s in stats:
        typer.echo(
            f"{s.source}: fetched={s.fetched} inserted={s.inserted} "
            f"skipped={s.skipped} errors={s.errors}"
        )


@app.command()
def extract(
    limit: int = typer.Option(50, help="Max articles to process this run."),
    include_bodyless: bool = typer.Option(
        False, "--include-bodyless",
        help="Also process headline-only articles (body IS NULL). Off by default to avoid wasting tokens.",
    ),
) -> None:
    """Run Claude claim extraction on unprocessed articles."""
    from extract.claim_extractor import extract_batch

    stats = extract_batch(limit=limit, include_bodyless=include_bodyless)
    claims = sum(s.claims_written for s in stats)
    tickers = sum(s.tickers_written for s in stats)
    sectors = sum(s.sectors_written for s in stats)
    tags = sum(s.tags_written for s in stats)
    cost = sum(s.cost_usd for s in stats)
    errors = sum(1 for s in stats if s.error)
    typer.echo(
        f"articles={len(stats)} claims={claims} tickers={tickers} "
        f"sectors={sectors} tags={tags} errors={errors} cost_usd={cost:.4f}"
    )


@app.command()
def validate(
    limit: int | None = typer.Option(None, help="Max claims to resolve."),
    min_confidence: float = typer.Option(0.5, help="Skip claims below this LLM confidence."),
) -> None:
    """Resolve past-due claims against yfinance / FRED."""
    from validate.resolver import validate_due_claims

    results = validate_due_claims(min_confidence=min_confidence, limit=limit)
    hit = sum(1 for r in results if r.outcome.value == "hit")
    partial = sum(1 for r in results if r.outcome.value == "partial")
    miss = sum(1 for r in results if r.outcome.value == "miss")
    pending = sum(1 for r in results if r.outcome.value == "pending")
    typer.echo(
        f"due={len(results)} resolved={hit + partial + miss} "
        f"hit={hit} partial={partial} miss={miss} pending={pending}"
    )


@app.command()
def rescore() -> None:
    """Recompute Bayesian source scores. (Week 4)"""
    typer.echo("rescore: not implemented yet (Week 4)")
    raise typer.Exit(code=2)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app()
