"""Stage 3 strategy research utilities."""
from __future__ import annotations

import pandas as pd

from app.analytics.performance import summarize_performance
from app.backtest.engine import BacktestConfig, run_backtest
from app.signals.runner import generate_signals

CANDIDATES = (
    "ema_trend",
    "momentum_breakout",
    "vwap_reversion",
    "volatility_breakout",
    "trend_pullback",
    "orb_breakout",
    "regime_breakout",
    "pullback_continuation",
    "regime_vwap_reversion",
    "momentum_regime",
)


def evaluate_candidates(bars: pd.DataFrame, base_config: BacktestConfig, *, strategies: tuple[str, ...] = CANDIDATES) -> pd.DataFrame:
    """Compare candidates under identical execution/risk assumptions."""
    rows: list[dict] = []
    for strategy in strategies:
        signals = generate_signals(bars, strategy)
        trades, equity = run_backtest(bars, signals, base_config)
        metrics = summarize_performance(trades, equity, base_config.initial_capital)
        metrics.update({"strategy": strategy, "signal_count": int((signals != 0).sum())})
        rows.append(metrics)
    return pd.DataFrame(rows).sort_values(
        ["net_pnl", "profit_factor", "max_drawdown_pct"], ascending=[False, False, True], ignore_index=True
    )


def evaluate_signal_quality(bars: pd.DataFrame, *, strategies: tuple[str, ...] = CANDIDATES) -> pd.DataFrame:
    """Measure raw next-bar directional edge before fees, slippage and exits.

    The signal is formed on bar t and the next tradable price is bar t+1 open,
    matching the production backtest's execution convention. This diagnostic is
    deliberately independent of the stop/target model so it can expose whether
    losses originate in signal direction or execution/exit assumptions.
    """
    next_open = bars["open"].shift(-1)
    forward_return = next_open / bars["close"] - 1.0
    rows: list[dict] = []
    for strategy in strategies:
        signals = generate_signals(bars, strategy)
        valid = signals.ne(0) & forward_return.notna()
        signed = forward_return.where(valid) * signals.where(valid)
        long_mask = valid & signals.eq(1)
        short_mask = valid & signals.eq(-1)
        rows.append({
            "strategy": strategy,
            "signals": int(valid.sum()),
            "long_signals": int(long_mask.sum()),
            "short_signals": int(short_mask.sum()),
            "mean_next_bar_edge_bps": float(signed.mean() * 10_000) if valid.any() else 0.0,
            "median_next_bar_edge_bps": float(signed.median() * 10_000) if valid.any() else 0.0,
            "long_mean_bps": float(forward_return.where(long_mask).mean() * 10_000) if long_mask.any() else 0.0,
            "short_mean_bps": float((-forward_return.where(short_mask)).mean() * 10_000) if short_mask.any() else 0.0,
            "edge_win_rate": float((signed > 0).sum() / valid.sum()) if valid.any() else 0.0,
        })
    return pd.DataFrame(rows).sort_values("mean_next_bar_edge_bps", ascending=False, ignore_index=True)


def evaluate_directional(bars: pd.DataFrame, base_config: BacktestConfig, strategy: str) -> pd.DataFrame:
    """Evaluate the same strategy long-only, short-only and combined."""
    signals = generate_signals(bars, strategy)
    rows = []
    for direction, value in (("long_only", 1), ("short_only", -1), ("combined", None)):
        selected = signals.where(signals == value, 0) if value is not None else signals
        trades, equity = run_backtest(bars, selected, base_config)
        metrics = summarize_performance(trades, equity, base_config.initial_capital)
        metrics.update({"strategy": strategy, "direction": direction})
        rows.append(metrics)
    return pd.DataFrame(rows)


def realistic_config(*, initial_capital: float = 100_000, risk_per_trade: float = 0.005, stop_loss_pct: float = 0.005, take_profit_pct: float = 0.01, fee_bps_per_side: float = 5.0, slippage_bps: float = 2.0) -> BacktestConfig:
    """Conservative common assumptions for research comparisons."""
    return BacktestConfig(
        initial_capital=initial_capital,
        risk_per_trade=risk_per_trade,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        fee_bps_per_side=fee_bps_per_side,
        slippage_bps=slippage_bps,
        square_off_at_session_end=True,
    )
