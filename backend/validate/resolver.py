"""Turn past-due claims into hit/partial/miss outcomes.

Each claim type has its own scoring rule:

- analyst_target: actual close vs target. hit=±5%, partial=±15%, miss=else.
- macro: actual macro value vs prediction. CPI/unemployment given as %,
  fed_funds as %, GDP as growth %. hit=|diff|<0.2, partial=|diff|<0.5.
- stock_event: deferred — needs event-driven validation (earnings dates,
  deal-close announcements). MVP marks them pending forever for now.

Returns counts per outcome bucket. We only write a `claim_outcomes` row
when we *actually* resolved — pending claims (data fetch failed) stay
without an outcome row so the next run retries them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Claim, ClaimOutcome, ClaimType, Outcome
from db.session import session_factory
from validate.market_data import (
    FRED_SERIES,
    earnings_near,
    fred_value,
    fred_yoy_pct,
    sec_8k_items_near,
    stock_close_any,
    us_close,
)

log = logging.getLogger(__name__)


@dataclass
class ResolveResult:
    claim_id: int
    type: str
    ticker: str | None
    predicted: float | None
    actual: float | None
    outcome: Outcome
    note: str | None = None


def _now_tz(dt) -> datetime:
    """Match the claim's timezone awareness for comparison."""
    if dt is None or dt.tzinfo is None:
        return datetime.now()
    return datetime.now(timezone.utc)


def resolve_analyst_target(claim: Claim) -> tuple[Outcome, float | None, str]:
    """Did the stock close near the analyst's price target by the deadline?"""
    if not claim.ticker or claim.predicted_value is None or claim.deadline is None:
        return Outcome.pending, None, "missing ticker/value/deadline"
    # stock_close_any routes US tickers to yfinance, TW tickers to FinMind
    actual = stock_close_any(claim.ticker, claim.deadline.date())
    if actual is None:
        return Outcome.pending, None, "no price data (yfinance/FinMind)"

    target = claim.predicted_value
    err = (actual - target) / target if target > 0 else 0
    if abs(err) <= 0.05:
        return Outcome.hit, actual, f"actual={actual:.2f} target={target:.2f} ({err:+.1%})"
    if abs(err) <= 0.15:
        return Outcome.partial, actual, f"actual={actual:.2f} target={target:.2f} ({err:+.1%})"
    return Outcome.miss, actual, f"actual={actual:.2f} target={target:.2f} ({err:+.1%})"


def resolve_macro(claim: Claim) -> tuple[Outcome, float | None, str]:
    """Predicted macro value vs actual official release."""
    if claim.topic is None or claim.predicted_value is None or claim.deadline is None:
        return Outcome.pending, None, "missing topic/value/deadline"
    series = FRED_SERIES.get(claim.topic)
    if series is None:
        return Outcome.pending, None, f"no FRED series for topic={claim.topic}"

    # For CPI predictions, the model typically gives a YoY %
    # For fed_funds/unemployment, level is what's reported.
    if claim.topic == "cpi":
        actual = fred_yoy_pct(series, claim.deadline.date())
    else:
        actual = fred_value(series, claim.deadline.date())
    if actual is None:
        return Outcome.pending, None, "FRED returned no data"

    err = actual - claim.predicted_value
    abs_err = abs(err)
    if abs_err < 0.2:
        return Outcome.hit, actual, f"actual={actual:.2f}% predicted={claim.predicted_value:.2f}% (Δ={err:+.2f})"
    if abs_err < 0.5:
        return Outcome.partial, actual, f"actual={actual:.2f}% predicted={claim.predicted_value:.2f}% (Δ={err:+.2f})"
    return Outcome.miss, actual, f"actual={actual:.2f}% predicted={claim.predicted_value:.2f}% (Δ={err:+.2f})"


def resolve_stock_event(claim: Claim) -> tuple[Outcome, float | None, str]:
    """Earnings beat / miss, merger close, product launch.

    - `earnings_beat` / `earnings_miss`: validated via yfinance earnings
      dates. Hit/miss decided by sign of `Surprise(%)` against the
      prediction direction.
    - `merger_completion` / `product_launch`: not implemented yet (would
      need SEC 8-K parsing or product news monitoring).
    """
    topic = claim.topic or ""
    if topic in ("earnings_beat", "earnings_miss"):
        if not claim.ticker or claim.deadline is None:
            return Outcome.pending, None, "missing ticker/deadline"
        info = earnings_near(claim.ticker, claim.deadline.date())
        if info is None:
            return Outcome.pending, None, "yfinance returned no earnings near deadline"
        surprise = info.get("surprise_pct")
        if surprise is None:
            # earnings hasn't been reported yet, even though deadline passed
            return Outcome.pending, None, f"no Reported EPS yet (report_date={info.get('report_date')})"

        # We compare the model's DIRECTIONAL call against the actual surprise.
        # ±2% is a small enough miss to be considered "in line" → partial.
        BEAT_HIT_THRESHOLD = 2.0
        IN_LINE_THRESHOLD = 2.0
        note_base = (
            f"surprise={surprise:+.2f}% report_date={info.get('report_date')} "
            f"est={info.get('eps_estimate')} actual={info.get('reported_eps')}"
        )
        if topic == "earnings_beat":
            if surprise >= BEAT_HIT_THRESHOLD:
                return Outcome.hit, surprise, "beat called, beat happened: " + note_base
            if surprise >= -IN_LINE_THRESHOLD:
                return Outcome.partial, surprise, "beat called, came in line: " + note_base
            return Outcome.miss, surprise, "beat called, missed: " + note_base
        else:  # earnings_miss
            if surprise <= -BEAT_HIT_THRESHOLD:
                return Outcome.hit, surprise, "miss called, missed: " + note_base
            if surprise <= IN_LINE_THRESHOLD:
                return Outcome.partial, surprise, "miss called, came in line: " + note_base
            return Outcome.miss, surprise, "miss called, beat instead: " + note_base

    if topic == "merger_completion":
        return _resolve_merger(claim)
    if topic == "product_launch":
        return _resolve_product_launch(claim)
    return Outcome.pending, None, f"unknown stock_event topic: {topic}"


def _resolve_merger(claim: Claim) -> tuple[Outcome, float | None, str]:
    """Did the company file an 8-K Item 2.01 (Completion of Acquisition) in the window?"""
    if not claim.ticker or claim.deadline is None:
        return Outcome.pending, None, "missing ticker/deadline"
    items = sec_8k_items_near(claim.ticker, claim.deadline.date())
    if not items:
        # Two cases: no filings at all (still possible the merger isn't through),
        # or our fetch failed. Stay pending and let next run retry.
        return Outcome.pending, None, "no 8-K filings in window (or SEC fetch failed)"
    note = f"8-K items in window: {','.join(items)}"
    if "2.01" in items:
        return Outcome.hit, None, "merger called, 8-K 2.01 filed: " + note
    # If 90 days have already passed beyond the deadline and still no 2.01,
    # we can be confident the deal didn't close on time. Mark as miss.
    if (datetime.now(timezone.utc).date() - claim.deadline.date()).days > 90:
        return Outcome.miss, None, "merger called, no 2.01 within 90d window: " + note
    return Outcome.pending, None, "deadline passed but still in 90d watch window: " + note


def _resolve_product_launch(claim: Claim) -> tuple[Outcome, float | None, str]:
    """Stock-price reaction proxy: did the market react positively around the launch?

    Crude but the best we can automate without a product-news subscription.
    A real launch typically registers as positive return in the ±5 trading
    days around the announced date.
    """
    if not claim.ticker or claim.deadline is None:
        return Outcome.pending, None, "missing ticker/deadline"
    deadline = claim.deadline.date()
    pre = stock_close_any(claim.ticker, deadline - timedelta(days=10))
    post = stock_close_any(claim.ticker, deadline + timedelta(days=5))
    if pre is None or post is None:
        return Outcome.pending, None, "price data unavailable"
    ret = (post - pre) / pre * 100
    note = f"±10d return = {ret:+.2f}% (pre={pre:.2f} post={post:.2f}). Proxy only."
    # Calibrated very loosely — product launches generally aren't huge price events.
    if ret >= 3:
        return Outcome.hit, ret, note
    if ret >= -3:
        return Outcome.partial, ret, note
    return Outcome.miss, ret, note


def resolve_one(claim: Claim) -> tuple[Outcome, float | None, str]:
    if claim.type == ClaimType.analyst_target:
        return resolve_analyst_target(claim)
    if claim.type == ClaimType.macro:
        return resolve_macro(claim)
    if claim.type == ClaimType.stock_event:
        return resolve_stock_event(claim)
    return Outcome.pending, None, f"unknown type {claim.type}"


def validate_due_claims(
    *, min_confidence: float = 0.5, limit: int | None = None
) -> list[ResolveResult]:
    """Pull past-due claims without outcomes, resolve them, write what we got.

    Only claims with confidence >= `min_confidence` are processed — the lower-
    confidence extractions are kept in the DB for inspection but not scored
    against the source's credibility.
    """
    results: list[ResolveResult] = []
    Session_ = session_factory()
    with Session_() as db:
        stmt = (
            select(Claim)
            .outerjoin(ClaimOutcome, ClaimOutcome.claim_id == Claim.id)
            .where(Claim.deadline.is_not(None))
            .where(Claim.deadline < datetime.now(timezone.utc))
            .where(Claim.llm_confidence >= min_confidence)
            .where(ClaimOutcome.id.is_(None))
            .order_by(Claim.deadline.desc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        claims = db.execute(stmt).scalars().all()

        for claim in claims:
            outcome, actual, note = resolve_one(claim)
            results.append(
                ResolveResult(
                    claim_id=claim.id,
                    type=claim.type.value,
                    ticker=claim.ticker,
                    predicted=claim.predicted_value,
                    actual=actual,
                    outcome=outcome,
                    note=note,
                )
            )
            if outcome != Outcome.pending:
                db.add(
                    ClaimOutcome(
                        claim_id=claim.id,
                        actual_value=actual,
                        outcome=outcome,
                        resolved_at=datetime.now(timezone.utc),
                        notes=note,
                    )
                )
                log.info(
                    "RESOLVED claim=%s type=%s outcome=%s %s",
                    claim.id, claim.type.value, outcome.value, note,
                )
            else:
                log.info("DEFERRED claim=%s type=%s — %s", claim.id, claim.type.value, note)
        db.commit()
    return results
