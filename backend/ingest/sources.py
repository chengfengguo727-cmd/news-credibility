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
    RssSource(
        id="reuters",
        name="Reuters",
        language="en",
        # Reuters official feeds redirect to reutersagency.com; community-maintained
        # mirrors exist but are unstable. Start with the markets wire on Google News
        # as a fallback — replace with a stable feed if/when available.
        feeds=(
            "https://news.google.com/rss/search?q=site:reuters.com+when:1d&hl=en-US&gl=US&ceid=US:en",
        ),
    ),
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
