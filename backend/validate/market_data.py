"""Market data adapters for Week 3-4 validation.

- US prices / earnings : yfinance (no key needed; flaky — graceful None fallback)
- Macro                : FRED public CSV endpoint (no key needed)
- Taiwan prices        : FinMind public daily-price endpoint (no key needed for ≤600 req/h)
- SEC filings          : data.sec.gov submissions JSON (no key; needs UA with contact)

All fetchers return None on any failure; the caller treats None as
"data not available" and leaves the claim outcome `pending` so it'll
be retried next run.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from functools import lru_cache

import httpx
import yfinance as yf

log = logging.getLogger(__name__)

# SEC's policy: requests must carry a real UA with contact info.
_SEC_UA = "NewsCredibilityBot/0.1 chengfengguo727@gmail.com"

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


def earnings_near(
    ticker: str, on: date, *, window_days: int = 14
) -> dict | None:
    """Find the earnings report dated closest to (and ≤) `on`.

    Returns a dict with eps_estimate / reported_eps / surprise_pct,
    or None if no matching earnings report found (often because yfinance
    is rate-limited — caller treats None as "pending, try again later").

    `window_days` allows for some slack: a claim with deadline=Q2 end might
    aim at earnings released slightly before. We also accept earnings
    released up to `window_days` AFTER `on` because some claims encode
    the predicted target date approximately.
    """
    try:
        df = yf.Ticker(ticker).get_earnings_dates(limit=12)
    except Exception as e:
        log.warning("earnings_near(%s) fetch failed: %s", ticker, e)
        return None
    if df is None or df.empty:
        return None

    # df index = Timestamp of earnings release; columns = ['EPS Estimate', 'Reported EPS', 'Surprise(%)']
    # We want the row closest to `on`, prioritizing past releases (Reported EPS not NaN).
    import pandas as pd

    target_ts = pd.Timestamp(on).tz_localize(None)
    # normalize index to naive timestamps for comparison
    idx_naive = df.index.tz_localize(None) if df.index.tz is not None else df.index

    deltas = (idx_naive - target_ts).total_seconds() / 86400
    # Pick the row whose date is within ±window_days, preferring past (negative delta)
    mask = (deltas >= -90) & (deltas <= window_days)
    if not mask.any():
        return None
    candidate = df[mask].iloc[(deltas[mask]).abs().argsort()[0]]

    def _f(v):
        try:
            return float(v) if v is not None and not (isinstance(v, float) and v != v) else None
        except (TypeError, ValueError):
            return None

    return {
        "eps_estimate": _f(candidate.get("EPS Estimate")),
        "reported_eps": _f(candidate.get("Reported EPS")),
        "surprise_pct": _f(candidate.get("Surprise(%)")),
        "report_date": str(candidate.name)[:10],
    }


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


@lru_cache(maxsize=1)
def _ticker_to_cik() -> dict[str, str]:
    """Fetch + cache the SEC's ticker→CIK mapping (once per process)."""
    try:
        r = httpx.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers={"User-Agent": _SEC_UA}, timeout=30,
        )
        r.raise_for_status()
        return {v["ticker"]: str(v["cik_str"]).zfill(10) for v in r.json().values()}
    except (httpx.HTTPError, ValueError, KeyError) as e:
        log.warning("ticker→CIK fetch failed: %s", e)
        return {}


def sec_8k_items_near(ticker: str, on: date, *, before_days: int = 30, after_days: int = 90) -> list[str]:
    """Return the list of distinct 8-K Item codes filed for `ticker` in
    the window [on - before_days, on + after_days].

    Item 2.01 means "Completion of Acquisition or Disposition of Assets"
    — that's our merger_completion signal.
    """
    cik = _ticker_to_cik().get(ticker)
    if not cik:
        return []
    try:
        r = httpx.get(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            headers={"User-Agent": _SEC_UA}, timeout=20,
        )
        r.raise_for_status()
        recent = r.json().get("filings", {}).get("recent", {})
    except (httpx.HTTPError, ValueError) as e:
        log.warning("SEC submissions fetch failed for %s (%s): %s", ticker, cik, e)
        return []

    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    items = recent.get("items", [])
    if not (forms and dates):
        return []

    window_start = (on - timedelta(days=before_days)).isoformat()
    window_end = (on + timedelta(days=after_days)).isoformat()
    found: set[str] = set()
    for i, form in enumerate(forms):
        if form != "8-K":
            continue
        d = dates[i] if i < len(dates) else ""
        if not (window_start <= d <= window_end):
            continue
        raw_items = items[i] if i < len(items) else ""
        # items field is a comma-separated string like "2.02,9.01"
        for it in (raw_items or "").split(","):
            it = it.strip()
            if it:
                found.add(it)
    return sorted(found)


# Yahoo TW-stock symbol normalization: "2330.TW" -> "2330"
def _tw_symbol(ticker: str) -> str | None:
    if ticker.endswith(".TW"):
        return ticker[:-3]
    if ticker.isdigit() and 4 <= len(ticker) <= 6:
        return ticker
    return None


def tw_close(ticker: str, on: date, *, lookback_days: int = 14) -> float | None:
    """Closing price for a Taiwan-listed stock via FinMind's public endpoint.

    No API key needed for the public TaiwanStockPrice dataset (rate-limited
    to ~600 req/h). Accepts `2330.TW` or bare `2330`.
    """
    sym = _tw_symbol(ticker)
    if sym is None:
        return None
    start = (on - timedelta(days=lookback_days)).isoformat()
    end = on.isoformat()
    url = (
        "https://api.finmindtrade.com/api/v4/data?"
        f"dataset=TaiwanStockPrice&data_id={sym}&start_date={start}&end_date={end}"
    )
    try:
        r = httpx.get(url, timeout=20)
        r.raise_for_status()
        payload = r.json()
        rows = payload.get("data") or []
        if not rows:
            return None
        # rows ordered ascending by date; pick the most recent close
        return float(rows[-1]["close"])
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        log.warning("tw_close(%s, %s) failed: %s", ticker, on, e)
        return None


def stock_close_any(ticker: str, on: date) -> float | None:
    """Route to the right backend by ticker shape."""
    if ".TW" in ticker or _tw_symbol(ticker):
        return tw_close(ticker, on)
    return us_close(ticker, on)


def fred_yoy_pct(series: str, on: date) -> float | None:
    """Year-over-year percent change of a FRED series at `on`.

    Useful for CPI etc. where the predicted value is typically a YoY %.
    """
    cur = fred_value(series, on)
    prior = fred_value(series, date(on.year - 1, on.month, min(on.day, 28)))
    if cur is None or prior is None or prior == 0:
        return None
    return ((cur - prior) / prior) * 100.0
