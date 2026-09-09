"""Trade-order Monte Carlo stress tests for Stage 5."""

from __future__ import annotations

import numpy as np
import pandas as pd


def monte_carlo_trade_order(trades: pd.DataFrame, *, simulations: int = 2000, seed: int = 42) -> dict:
    """Randomize the observed trade order and report terminal/drawdown risk."""
    if simulations <= 0:
        raise ValueError("simulations must be positive")
    if trades.empty:
        return {"simulations": 0, "median_terminal_pnl": 0.0, "worst_terminal_pnl": 0.0, "worst_drawdown": 0.0}
    pnl = trades["net_pnl"].astype(float).to_numpy()
    rng = np.random.default_rng(seed)
    terminal = np.empty(simulations)
    drawdowns = np.empty(simulations)
    for i in range(simulations):
        path = rng.permutation(pnl).cumsum()
        peak = np.maximum.accumulate(np.r_[0.0, path])
        dd = np.min(np.r_[0.0, path] - peak)
        terminal[i] = path[-1]
        drawdowns[i] = dd
    return {
        "simulations": simulations,
        "median_terminal_pnl": round(float(np.median(terminal)), 2),
        "p05_terminal_pnl": round(float(np.percentile(terminal, 5)), 2),
        "worst_terminal_pnl": round(float(terminal.min()), 2),
        "median_max_drawdown": round(float(np.median(drawdowns)), 2),
        "p05_max_drawdown": round(float(np.percentile(drawdowns, 5)), 2),
        "worst_drawdown": round(float(drawdowns.min()), 2),
    }
