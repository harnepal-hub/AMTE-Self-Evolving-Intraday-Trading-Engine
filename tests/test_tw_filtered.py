import numpy as np
import pandas as pd

from app.signals.tw_all_in_one_filtered import filtered_signals, tw_base


def make_bars(n=500):
    idx = pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC")
    close = 100 + np.cumsum(np.sin(np.arange(n) / 7) * 0.15 + 0.03)
    return pd.DataFrame({
        "open": close,
        "high": close + 0.2,
        "low": close - 0.2,
        "close": close,
        "volume": 1000 + 100 * (np.arange(n) % 10),
    }, index=idx)


def test_base_signal_is_causal_shape():
    bars = make_bars()
    s = tw_base(bars, 16)
    assert len(s) == len(bars)
    assert set(s.dropna().unique()).issubset({-1, 0, 1})


def test_filters_only_reduce_or_equal_signals():
    bars = make_bars()
    base = filtered_signals(bars, hull_length=16, ema_length=100, ema_filter=False, slope_filter=False)
    filtered = filtered_signals(
        bars, hull_length=16, ema_length=100, ema_filter=True, slope_filter=True,
        volume_ratio_min=1.2, atr_expansion_min=1.1, ema_distance_min=0.003,
        cooldown_bars=3,
    )
    assert int((filtered != 0).sum()) <= int((base != 0).sum())


def test_no_lookahead_prefix_invariance():
    bars = make_bars()
    a = filtered_signals(bars, hull_length=12, ema_length=100, ema_filter=True, slope_filter=True)
    prefix = bars.iloc[:350]
    b = filtered_signals(prefix, hull_length=12, ema_length=100, ema_filter=True, slope_filter=True)
    assert a.iloc[:350].equals(b)
