from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.data.schema import REQUIRED_COLUMNS


def load_ohlcv_csv(path: str | Path, timestamp_column: str = "timestamp") -> pd.DataFrame:
    """Load a CSV into AMTE's canonical OHLCV DataFrame format."""
    frame = pd.read_csv(path)
    if timestamp_column not in frame.columns:
        raise ValueError(f"Missing timestamp column: {timestamp_column}")
    frame[timestamp_column] = pd.to_datetime(frame[timestamp_column], errors="raise")
    frame = frame.set_index(timestamp_column).sort_index()
    missing = set(REQUIRED_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"Missing OHLCV columns: {sorted(missing)}")
    frame = frame.loc[:, list(REQUIRED_COLUMNS)]
    frame = frame.apply(pd.to_numeric, errors="raise")
    if frame.index.has_duplicates:
        raise ValueError("Duplicate timestamps found in market data")
    if frame.empty:
        raise ValueError("Market data file is empty")
    return frame
