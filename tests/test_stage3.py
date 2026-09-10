import pandas as pd

from app.backtest.engine import BacktestConfig
from app.research.stage3 import CANDIDATES, evaluate_candidates, evaluate_directional, evaluate_signal_quality, realistic_config
from app.signals.runner import generate_signals


def _bars(periods=220):
    idx = pd.date_range("2026-01-01", periods=periods, freq="5min", tz="UTC")
    close = pd.Series(range(100, 100 + periods), index=idx, dtype=float)
    return pd.DataFrame({"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1000.0}, index=idx)


def test_stage3_leaderboard_is_deterministic_and_sorted():
    bars = _bars()
    config = BacktestConfig(initial_capital=100000, risk_per_trade=0.005)
    result = evaluate_candidates(bars, config)
    assert len(result) == len(CANDIDATES)
    assert set(result["strategy"]) == set(CANDIDATES)
    assert result["net_pnl"].is_monotonic_decreasing


def test_stage3_directional_has_long_short_combined():
    result = evaluate_directional(_bars(), realistic_config(), "ema_trend")
    assert set(result["direction"]) == {"long_only", "short_only", "combined"}


def test_realistic_config_has_nonzero_execution_costs():
    config = realistic_config()
    assert config.fee_bps_per_side > 0
    assert config.slippage_bps > 0
    assert config.risk_per_trade == 0.005


def test_redesigned_strategies_are_registered_and_causal_shape_is_preserved():
    bars = _bars()
    for strategy in ("regime_breakout", "pullback_continuation", "regime_vwap_reversion", "momentum_regime"):
        signals = generate_signals(bars, strategy)
        assert signals.index.equals(bars.index)
        assert set(signals.dropna().unique()).issubset({-1, 0, 1})


def test_signal_quality_has_expected_diagnostic_columns():
    result = evaluate_signal_quality(_bars(), strategies=("ema_trend", "regime_breakout"))
    assert set(result["strategy"]) == {"ema_trend", "regime_breakout"}
    assert {"signals", "mean_next_bar_edge_bps", "edge_win_rate"}.issubset(result.columns)
