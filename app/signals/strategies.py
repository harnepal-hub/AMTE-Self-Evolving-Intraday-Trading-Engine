"""Deterministic long/short intraday strategies for Stage 3 research."""

from __future__ import annotations

import pandas as pd


def _ready(row: pd.Series, names: tuple[str, ...]) -> bool:
    return all(pd.notna(row.get(name)) for name in names)


def ema_trend(row: pd.Series) -> int:
    if not _ready(row, ("close", "ema_fast", "ema_slow", "atr")):
        return 0
    if row.close > row.ema_fast > row.ema_slow:
        return 1
    if row.close < row.ema_fast < row.ema_slow:
        return -1
    return 0


def momentum_breakout(row: pd.Series) -> int:
    if not _ready(row, ("close", "prior_high", "prior_low", "volume_ratio", "atr_pct")):
        return 0
    if row.volume_ratio < 1.0:
        return 0
    if row.close > row.prior_high:
        return 1
    if row.close < row.prior_low:
        return -1
    return 0


def vwap_reversion(row: pd.Series, z: float = 0.0025) -> int:
    """Mean-reversion signal around session VWAP; symmetric long/short."""
    if not _ready(row, ("close", "vwap", "atr_pct")) or row.atr_pct <= 0:
        return 0
    distance = row.close / row.vwap - 1.0
    threshold = max(z, row.atr_pct * 0.5)
    if distance < -threshold:
        return 1
    if distance > threshold:
        return -1
    return 0


def or_breakout(frame: pd.DataFrame, opening_bars: int = 6) -> pd.Series:
    """Opening-range breakout using only completed opening-range bars per day."""
    if opening_bars <= 0:
        raise ValueError("opening_bars must be positive")
    df = frame.copy().sort_index()
    days = pd.Series(df.index.date, index=df.index)
    position = days.groupby(days).cumcount()
    opening_high = df["high"].where(position < opening_bars).groupby(days).transform("max").shift(1)
    opening_low = df["low"].where(position < opening_bars).groupby(days).transform("min").shift(1)
    # Reset ranges at the start of each session and suppress signals inside OR.
    result = pd.Series(0, index=df.index, dtype=int)
    active = position >= opening_bars
    result.loc[active & (df["close"] > opening_high)] = 1
    result.loc[active & (df["close"] < opening_low)] = -1
    return result


STRATEGIES = {
    "ema_trend": ema_trend,
    "momentum_breakout": momentum_breakout,
    "vwap_reversion": vwap_reversion,
}
