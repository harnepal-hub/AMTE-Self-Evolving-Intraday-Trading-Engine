"""Causal, reusable technical features for deterministic strategy research."""

from __future__ import annotations

import numpy as np
import pandas as pd


def add_features(bars: pd.DataFrame, *, fast: int = 20, slow: int = 50, atr_window: int = 14, momentum_window: int = 10) -> pd.DataFrame:
    """Return bars enriched only with information available on each bar."""
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")
    if min(fast, slow, atr_window, momentum_window) <= 0:
        raise ValueError("Feature windows must be positive")

    df = bars.copy().sort_index()
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)

    df["sma_fast"] = close.rolling(fast, min_periods=fast).mean()
    df["sma_slow"] = close.rolling(slow, min_periods=slow).mean()
    df["ema_fast"] = close.ewm(span=fast, adjust=False, min_periods=fast).mean()
    df["ema_slow"] = close.ewm(span=slow, adjust=False, min_periods=slow).mean()

    prev_close = close.shift(1)
    tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    df["atr"] = tr.rolling(atr_window, min_periods=atr_window).mean()
    df["atr_pct"] = df["atr"] / close
    df["atr_pct_mean"] = df["atr_pct"].shift(1).rolling(50, min_periods=50).mean()
    df["momentum"] = close.pct_change(momentum_window)

    typical = (high + low + close) / 3.0
    session_key = pd.Series(df.index.date, index=df.index)
    pv = typical * volume
    df["vwap"] = pv.groupby(session_key).cumsum() / volume.groupby(session_key).cumsum().replace(0, np.nan)
    df["volume_mean"] = volume.rolling(20, min_periods=20).mean()
    df["volume_ratio"] = volume / df["volume_mean"]

    df["prior_high"] = high.shift(1).rolling(20, min_periods=20).max()
    df["prior_low"] = low.shift(1).rolling(20, min_periods=20).min()

    # Additional causal regime features. Every rolling statistic is shifted when
    # it is used as a baseline so the current bar cannot define its own regime.
    df["trend_strength"] = (df["ema_fast"] - df["ema_slow"]).abs() / df["atr"].replace(0, np.nan)
    df["range_pct"] = (high - low) / close.replace(0, np.nan)
    df["range_mean"] = df["range_pct"].shift(1).rolling(20, min_periods=20).mean()
    df["volume_ratio_mean"] = df["volume_ratio"].shift(1).rolling(20, min_periods=20).mean()
    df["momentum_mean"] = df["momentum"].shift(1).rolling(20, min_periods=20).mean()
    df["breakout_buffer"] = df["atr"] * 0.10
    return df
