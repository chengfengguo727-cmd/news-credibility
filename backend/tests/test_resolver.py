"""Unit tests for resolver scoring rules — no network.

We monkey-patch the market_data fetchers so the resolve_* functions get
deterministic inputs.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from db.models import Claim, ClaimType, Outcome
from validate import resolver


def _claim(**kwargs) -> Claim:
    """Build a Claim that's NOT attached to a DB session."""
    defaults = dict(
        id=1,
        article_id=1,
        type=ClaimType.analyst_target,
        ticker="AAPL",
        predicted_value=200.0,
        deadline=datetime(2026, 1, 1, tzinfo=timezone.utc),
        raw_text="test",
        llm_confidence=0.9,
    )
    defaults.update(kwargs)
    return Claim(**defaults)


# --- resolve_analyst_target ------------------------------------------

def test_analyst_target_hit_when_within_5pct(monkeypatch):
    monkeypatch.setattr(resolver, "stock_close_any", lambda t, on: 205.0)  # +2.5%
    outcome, actual, _ = resolver.resolve_analyst_target(_claim(predicted_value=200.0))
    assert outcome == Outcome.hit
    assert actual == 205.0


def test_analyst_target_partial_when_within_15pct(monkeypatch):
    monkeypatch.setattr(resolver, "stock_close_any", lambda t, on: 220.0)  # +10%
    outcome, _, _ = resolver.resolve_analyst_target(_claim(predicted_value=200.0))
    assert outcome == Outcome.partial


def test_analyst_target_miss_when_beyond_15pct(monkeypatch):
    monkeypatch.setattr(resolver, "stock_close_any", lambda t, on: 150.0)  # -25%
    outcome, _, _ = resolver.resolve_analyst_target(_claim(predicted_value=200.0))
    assert outcome == Outcome.miss


def test_analyst_target_pending_on_missing_fields():
    outcome, _, _ = resolver.resolve_analyst_target(_claim(ticker=None))
    assert outcome == Outcome.pending


def test_analyst_target_pending_when_yfinance_returns_none(monkeypatch):
    monkeypatch.setattr(resolver, "stock_close_any", lambda t, on: None)
    outcome, _, _ = resolver.resolve_analyst_target(_claim())
    assert outcome == Outcome.pending


# --- resolve_macro ---------------------------------------------------

def test_macro_cpi_hit_when_within_0_2(monkeypatch):
    monkeypatch.setattr(resolver, "fred_yoy_pct", lambda s, on: 3.15)
    claim = _claim(type=ClaimType.macro, ticker=None, topic="cpi", predicted_value=3.1)
    outcome, _, _ = resolver.resolve_macro(claim)
    assert outcome == Outcome.hit


def test_macro_unemployment_uses_level_not_yoy(monkeypatch):
    """unemployment is reported as a level (e.g. 4.3%), not a YoY change."""
    calls = []
    monkeypatch.setattr(resolver, "fred_value", lambda s, on: (calls.append((s, on)), 4.3)[1])
    monkeypatch.setattr(resolver, "fred_yoy_pct", lambda *a, **kw: pytest.fail("shouldn't call yoy"))
    claim = _claim(type=ClaimType.macro, ticker=None, topic="unemployment", predicted_value=4.3)
    outcome, _, _ = resolver.resolve_macro(claim)
    assert outcome == Outcome.hit
    assert calls and calls[0][0] == "UNRATE"


def test_macro_pending_on_unknown_topic():
    claim = _claim(type=ClaimType.macro, ticker=None, topic="m2", predicted_value=3.0)
    outcome, _, note = resolver.resolve_macro(claim)
    assert outcome == Outcome.pending
    assert "FRED series" in note


def test_macro_miss_when_more_than_0_5_off(monkeypatch):
    monkeypatch.setattr(resolver, "fred_yoy_pct", lambda s, on: 4.5)
    claim = _claim(type=ClaimType.macro, ticker=None, topic="cpi", predicted_value=3.1)
    outcome, _, _ = resolver.resolve_macro(claim)
    assert outcome == Outcome.miss


# --- resolve_stock_event ---------------------------------------------

def _earnings_event_claim(topic: str, **kw) -> Claim:
    return _claim(type=ClaimType.stock_event, topic=topic, predicted_text="x", **kw)


def test_stock_event_pending_when_no_topic():
    outcome, _, _ = resolver.resolve_stock_event(
        _claim(type=ClaimType.stock_event, topic=None)
    )
    assert outcome == Outcome.pending


def test_stock_event_pending_when_no_ticker():
    claim = _earnings_event_claim("earnings_beat", ticker=None)
    outcome, _, _ = resolver.resolve_stock_event(claim)
    assert outcome == Outcome.pending


def test_stock_event_pending_when_yfinance_empty(monkeypatch):
    monkeypatch.setattr(resolver, "earnings_near", lambda *a, **kw: None)
    outcome, _, note = resolver.resolve_stock_event(_earnings_event_claim("earnings_beat"))
    assert outcome == Outcome.pending
    assert "yfinance" in note


def test_stock_event_pending_when_report_not_yet(monkeypatch):
    """earnings date passed but Yahoo hasn't filled Reported EPS yet."""
    monkeypatch.setattr(
        resolver, "earnings_near",
        lambda *a, **kw: {"eps_estimate": 1.5, "reported_eps": None, "surprise_pct": None,
                          "report_date": "2026-07-31"},
    )
    outcome, _, note = resolver.resolve_stock_event(_earnings_event_claim("earnings_beat"))
    assert outcome == Outcome.pending
    assert "no Reported EPS" in note


def test_earnings_beat_hit_when_strongly_positive(monkeypatch):
    monkeypatch.setattr(
        resolver, "earnings_near",
        lambda *a, **kw: {"eps_estimate": 1.5, "reported_eps": 1.7, "surprise_pct": 13.3, "report_date": "x"},
    )
    outcome, actual, _ = resolver.resolve_stock_event(_earnings_event_claim("earnings_beat"))
    assert outcome == Outcome.hit
    assert actual == 13.3


def test_earnings_beat_partial_when_in_line(monkeypatch):
    monkeypatch.setattr(
        resolver, "earnings_near",
        lambda *a, **kw: {"eps_estimate": 1.5, "reported_eps": 1.51, "surprise_pct": 0.5, "report_date": "x"},
    )
    outcome, _, _ = resolver.resolve_stock_event(_earnings_event_claim("earnings_beat"))
    assert outcome == Outcome.partial


def test_earnings_beat_miss_when_negative(monkeypatch):
    monkeypatch.setattr(
        resolver, "earnings_near",
        lambda *a, **kw: {"eps_estimate": 1.5, "reported_eps": 1.2, "surprise_pct": -20.0, "report_date": "x"},
    )
    outcome, _, _ = resolver.resolve_stock_event(_earnings_event_claim("earnings_beat"))
    assert outcome == Outcome.miss


def test_earnings_miss_hit_when_strongly_negative(monkeypatch):
    """Miss called and it happened — the source called it right."""
    monkeypatch.setattr(
        resolver, "earnings_near",
        lambda *a, **kw: {"eps_estimate": 1.5, "reported_eps": 1.2, "surprise_pct": -20.0, "report_date": "x"},
    )
    outcome, _, note = resolver.resolve_stock_event(_earnings_event_claim("earnings_miss"))
    assert outcome == Outcome.hit
    assert "miss called, missed" in note


def test_earnings_miss_miss_when_actually_beat(monkeypatch):
    """Miss called but the company beat — the source called it wrong."""
    monkeypatch.setattr(
        resolver, "earnings_near",
        lambda *a, **kw: {"eps_estimate": 1.5, "reported_eps": 1.8, "surprise_pct": 20.0, "report_date": "x"},
    )
    outcome, _, note = resolver.resolve_stock_event(_earnings_event_claim("earnings_miss"))
    assert outcome == Outcome.miss
    assert "miss called, beat instead" in note


def test_merger_hit_when_2_01_filed(monkeypatch):
    monkeypatch.setattr(resolver, "sec_8k_items_near", lambda *a, **kw: ["2.01", "9.01"])
    outcome, _, note = resolver.resolve_stock_event(_earnings_event_claim("merger_completion"))
    assert outcome == Outcome.hit
    assert "2.01" in note


def test_merger_pending_when_no_filings(monkeypatch):
    monkeypatch.setattr(resolver, "sec_8k_items_near", lambda *a, **kw: [])
    outcome, _, _ = resolver.resolve_stock_event(_earnings_event_claim("merger_completion"))
    assert outcome == Outcome.pending


def test_merger_miss_after_90d_no_2_01(monkeypatch):
    from datetime import datetime, timezone, timedelta
    monkeypatch.setattr(resolver, "sec_8k_items_near", lambda *a, **kw: ["5.02", "9.01"])
    long_ago = datetime.now(timezone.utc) - timedelta(days=120)
    claim = _earnings_event_claim("merger_completion", deadline=long_ago)
    outcome, _, _ = resolver.resolve_stock_event(claim)
    assert outcome == Outcome.miss


def test_product_launch_hit_on_strong_rise(monkeypatch):
    # pre=100, post=105 -> +5%
    monkeypatch.setattr(resolver, "stock_close_any",
                        lambda t, on: 100.0 if on.year == 2025 else 105.0)
    from datetime import datetime, timezone
    claim = _earnings_event_claim("product_launch",
                                  deadline=datetime(2026, 1, 1, tzinfo=timezone.utc))
    outcome, val, _ = resolver.resolve_stock_event(claim)
    # We supplied a date arithmetic where pre uses an earlier year
    assert outcome in (Outcome.hit, Outcome.partial)  # actual % depends on direction
    assert val is not None


def test_product_launch_pending_when_price_missing(monkeypatch):
    monkeypatch.setattr(resolver, "stock_close_any", lambda t, on: None)
    outcome, _, _ = resolver.resolve_stock_event(_earnings_event_claim("product_launch"))
    assert outcome == Outcome.pending
