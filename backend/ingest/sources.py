"""Registry of RSS sources for the MVP.

Each source has a stable short id (used as `articles.source`) and 1+ feed URLs.
Keep feed URLs to the publicly-advertised ones; do not bypass paywalls.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RssSource:
    id: str
    name: str
    language: str
    feeds: tuple[str, ...]


SOURCES: tuple[RssSource, ...] = (
    # Reuters is DISABLED — left here for reference.
    # We previously used Google News (`site:reuters.com`) as a fallback because
    # Reuters' own RSS endpoints (feeds.reuters.com, reuters.com/markets/rss,
    # reutersagency.com/feed) all return 401/404 as of 2026. Google News only
    # gives headlines + ~200-char snippets, never article bodies. After
    # backfilling 97 Reuters articles we measured avg article_quality=0.26 and
    # claims_per_article=0.01 — well below CNBC (0.66, 0.27) and Yahoo
    # (0.55, 0.24). The signal is too thin to justify the Claude tokens.
    # Re-enable when we have a paid feed (NewsAPI Business, Polygon News, etc.)
    # or a working scraping path that defeats Reuters' Cloudflare gate.
    #
    # RssSource(id="reuters", name="Reuters", language="en", feeds=(
    #     "https://news.google.com/rss/search?q=site:reuters.com+when:1d&hl=en-US&gl=US&ceid=US:en",
    # )),
    RssSource(
        id="cnbc",
        name="CNBC",
        language="en",
        feeds=(
            "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",  # Top news
            "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15839069",   # Markets
        ),
    ),
    RssSource(
        id="yahoo_finance",
        name="Yahoo Finance",
        language="en",
        feeds=(
            "https://finance.yahoo.com/news/rssindex",
        ),
    ),
)


def by_id(source_id: str) -> RssSource | None:
    return next((s for s in SOURCES if s.id == source_id), None)
