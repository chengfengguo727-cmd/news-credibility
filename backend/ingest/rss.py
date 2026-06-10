"""RSS ingest.

Fetches each configured feed, dedupes by URL, and inserts new articles.
Body extraction is best-effort via trafilatura; failures fall back to the
RSS summary so we always have *something* to score on.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser
import httpx
import trafilatura
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from config import get_settings
from db.models import Article
from db.session import session_factory
from ingest.sources import SOURCES, RssSource

log = logging.getLogger(__name__)


@dataclass
class IngestStats:
    source: str
    fetched: int = 0
    inserted: int = 0
    skipped: int = 0
    errors: int = 0


def _parse_dt(entry) -> datetime | None:
    for key in ("published", "updated", "created"):
        val = entry.get(key)
        if not val:
            continue
        try:
            dt = parsedate_to_datetime(val)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (TypeError, ValueError):
            pass
    if entry.get("published_parsed"):
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    return None


def _fetch_body(client: httpx.Client, url: str, *, source_id: str = "") -> str | None:
    try:
        resp = client.get(url, follow_redirects=True, timeout=15)
        if resp.status_code != 200 or not resp.text:
            return None
        body = trafilatura.extract(resp.text, include_comments=False, include_tables=False)
        if body and source_id == "yahoo_tw":
            body = _strip_yahoo_tw_related(body)
        return body
    except (httpx.HTTPError, ValueError) as e:
        log.warning("body fetch failed for %s: %s", url, e)
        return None


# Headline marker patterns indicating a related-news appendix in Yahoo TW.
# Each line is a regex that, when matched at line-start, marks "everything
# below here is a different article, drop it".
_YAHOO_TW_BOUNDARY_PATTERNS = (
    # Explicit section markers
    r"^(相關新聞|推薦閱讀|延伸閱讀|熱門文章|精選文章|更多新聞)\b",
    # News-agency source bracket appearing fresh (start of a quoted lead)
    r"^[\[【\(（](中央社|FTNN新聞網|公視新聞網|TVBS|聯合新聞網|自由時報|中時新聞網|工商時報|經濟日報|鉅亨網|MoneyDJ)",
)


def _strip_yahoo_tw_related(body: str) -> str:
    """Yahoo TW articles concatenate a related-news appendix into the
    main text — trafilatura can't tell them apart. Cut at the first
    obvious boundary marker we can find.

    Walks line by line; stops at the first line that:
      (a) matches a known section header, OR
      (b) starts with a bracketed news-agency tag (signals a fresh lead)

    Conservative: if we can't find a boundary, returns body unchanged.
    """
    import re

    lines = body.split("\n")
    bounds = [re.compile(p) for p in _YAHOO_TW_BOUNDARY_PATTERNS]

    def _looks_like_headline(s: str) -> bool:
        """Short, attention-grabbing line that doesn't end in a proper sentence."""
        if not (8 <= len(s) <= 60):
            return False
        if s.endswith("。"):
            return False
        # sensational punctuation, em-dash teaser, or truncation
        if any(c in s for c in "！？!?") or "..." in s or "…" in s:
            return True
        return False

    # Pass 1: explicit boundary markers (high precision — agency tags,
    # named section headers). Prefer this when found.
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if any(b.search(stripped) for b in bounds) and i >= 2:
            return "\n".join(lines[:i]).rstrip()

    # Pass 2 fallback: only used if Pass 1 didn't match.
    # We look for *consecutive* headline-style lines (no intervening
    # narrative paragraph) — that's a tight signal for a related-news
    # widget. A single headline-style line in the middle of an article
    # is allowed (subheading); two next to each other isn't.
    last_was_headline = False
    last_headline_at: int | None = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        is_h = _looks_like_headline(stripped)
        if is_h and last_was_headline and last_headline_at is not None and last_headline_at >= 2:
            return "\n".join(lines[:last_headline_at]).rstrip()
        if is_h:
            last_headline_at = i
        last_was_headline = is_h
    return body


def ingest_source(source: RssSource, *, fetch_body: bool = True) -> IngestStats:
    settings = get_settings()
    stats = IngestStats(source=source.id)
    Session_ = session_factory()
    headers = {"User-Agent": settings.rss_user_agent}

    with httpx.Client(headers=headers) as client, Session_() as db:
        for feed_url in source.feeds:
            try:
                resp = client.get(feed_url, timeout=20)
                parsed = feedparser.parse(resp.content)
            except httpx.HTTPError as e:
                log.error("feed fetch failed %s: %s", feed_url, e)
                stats.errors += 1
                continue

            for entry in parsed.entries:
                stats.fetched += 1
                url = entry.get("link")
                title = entry.get("title")
                if not url or not title:
                    stats.skipped += 1
                    continue

                if _exists(db, url):
                    stats.skipped += 1
                    continue

                summary = entry.get("summary") or entry.get("description")
                published_at = _parse_dt(entry)
                body = None
                if fetch_body:
                    body = _fetch_body(client, url, source_id=source.id)
                    time.sleep(settings.rss_rate_limit_sec)

                _upsert(
                    db,
                    Article(
                        source=source.id,
                        url=url,
                        title=title[:1024],
                        summary=summary,
                        body=body,
                        published_at=published_at,
                        language=source.language,
                    ),
                )
                stats.inserted += 1

            db.commit()

    return stats


def _exists(db: Session, url: str) -> bool:
    return db.execute(select(Article.id).where(Article.url == url)).first() is not None


def _upsert(db: Session, article: Article) -> None:
    """ON CONFLICT DO NOTHING on url unique index."""
    stmt = (
        pg_insert(Article)
        .values(
            source=article.source,
            url=article.url,
            title=article.title,
            summary=article.summary,
            body=article.body,
            published_at=article.published_at,
            language=article.language,
        )
        .on_conflict_do_nothing(index_elements=["url"])
    )
    db.execute(stmt)


def ingest_all() -> list[IngestStats]:
    results = []
    for source in SOURCES:
        log.info("ingesting %s", source.id)
        try:
            results.append(ingest_source(source))
        except Exception:
            log.exception("ingest failed for %s", source.id)
            results.append(IngestStats(source=source.id, errors=1))
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for s in ingest_all():
        print(s)
