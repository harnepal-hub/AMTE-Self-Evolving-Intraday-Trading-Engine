import pandas as pd

from app.features.indicators import add_features
from app.signals.runner import generate_signals


def _bars(periods=80):
    idx = pd.date_range("2026-01-01 09:15", periods=periods, freq="5min")
    close = pd.Series(range(100, 100 + periods), index=idx, dtype=float)
    return pd.DataFrame({
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": 1000.0,
    }, index=idx)


def test_features_do_not_use_future_bars():
    bars = _bars()
    first = add_features(bars)
    altered = bars.copy()
    altered.iloc[-1, altered.columns.get_loc("close")] += 10_000
    second = add_features(altered)
    comparable = first.index[:-1]
    pd.testing.assert_frame_equal(
        first.loc[comparable, ["sma_fast", "sma_slow", "ema_fast", "ema_slow", "atr", "momentum"]],
        second.loc[comparable, ["sma_fast", "sma_slow", "ema_fast", "ema_slow", "atr", "momentum"]],
    )


def test_ema_trend_emits_long_and_short():
    bars = _bars(120)
    up = generate_signals(bars, "ema_trend")
    assert 1 in set(up)

    down = bars.iloc[::-1].copy().sort_index()
    down["close"] = down["close"].iloc[::-1].to_numpy()
    down["open"] = down["close"]
    down["high"] = down["close"] + 1
    down["low"] = down["close"] - 1
    short = generate_signals(down, "ema_trend")
    assert -1 in set(short)


def test_all_research_strategies_return_valid_signal_values():
    bars = _bars(120)
    for name in ("ema_trend", "momentum_breakout", "vwap_reversion", "orb_breakout"):
        signals = generate_signals(bars, name)
        assert signals.index.equals(bars.index)
        assert set(signals.unique()).issubset({-1, 0, 1})
