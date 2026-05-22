"""Market data adapters — Week 3.

- US prices: yfinance
- Macro: FRED (via fredapi)
- TW prices: FinMind (later)
"""

from __future__ import annotations

from datetime import date


def us_close(ticker: str, on: date) -> float | None:
    raise NotImplementedError("Week 3")


def fred_value(series: str, on: date) -> float | None:
    raise NotImplementedError("Week 3")
