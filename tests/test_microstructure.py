import numpy as np
import pandas as pd

from app.research.microstructure import make_features, make_target
from scripts.run_ml_microstructure_research import cap_daily


def sample_bars(n=120):
    idx = pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC")
    close = pd.Series(100 + np.linspace(0, 2, n), index=idx)
    return pd.DataFrame({"open": close, "high": close + .2, "low": close - .2, "close": close, "volume": 100.0}, index=idx)


def test_features_are_causal_under_future_perturbation():
    bars = sample_bars()
    x1 = make_features(bars)
    changed = bars.copy()
    changed.iloc[-1, changed.columns.get_loc("close")] *= 1.5
    x2 = make_features(changed)
    pd.testing.assert_frame_equal(x1.iloc[:-1], x2.iloc[:-1])


def test_target_uses_future_and_last_horizon_is_unknown():
    bars = sample_bars()
    y = make_target(bars, horizon_bars=3, min_move_bps=1)
    assert y.iloc[-3:].isna().all()


def test_daily_cap_is_hard_five():
    bars = sample_bars(30)
    signals = pd.Series(1, index=bars.index)
    capped = cap_daily(signals, max_trades=5)
    assert int((capped != 0).sum()) == 5
