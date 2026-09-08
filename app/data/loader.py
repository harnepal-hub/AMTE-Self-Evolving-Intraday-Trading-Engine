"""CSV market-data ingestion and normalization."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.data.schema import REQUIRED_COLUMNS


def load_ohlcv_csv(
    path: str | Path,
    timestamp_column: str = "timestamp",
    timezone: str | None = None,
) -> pd.DataFrame:
    """Load and validate a CSV into AMTE's canonical OHLCV format.

    Timestamps are sorted and, when ``timezone`` is supplied, normalized to
    that timezone. Naive timestamps are localized; timezone-aware timestamps
    are converted.
    """
    frame = pd.read_csv(path)
    if timestamp_column not in frame.columns:
        raise ValueError(f"Missing timestamp column: {timestamp_column}")

    missing = set(REQUIRED_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"Missing OHLCV columns: {sorted(missing)}")

    frame[timestamp_column] = pd.to_datetime(frame[timestamp_column], errors="raise")
    frame = frame.set_index(timestamp_column).sort_index()
    frame = frame.loc[:, list(REQUIRED_COLUMNS)]
    frame = frame.apply(pd.to_numeric, errors="raise")

    if frame.empty:
        raise ValueError("Market data file is empty")
    if frame.index.has_duplicates:
        raise ValueError("Duplicate timestamps found in market data")
    if not frame.index.is_monotonic_increasing:
        raise ValueError("Timestamps must be increasing")
    if frame.isna().any().any():
        raise ValueError("OHLCV data contains missing values")
    if (frame[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("OHLC prices must be positive")
    if (frame["volume"] < 0).any():
        raise ValueError("Volume cannot be negative")
    if (frame["high"] < frame[["open", "close"]].max(axis=1)).any():
        raise ValueError("Found bar with high below open/close")
    if (frame["low"] > frame[["open", "close"]].min(axis=1)).any():
        raise ValueError("Found bar with low above open/close")

    if timezone:
        if frame.index.tz is None:
            frame.index = frame.index.tz_localize(timezone)
        else:
            frame.index = frame.index.tz_convert(timezone)

    return frame
