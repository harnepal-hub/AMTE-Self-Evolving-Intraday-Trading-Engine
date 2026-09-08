from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass(frozen=True)
class BacktestConfig:
    initial_capital: float = 100_000.0
    risk_per_trade: float = 0.01
    stop_loss_pct: float = 0.005
    take_profit_pct: float = 0.01
    brokerage_per_trade: float = 0.0
    square_off_at_session_end: bool = True


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    side: str
    entry_price: float
    exit_price: float
    quantity: int
    gross_pnl: float
    costs: float
    net_pnl: float
    exit_reason: str


def _levels(side: str, entry: float, cfg: BacktestConfig) -> tuple[float, float]:
    if side == "LONG":
        return entry * (1 - cfg.stop_loss_pct), entry * (1 + cfg.take_profit_pct)
    return entry * (1 + cfg.stop_loss_pct), entry * (1 - cfg.take_profit_pct)


def _quantity(capital: float, entry: float, cfg: BacktestConfig) -> int:
    risk_cash = capital * cfg.risk_per_trade
    risk_per_share = entry * cfg.stop_loss_pct
    if risk_per_share <= 0:
        return 0
    return max(0, int(risk_cash / risk_per_share))


def run_backtest(
    bars: pd.DataFrame,
    signals: pd.Series,
    config: Optional[BacktestConfig] = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run a conservative OHLC backtest.

    Signal at bar t is executed at the NEXT bar open. This avoids using the
    completed bar's information at an earlier price. If both SL and TP are
    touched in the same bar, SL is assumed to execute first (conservative).
    """
    cfg = config or BacktestConfig()
    required = {"open", "high", "low", "close"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"Missing OHLC columns: {sorted(missing)}")

    df = bars.copy().sort_index()
    sig = signals.reindex(df.index).fillna(0).astype(int)
    capital = cfg.initial_capital
    equity_rows = []
    trades: list[Trade] = []
    position = None

    for i in range(len(df)):
        row = df.iloc[i]
        ts = df.index[i]

        if position is not None:
            side = position["side"]
            sl = position["sl"]
            tp = position["tp"]
            exit_price = None
            reason = None

            if side == "LONG":
                if row.low <= sl:
                    exit_price, reason = sl, "STOP_LOSS"
                elif row.high >= tp:
                    exit_price, reason = tp, "TAKE_PROFIT"
            else:
                if row.high >= sl:
                    exit_price, reason = sl, "STOP_LOSS"
                elif row.low <= tp:
                    exit_price, reason = tp, "TAKE_PROFIT"

            if exit_price is not None:
                qty = position["quantity"]
                gross = (exit_price - position["entry_price"]) * qty
                if side == "SHORT":
                    gross = -gross
                costs = cfg.brokerage_per_trade * 2
                net = gross - costs
                capital += net
                trades.append(
                    Trade(
                        position["entry_time"], ts, side,
                        position["entry_price"], exit_price, qty,
                        gross, costs, net, reason,
                    )
                )
                position = None

        # Entry occurs on the next bar open, based on the prior completed bar.
        if position is None and i > 0 and sig.iloc[i - 1] in (1, -1):
            side = "LONG" if sig.iloc[i - 1] == 1 else "SHORT"
            entry = float(row.open)
            qty = _quantity(capital, entry, cfg)
            if qty > 0:
                sl, tp = _levels(side, entry, cfg)
                position = {
                    "side": side,
                    "entry_time": ts,
                    "entry_price": entry,
                    "quantity": qty,
                    "sl": sl,
                    "tp": tp,
                }

        unrealized = 0.0
        if position is not None:
            mark = float(row.close)
            unrealized = (mark - position["entry_price"]) * position["quantity"]
            if position["side"] == "SHORT":
                unrealized = -unrealized
        equity_rows.append({"timestamp": ts, "equity": capital + unrealized})

    # Force-close any remaining position at the final close.
    if position is not None:
        row = df.iloc[-1]
        exit_price = float(row.close)
        qty = position["quantity"]
        gross = (exit_price - position["entry_price"]) * qty
        if position["side"] == "SHORT":
            gross = -gross
        costs = cfg.brokerage_per_trade * 2
        net = gross - costs
        capital += net
        trades.append(
            Trade(
                position["entry_time"], df.index[-1], position["side"],
                position["entry_price"], exit_price, qty,
                gross, costs, net, "END_OF_DATA",
            )
        )
        equity_rows[-1]["equity"] = capital

    trade_df = pd.DataFrame([t.__dict__ for t in trades])
    equity_df = pd.DataFrame(equity_rows).set_index("timestamp")
    return trade_df, equity_df
