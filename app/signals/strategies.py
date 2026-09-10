"""Deterministic long/short intraday strategies for research."""

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
    if not _ready(row, ("close", "vwap", "atr_pct")) or row.atr_pct <= 0:
        return 0
    distance = row.close / row.vwap - 1.0
    threshold = max(z, row.atr_pct * 0.5)
    if distance < -threshold:
        return 1
    if distance > threshold:
        return -1
    return 0


def volatility_breakout(row: pd.Series) -> int:
    if not _ready(row, ("close", "prior_high", "prior_low", "atr_pct", "atr_pct_mean")):
        return 0
    if row.atr_pct_mean <= 0 or row.atr_pct > row.atr_pct_mean * 0.85:
        return 0
    if row.close > row.prior_high:
        return 1
    if row.close < row.prior_low:
        return -1
    return 0


def trend_pullback(row: pd.Series) -> int:
    if not _ready(row, ("close", "ema_fast", "ema_slow", "atr")) or row.atr <= 0:
        return 0
    distance = abs(row.close - row.ema_fast) / row.atr
    if distance > 0.75:
        return 0
    if row.ema_fast > row.ema_slow and row.close > row.ema_slow:
        return 1
    if row.ema_fast < row.ema_slow and row.close < row.ema_slow:
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
    result = pd.Series(0, index=df.index, dtype=int)
    active = position >= opening_bars
    result.loc[active & (df["close"] > opening_high)] = 1
    result.loc[active & (df["close"] < opening_low)] = -1
    return result


def regime_breakout(row: pd.Series) -> int:
    """Trend breakout requiring directional regime, expansion and confirmation."""
    names = ("close", "prior_high", "prior_low", "ema_fast", "ema_slow", "atr", "atr_pct", "atr_pct_mean", "volume_ratio")
    if not _ready(row, names) or row.atr <= 0 or row.atr_pct_mean <= 0:
        return 0
    expansion = row.atr_pct >= row.atr_pct_mean * 0.95
    volume_ok = row.volume_ratio >= 1.10
    if not (expansion and volume_ok):
        return 0
    if row.close > row.prior_high + row.atr * 0.10 and row.ema_fast > row.ema_slow:
        return 1
    if row.close < row.prior_low - row.atr * 0.10 and row.ema_fast < row.ema_slow:
        return -1
    return 0


def pullback_continuation(row: pd.Series) -> int:
    """Enter only when a pullback is aligned with a sufficiently strong trend."""
    names = ("close", "ema_fast", "ema_slow", "atr", "trend_strength", "momentum", "volume_ratio")
    if not _ready(row, names) or row.atr <= 0:
        return 0
    if row.trend_strength < 0.75 or row.volume_ratio < 0.90:
        return 0
    near_ema = abs(row.close - row.ema_fast) <= row.atr * 0.45
    if not near_ema:
        return 0
    if row.ema_fast > row.ema_slow and row.momentum > 0:
        return 1
    if row.ema_fast < row.ema_slow and row.momentum < 0:
        return -1
    return 0


def regime_vwap_reversion(row: pd.Series) -> int:
    """Mean-revert only in weak-trend regimes, avoiding fades of strong trends."""
    names = ("close", "vwap", "atr", "trend_strength", "atr_pct_mean")
    if not _ready(row, names) or row.atr <= 0 or row.atr_pct_mean <= 0:
        return 0
    if row.trend_strength > 0.65:
        return 0
    distance = row.close - row.vwap
    threshold = max(row.atr * 0.60, row.close * 0.0015)
    if distance < -threshold:
        return 1
    if distance > threshold:
        return -1
    return 0


def momentum_regime(row: pd.Series) -> int:
    """Trade persistent momentum only when trend and volume agree."""
    names = ("close", "ema_fast", "ema_slow", "trend_strength", "momentum", "volume_ratio", "atr_pct", "atr_pct_mean")
    if not _ready(row, names) or row.atr_pct_mean <= 0:
        return 0
    if row.trend_strength < 0.60 or row.volume_ratio < 1.05:
        return 0
    if not (0.75 * row.atr_pct_mean <= row.atr_pct <= 2.50 * row.atr_pct_mean):
        return 0
    if row.ema_fast > row.ema_slow and row.momentum > 0.002:
        return 1
    if row.ema_fast < row.ema_slow and row.momentum < -0.002:
        return -1
    return 0


STRATEGIES = {
    "ema_trend": ema_trend,
    "momentum_breakout": momentum_breakout,
    "vwap_reversion": vwap_reversion,
    "volatility_breakout": volatility_breakout,
    "trend_pullback": trend_pullback,
    "regime_breakout": regime_breakout,
    "pullback_continuation": pullback_continuation,
    "regime_vwap_reversion": regime_vwap_reversion,
    "momentum_regime": momentum_regime,
    "orb_breakout": or_breakout,
}
