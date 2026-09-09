"""Chronological walk-forward and cost-stress evaluation helpers."""

from __future__ import annotations

from dataclasses import replace
from collections.abc import Callable

import pandas as pd

from app.analytics.performance import summarize_performance
from app.backtest.engine import BacktestConfig, run_backtest
from app.signals.runner import generate_signals


def walk_forward_splits(index: pd.Index, train_bars: int, test_bars: int, step_bars: int | None = None):
    """Yield non-overlapping chronological train/test windows."""
    if train_bars <= 0 or test_bars <= 0:
        raise ValueError("train_bars and test_bars must be positive")
    step = step_bars or test_bars
    if step <= 0:
        raise ValueError("step_bars must be positive")
    start = 0
    while start + train_bars + test_bars <= len(index):
        train = index[start : start + train_bars]
        test_start = start + train_bars
        test = index[test_start : test_start + test_bars]
        yield train, test
        start += step


def evaluate_walk_forward(
    bars: pd.DataFrame,
    strategy: str,
    config: BacktestConfig,
    train_bars: int,
    test_bars: int,
    step_bars: int | None = None,
) -> pd.DataFrame:
    """Evaluate fixed strategy rules on sequential out-of-sample windows."""
    rows = []
    for n, (train_idx, test_idx) in enumerate(walk_forward_splits(bars.index, train_bars, test_bars, step_bars), 1):
        # Training data is deliberately retained as metadata only for fixed rules.
        test = bars.loc[test_idx]
        signals = generate_signals(test, strategy)
        trades, equity = run_backtest(test, signals, config)
        metrics = summarize_performance(trades, equity, config.initial_capital)
        metrics.update({"window": n, "train_start": train_idx[0], "train_end": train_idx[-1], "test_start": test_idx[0], "test_end": test_idx[-1], "strategy": strategy})
        rows.append(metrics)
    return pd.DataFrame(rows)


def stress_configs(base: BacktestConfig) -> dict[str, BacktestConfig]:
    """Return conservative cost/slippage scenarios for robustness checks."""
    return {
        "base": base,
        "cost_2x": replace(base, brokerage_per_side=base.brokerage_per_side * 2, fee_bps_per_side=base.fee_bps_per_side * 2, fixed_cost_per_side=base.fixed_cost_per_side * 2),
        "slippage_2x": replace(base, slippage_bps=base.slippage_bps * 2),
        "cost_slippage_2x": replace(base, brokerage_per_side=base.brokerage_per_side * 2, fee_bps_per_side=base.fee_bps_per_side * 2, fixed_cost_per_side=base.fixed_cost_per_side * 2, slippage_bps=base.slippage_bps * 2),
    }


def evaluate_stress(bars: pd.DataFrame, strategy: str, base: BacktestConfig) -> pd.DataFrame:
    """Run identical signals under increasingly adverse execution assumptions."""
    signals = generate_signals(bars, strategy)
    rows = []
    for scenario, config in stress_configs(base).items():
        trades, equity = run_backtest(bars, signals, config)
        row = summarize_performance(trades, equity, config.initial_capital)
        row.update({"scenario": scenario, "strategy": strategy})
        rows.append(row)
    return pd.DataFrame(rows)
