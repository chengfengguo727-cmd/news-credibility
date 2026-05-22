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
def extract() -> None:
    """Run claim extraction on unprocessed articles. (Week 2)"""
    typer.echo("extract: not implemented yet (Week 2)")
    raise typer.Exit(code=2)


@app.command()
def validate() -> None:
    """Resolve due claims against market data. (Week 3)"""
    typer.echo("validate: not implemented yet (Week 3)")
    raise typer.Exit(code=2)


@app.command()
def rescore() -> None:
    """Recompute Bayesian source scores. (Week 4)"""
    typer.echo("rescore: not implemented yet (Week 4)")
    raise typer.Exit(code=2)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app()
