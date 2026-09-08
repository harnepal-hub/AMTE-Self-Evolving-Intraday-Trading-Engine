from __future__ import annotations

import pandas as pd


def summarize_performance(trades: pd.DataFrame, equity: pd.DataFrame, initial_capital: float) -> dict:
    if equity.empty:
        return {"trades": 0, "net_pnl": 0.0, "return_pct": 0.0, "max_drawdown_pct": 0.0}

    net_pnl = float(trades["net_pnl"].sum()) if not trades.empty else 0.0
    wins = int((trades["net_pnl"] > 0).sum()) if not trades.empty else 0
    losses = int((trades["net_pnl"] < 0).sum()) if not trades.empty else 0
    gross_profit = float(trades.loc[trades["net_pnl"] > 0, "net_pnl"].sum()) if not trades.empty else 0.0
    gross_loss = float(-trades.loc[trades["net_pnl"] < 0, "net_pnl"].sum()) if not trades.empty else 0.0

    running_max = equity["equity"].cummax()
    drawdown_pct = (equity["equity"] / running_max - 1.0) * 100

    return {
        "trades": int(len(trades)),
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(100 * wins / len(trades), 2) if len(trades) else 0.0,
        "net_pnl": round(net_pnl, 2),
        "return_pct": round(100 * net_pnl / initial_capital, 2),
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss else None,
        "max_drawdown_pct": round(float(drawdown_pct.min()), 2),
        "ending_equity": round(float(equity["equity"].iloc[-1]), 2),
    }
