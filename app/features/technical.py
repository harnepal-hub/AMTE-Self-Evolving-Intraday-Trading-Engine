"""Leakage-safe technical features for completed OHLCV bars."""

from __future__ import annotations

import pandas as pd


def add_basic_features(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with simple causal features.

    Features use current and historical bars only. No future rows are referenced.
    Required columns: open, high, low, close, volume.
    """
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")

    out = df.copy()
    out["return_1"] = out["close"].pct_change()
    out["range_pct"] = (out["high"] - out["low"]) / out["close"].replace(0, pd.NA)
    out["body_pct"] = (out["close"] - out["open"]) / out["open"].replace(0, pd.NA)
    out["sma_20"] = out["close"].rolling(20, min_periods=20).mean()
    out["sma_50"] = out["close"].rolling(50, min_periods=50).mean()
    out["volume_sma_20"] = out["volume"].rolling(20, min_periods=20).mean()
    return out
