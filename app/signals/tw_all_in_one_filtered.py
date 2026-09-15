"""Filtered TW All in One signals for cross-coin research.

The base signal is the supplied Pine logic: EHMA/HULL with SHULL=HULL[2],
then crossunder=BUY and crossover=SELL. Filters are causal and are applied
only to information available on the signal bar.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0.0)
    dn = -d.clip(upper=0.0)
    au = up.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    ad = dn.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = au / ad.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def tw_base(bars: pd.DataFrame, length: int = 16) -> pd.Series:
    close = bars["close"].astype(float)
    half = max(1, length // 2)
    root = max(1, round(length ** 0.5))
    hull = _ema(2 * _ema(close, half) - _ema(close, length), root)
    mhull = hull
    shull = hull.shift(2)
    prev_m = mhull.shift(1)
    prev_s = shull.shift(1)
    buy = (prev_s >= prev_m) & (shull < mhull)
    sell = (prev_s <= prev_m) & (shull > mhull)
    return pd.Series(np.select([buy, sell], [1, -1], default=0), index=bars.index, dtype=int)


def filtered_signals(
    bars: pd.DataFrame,
    *,
    hull_length: int = 16,
    ema_length: int = 100,
    ema_filter: bool = True,
    slope_filter: bool = True,
    volume_ratio_min: float = 0.0,
    atr_expansion_min: float = 0.0,
    ema_distance_min: float = 0.0,
    rsi_filter: bool = False,
    cooldown_bars: int = 0,
) -> pd.Series:
    """Return -1/0/1 TW signals after a pre-declared causal filter stack."""
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")
    if cooldown_bars < 0:
        raise ValueError("cooldown_bars must be non-negative")

    df = bars.sort_index().copy()
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    vol = df["volume"].astype(float)
    ema = _ema(close, ema_length)
    base = tw_base(df, hull_length)

    half = max(1, hull_length // 2)
    root = max(1, round(hull_length ** 0.5))
    hull = _ema(2 * _ema(close, half) - _ema(close, hull_length), root)

    tr = pd.concat([(high-low), (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14, min_periods=14).mean()
    atr_pct = atr / close.replace(0, np.nan)
    atr_mean = atr_pct.shift(1).rolling(50, min_periods=50).mean()
    vol_mean = vol.rolling(20, min_periods=20).mean()
    volume_ratio = vol / vol_mean
    ema_distance = (close - ema).abs() / close.replace(0, np.nan)
    rsi = _rsi(close)

    out = base.copy()
    if ema_filter:
        out = out.where(~((out == 1) & (close <= ema)), 0)
        out = out.where(~((out == -1) & (close >= ema)), 0)
    if slope_filter:
        out = out.where(~((out == 1) & (hull <= hull.shift(1))), 0)
        out = out.where(~((out == -1) & (hull >= hull.shift(1))), 0)
    if volume_ratio_min > 0:
        out = out.where(volume_ratio >= volume_ratio_min, 0)
    if atr_expansion_min > 0:
        out = out.where((atr_pct / atr_mean.replace(0, np.nan)) >= atr_expansion_min, 0)
    if ema_distance_min > 0:
        out = out.where(ema_distance >= ema_distance_min, 0)
    if rsi_filter:
        out = out.where(~((out == 1) & (rsi < 50)), 0)
        out = out.where(~((out == -1) & (rsi > 50)), 0)

    if cooldown_bars:
        arr = out.to_numpy(copy=True)
        last = -10**9
        for i, sig in enumerate(arr):
            if sig:
                if i - last <= cooldown_bars:
                    arr[i] = 0
                else:
                    last = i
        out = pd.Series(arr, index=out.index, dtype=int)
    return out
