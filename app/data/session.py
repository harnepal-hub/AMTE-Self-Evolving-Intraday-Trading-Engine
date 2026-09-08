"""Market-session filtering for normalized OHLCV data."""

from __future__ import annotations

from datetime import time

import pandas as pd


def filter_session(
    frame: pd.DataFrame,
    market_open: str,
    market_close: str,
    timezone: str,
) -> pd.DataFrame:
    """Keep bars inside a configured daily market session.

    The input index must be datetime-like. Timezone-aware indexes are
    converted to ``timezone``; naive indexes are localized to it.
    """
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise TypeError("Market data index must be a DatetimeIndex")

    index = frame.index
    if index.tz is None:
        index = index.tz_localize(timezone)
    else:
        index = index.tz_convert(timezone)

    start = time.fromisoformat(market_open)
    end = time.fromisoformat(market_close)
    mask = (index.time >= start) & (index.time <= end)

    result = frame.copy()
    result.index = index
    return result.loc[mask]


def assert_regular_frequency(frame: pd.DataFrame, frequency: str) -> None:
    """Reject unexpected gaps within each calendar day.

    Overnight/session gaps are ignored; only consecutive bars on the same
    local date are checked. Exchange holidays are therefore not treated as
    missing bars.
    """
    if len(frame) < 2:
        return
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise TypeError("Market data index must be a DatetimeIndex")

    expected = pd.Timedelta(frequency)
    diffs = frame.index.to_series().diff()
    same_day = frame.index.to_series().dt.date.eq(frame.index.to_series().shift(1).dt.date)
    gaps = diffs[(diffs > expected) & same_day]
    if not gaps.empty:
        raise ValueError(f"Unexpected intraday gaps found: {len(gaps)}")
