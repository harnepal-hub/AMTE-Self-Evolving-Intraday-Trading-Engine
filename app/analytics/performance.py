from __future__ import annotations

import numpy as np
import pandas as pd


def _max_consecutive(values: pd.Series, condition) -> int:
    best = run = 0
    for value in values:
        if condition(value):
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def summarize_performance(trades: pd.DataFrame, equity: pd.DataFrame, initial_capital: float) -> dict:
    if initial_capital <= 0:
        raise ValueError("initial_capital must be positive")
    if equity.empty:
        return {"trades": 0, "net_pnl": 0.0, "return_pct": 0.0, "max_drawdown_pct": 0.0}

    pnl = trades["net_pnl"] if not trades.empty else pd.Series(dtype=float)
    net_pnl = float(pnl.sum()) if not pnl.empty else 0.0
    wins = int((pnl > 0).sum()) if not pnl.empty else 0
    losses = int((pnl < 0).sum()) if not pnl.empty else 0
    gross_profit = float(pnl[pnl > 0].sum()) if not pnl.empty else 0.0
    gross_loss = float(-pnl[pnl < 0].sum()) if not pnl.empty else 0.0
    running_max = equity["equity"].cummax()
    drawdown_pct = (equity["equity"] / running_max - 1.0) * 100

    avg_win = float(pnl[pnl > 0].mean()) if wins else 0.0
    avg_loss = float(pnl[pnl < 0].mean()) if losses else 0.0
    expectancy = float(pnl.mean()) if not pnl.empty else 0.0
    daily = equity["equity"].resample("1D").last().dropna().pct_change().dropna()
    sharpe = float(np.sqrt(252) * daily.mean() / daily.std()) if len(daily) > 1 and daily.std() > 0 else None

    result = {
        "trades": int(len(pnl)),
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(100 * wins / len(pnl), 2) if len(pnl) else 0.0,
        "net_pnl": round(net_pnl, 2),
        "return_pct": round(100 * net_pnl / initial_capital, 2),
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss else None,
        "expectancy_per_trade": round(expectancy, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "max_consecutive_losses": _max_consecutive(pnl, lambda x: x < 0),
        "max_consecutive_wins": _max_consecutive(pnl, lambda x: x > 0),
        "max_drawdown_pct": round(float(drawdown_pct.min()), 2),
        "ending_equity": round(float(equity["equity"].iloc[-1]), 2),
        "daily_sharpe_annualized": round(sharpe, 3) if sharpe is not None else None,
    }
    if not trades.empty and "side" in trades:
        result["long_trades"] = int((trades["side"] == "LONG").sum())
        result["short_trades"] = int((trades["side"] == "SHORT").sum())
        result["long_net_pnl"] = round(float(trades.loc[trades["side"] == "LONG", "net_pnl"].sum()), 2)
        result["short_net_pnl"] = round(float(trades.loc[trades["side"] == "SHORT", "net_pnl"].sum()), 2)
    return result
