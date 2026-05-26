"""Unit tests for the pure-parse helpers.

The subprocess call to `claude` is not exercised here — it requires the CLI
and an authenticated session. We only test `_parse_claims` and `_parse_deadline`,
which are pure functions over the structured_output dict that claude returns.
"""

from __future__ import annotations

from datetime import datetime, timezone

from db.models import ClaimType
from extract.claim_extractor import _parse_claims, _parse_deadline


def test_empty_structured_returns_no_rows():
    assert _parse_claims({"claims": []}, article_id=1) == []


def test_missing_claims_key_returns_no_rows():
    assert _parse_claims({}, article_id=1) == []


def test_analyst_target_parses_fully():
    structured = {
        "claims": [
            {
                "type": "analyst_target",
                "ticker": "AAPL",
                "predicted_value": 250.0,
                "predicted_text": "Buy",
                "deadline_iso": "2026-05-24T00:00:00Z",
                "raw_text": "raised AAPL target to $250",
                "confidence": 0.95,
            }
        ]
    }
    claims = _parse_claims(structured, article_id=42)
    assert len(claims) == 1
    c = claims[0]
    assert c.article_id == 42
    assert c.type == ClaimType.analyst_target
    assert c.ticker == "AAPL"
    assert c.predicted_value == 250.0
    assert c.predicted_text == "Buy"
    assert c.deadline == datetime(2026, 5, 24, tzinfo=timezone.utc)
    assert c.raw_text == "raised AAPL target to $250"
    assert c.llm_confidence == 0.95


def test_macro_claim_no_ticker():
    structured = {
        "claims": [
            {
                "type": "macro",
                "topic": "fed_funds",
                "predicted_value": 5.125,
                "deadline_iso": "2026-09-17",
                "raw_text": "expect 25bp cut in September",
                "confidence": 0.8,
            }
        ]
    }
    claims = _parse_claims(structured, article_id=1)
    assert len(claims) == 1
    assert claims[0].ticker is None
    assert claims[0].topic == "fed_funds"


def test_invalid_claim_type_is_skipped():
    structured = {
        "claims": [
            {"type": "nonsense", "raw_text": "junk", "confidence": 0.5},
            {"type": "macro", "raw_text": "good", "confidence": 0.7},
        ]
    }
    claims = _parse_claims(structured, article_id=1)
    assert len(claims) == 1
    assert claims[0].type == ClaimType.macro


def test_missing_optional_fields_become_none():
    structured = {
        "claims": [
            {
                "type": "stock_event",
                "ticker": "NVDA",
                "raw_text": "expected to beat",
                "confidence": 0.6,
            }
        ]
    }
    claims = _parse_claims(structured, article_id=1)
    assert len(claims) == 1
    c = claims[0]
    assert c.predicted_value is None
    assert c.predicted_text is None
    assert c.deadline is None
    assert c.topic is None


def test_parse_deadline_handles_z_suffix_and_naive():
    assert _parse_deadline(None) is None
    assert _parse_deadline("") is None
    assert _parse_deadline("not-a-date") is None
    assert _parse_deadline("2026-05-24") == datetime(2026, 5, 24, tzinfo=timezone.utc)
    assert _parse_deadline("2026-05-24T12:34:56Z") == datetime(
        2026, 5, 24, 12, 34, 56, tzinfo=timezone.utc
    )
