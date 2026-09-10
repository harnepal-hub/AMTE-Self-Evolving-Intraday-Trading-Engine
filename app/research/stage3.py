"""Stage 3 strategy research utilities.

Runs deterministic candidate strategies through the production backtester and
returns a comparable leaderboard. No parameter fitting or holdout selection is
performed here; those belong to later validation stages.
"""
from __future__ import annotations

from dataclasses import replace
import pandas as pd

from app.analytics.performance import summarize_performance
from app.backtest.engine import BacktestConfig, run_backtest
from app.signals.runner import generate_signals

CANDIDATES = ("ema_trend", "momentum_breakout", "vwap_reversion", "orb_breakout")


def evaluate_candidates(
    bars: pd.DataFrame,
    base_config: BacktestConfig,
    *,
    strategies: tuple[str, ...] = CANDIDATES,
) -> pd.DataFrame:
    """Compare candidates under identical execution/risk assumptions."""
    rows: list[dict] = []
    for strategy in strategies:
        signals = generate_signals(bars, strategy)
        trades, equity = run_backtest(bars, signals, base_config)
        metrics = summarize_performance(trades, equity, base_config.initial_capital)
        metrics.update({"strategy": strategy, "signal_count": int((signals != 0).sum())})
        rows.append(metrics)
    return pd.DataFrame(rows).sort_values(
        ["net_pnl", "profit_factor", "max_drawdown_pct"],
        ascending=[False, False, False],
        ignore_index=True,
    )


def evaluate_directional(
    bars: pd.DataFrame,
    base_config: BacktestConfig,
    strategy: str,
) -> pd.DataFrame:
    """Evaluate the same strategy long-only and short-only."""
    signals = generate_signals(bars, strategy)
    rows = []
    for direction, value in (("long_only", 1), ("short_only", -1), ("combined", None)):
        selected = signals.where(signals == value, 0) if value is not None else signals
        trades, equity = run_backtest(bars, selected, base_config)
        metrics = summarize_performance(trades, equity, base_config.initial_capital)
        metrics.update({"strategy": strategy, "direction": direction})
        rows.append(metrics)
    return pd.DataFrame(rows)


def realistic_config(
    *,
    initial_capital: float = 100_000,
    risk_per_trade: float = 0.005,
    stop_loss_pct: float = 0.005,
    take_profit_pct: float = 0.01,
    fee_bps_per_side: float = 5.0,
    slippage_bps: float = 2.0,
) -> BacktestConfig:
    """Conservative research assumptions; replace with venue-specific costs later."""
    return BacktestConfig(
        initial_capital=initial_capital,
        risk_per_trade=risk_per_trade,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        fee_bps_per_side=fee_bps_per_side,
        slippage_bps=slippage_bps,
        square_off_at_session_end=True,
    )
