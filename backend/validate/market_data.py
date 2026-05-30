"""Market data adapters for Week 3 validation.

- US prices: yfinance (no key needed)
- Macro: FRED CSV graph endpoint (no key needed; full API would need FRED_API_KEY)
- Taiwan prices: FinMind (not yet — deferred to Week 4+)

All fetchers return None on any failure; the caller treats None as "data
not available" and leaves the claim outcome as `pending` so it'll be
retried next run.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import httpx
import yfinance as yf

log = logging.getLogger(__name__)

# FRED series IDs we know how to validate against
FRED_SERIES: dict[str, str] = {
    "fed_funds": "DFF",        # Federal Funds Rate (Effective, Daily)
    "cpi": "CPIAUCSL",         # CPI All Urban Consumers, Monthly (level)
    "cpi_yoy": "CPIAUCSL",     # alias; YoY transform applied in resolver
    "gdp": "GDP",              # Real GDP, Quarterly
    "unemployment": "UNRATE",  # Unemployment Rate, Monthly
}


def us_close(ticker: str, on: date, *, window_days: int = 14) -> float | None:
    """Closing price for `ticker` at or near `on`.

    Markets are closed on weekends/holidays — we fetch a small window before
    `on` and return the last available close. Returns None if yfinance gives
    nothing.
    """
    try:
        start = (on - timedelta(days=window_days)).isoformat()
        # end is exclusive in yfinance; bump by one day
        end = (on + timedelta(days=1)).isoformat()
        df = yf.Ticker(ticker).history(start=start, end=end, interval="1d", auto_adjust=True)
        if df is None or df.empty:
            return None
        return float(df["Close"].iloc[-1])
    except Exception as e:
        log.warning("us_close(%s, %s) failed: %s", ticker, on, e)
        return None


def fred_value(series: str, on: date, *, lookback_days: int = 90) -> float | None:
    """Latest observation of a FRED series at or before `on`.

    Uses the public graph CSV endpoint — no API key, but rate-limited and
    no historical headers. Sufficient for MVP.
    """
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
    try:
        r = httpx.get(url, timeout=20, follow_redirects=True)
        r.raise_for_status()
        # CSV header is "observation_date,SERIES_ID" — parse data lines
        cutoff_iso = on.isoformat()
        earliest = (on - timedelta(days=lookback_days)).isoformat()
        best_val: float | None = None
        for line in r.text.strip().splitlines()[1:]:
            parts = line.split(",")
            if len(parts) < 2:
                continue
            dstr, vstr = parts[0], parts[1].strip()
            if vstr in ("", "."):
                continue
            if earliest <= dstr <= cutoff_iso:
                try:
                    best_val = float(vstr)  # iterate forward → last <= cutoff wins
                except ValueError:
                    continue
        return best_val
    except (httpx.HTTPError, ValueError) as e:
        log.warning("fred_value(%s, %s) failed: %s", series, on, e)
        return None


def fred_yoy_pct(series: str, on: date) -> float | None:
    """Year-over-year percent change of a FRED series at `on`.

    Useful for CPI etc. where the predicted value is typically a YoY %.
    """
    cur = fred_value(series, on)
    prior = fred_value(series, date(on.year - 1, on.month, min(on.day, 28)))
    if cur is None or prior is None or prior == 0:
        return None
    return ((cur - prior) / prior) * 100.0
