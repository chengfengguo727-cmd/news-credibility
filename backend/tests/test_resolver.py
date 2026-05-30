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
    monkeypatch.setattr(resolver, "us_close", lambda t, on: 205.0)  # +2.5%
    outcome, actual, _ = resolver.resolve_analyst_target(_claim(predicted_value=200.0))
    assert outcome == Outcome.hit
    assert actual == 205.0


def test_analyst_target_partial_when_within_15pct(monkeypatch):
    monkeypatch.setattr(resolver, "us_close", lambda t, on: 220.0)  # +10%
    outcome, _, _ = resolver.resolve_analyst_target(_claim(predicted_value=200.0))
    assert outcome == Outcome.partial


def test_analyst_target_miss_when_beyond_15pct(monkeypatch):
    monkeypatch.setattr(resolver, "us_close", lambda t, on: 150.0)  # -25%
    outcome, _, _ = resolver.resolve_analyst_target(_claim(predicted_value=200.0))
    assert outcome == Outcome.miss


def test_analyst_target_pending_on_missing_fields():
    outcome, _, _ = resolver.resolve_analyst_target(_claim(ticker=None))
    assert outcome == Outcome.pending


def test_analyst_target_pending_when_yfinance_returns_none(monkeypatch):
    monkeypatch.setattr(resolver, "us_close", lambda t, on: None)
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

def test_stock_event_always_pending_for_now():
    outcome, _, note = resolver.resolve_stock_event(_claim(type=ClaimType.stock_event))
    assert outcome == Outcome.pending
    assert "not implemented" in note.lower()
