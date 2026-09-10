import pandas as pd

from app.research.robustness import monte_carlo_trade_order
from app.research.walk_forward import stress_configs, walk_forward_splits
from app.backtest.engine import BacktestConfig


def test_walk_forward_is_chronological_and_non_overlapping():
    idx = pd.date_range("2026-01-01", periods=20, freq="h")
    splits = list(walk_forward_splits(idx, train_bars=5, test_bars=3))
    assert splits
    for train, test in splits:
        assert train[-1] < test[0]


def test_stress_configs_double_costs_and_slippage():
    base = BacktestConfig(fee_bps_per_side=5, slippage_bps=2)
    scenarios = stress_configs(base)
    assert scenarios["cost_2x"].fee_bps_per_side == 10
    assert scenarios["slippage_2x"].slippage_bps == 4
    assert scenarios["cost_slippage_2x"].fee_bps_per_side == 10
    assert scenarios["cost_slippage_2x"].slippage_bps == 4


def test_monte_carlo_is_deterministic():
    trades = pd.DataFrame({"net_pnl": [10.0, -5.0, 7.0, -2.0, 4.0]})
    first = monte_carlo_trade_order(trades, simulations=100, seed=42)
    second = monte_carlo_trade_order(trades, simulations=100, seed=42)
    assert first == second
    assert first["simulations"] == 100
