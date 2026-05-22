"""Claim extractor — Week 2.

Plan: feed (title + body) to Claude Haiku with a structured-output prompt
that returns 0..N claims of types {analyst_target, macro, stock_event}.
Write to claims table; mark article.extracted = true.

Skeleton only — implementation lands in Week 2.
"""

from __future__ import annotations

from db.models import ClaimType  # noqa: F401  (kept for downstream typing)

EXTRACTOR_MODEL = "claude-haiku-4-5-20251001"


def extract_claims_for_article(article_id: int) -> int:
    """Returns the number of claims written. Stub."""
    raise NotImplementedError("Week 2")
