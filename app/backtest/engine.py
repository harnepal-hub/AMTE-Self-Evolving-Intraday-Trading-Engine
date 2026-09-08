"""Conservative event-driven OHLC backtest engine."""

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
    slippage_bps: float = 0.0
    quantity_step: int = 1
    contract_multiplier: float = 1.0
    square_off_at_session_end: bool = True
    session_close: str | None = None
    session_timezone: str | None = None
    max_daily_loss_pct: float | None = None
    cooldown_bars: int = 0


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
    if side == "SHORT":
        return entry * (1 + cfg.stop_loss_pct), entry * (1 - cfg.take_profit_pct)
    raise ValueError(f"Unsupported side: {side}")


def _quantity(capital: float, entry: float, cfg: BacktestConfig) -> int:
    risk_cash = capital * cfg.risk_per_trade
    risk_per_unit = entry * cfg.stop_loss_pct * cfg.contract_multiplier
    if risk_per_unit <= 0 or cfg.quantity_step <= 0:
        return 0
    raw = risk_cash / risk_per_unit
    return max(0, int(raw // cfg.quantity_step) * cfg.quantity_step)


def _slipped_price(price: float, side: str, bps: float, is_entry: bool) -> float:
    """Apply adverse execution slippage to a market/stop/target fill."""
    if bps < 0:
        raise ValueError("slippage_bps cannot be negative")
    impact = bps / 10_000
    if side == "LONG":
        direction = 1 if is_entry else -1
    else:
        direction = -1 if is_entry else 1
    return price * (1 + direction * impact)


def _session_close_hit(ts: pd.Timestamp, cfg: BacktestConfig) -> bool:
    if not cfg.square_off_at_session_end or not cfg.session_close:
        return False
    local_ts = ts
    if cfg.session_timezone:
        if local_ts.tzinfo is None:
            local_ts = local_ts.tz_localize(cfg.session_timezone)
        else:
            local_ts = local_ts.tz_convert(cfg.session_timezone)
    close = pd.Timestamp(f"{local_ts.date()} {cfg.session_close}", tz=local_ts.tz)
    return local_ts >= close


def run_backtest(
    bars: pd.DataFrame,
    signals: pd.Series,
    config: Optional[BacktestConfig] = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run a conservative OHLC backtest with long and short support.

    A signal on bar *t* is executed at bar *t+1* open. Stops and targets are
    checked using OHLC and, when both are touched in one bar, the stop wins.
    Session-end exits occur at the first bar at/after the configured close.
    Slippage is adverse and costs are charged on both entry and exit.
    """
    cfg = config or BacktestConfig()
    required = {"open", "high", "low", "close"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"Missing OHLC columns: {sorted(missing)}")
    if cfg.initial_capital <= 0 or not 0 < cfg.risk_per_trade <= 1:
        raise ValueError("initial_capital must be positive and risk_per_trade must be in (0, 1]")

    df = bars.copy().sort_index()
    if df.empty:
        return pd.DataFrame(), pd.DataFrame(columns=["equity"])
    sig = signals.reindex(df.index).fillna(0).astype(int)
    capital = cfg.initial_capital
    equity_rows: list[dict] = []
    trades: list[Trade] = []
    position = None
    last_exit_i = -10**9
    day_start_capital = capital

    for i in range(len(df)):
        row = df.iloc[i]
        ts = df.index[i]
        current_date = ts.date()
        if i == 0 or df.index[i - 1].date() != current_date:
            day_start_capital = capital

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

            if exit_price is None and _session_close_hit(ts, cfg):
                exit_price, reason = float(row.close), "SESSION_END"

            if exit_price is not None:
                fill = _slipped_price(float(exit_price), side, cfg.slippage_bps, False)
                qty = position["quantity"]
                gross = (fill - position["entry_price"]) * qty * cfg.contract_multiplier
                if side == "SHORT":
                    gross = -gross
                costs = cfg.brokerage_per_trade
                net = gross - costs
                capital += net
                trades.append(Trade(
                    position["entry_time"], ts, side,
                    position["entry_price"], fill, qty,
                    gross, costs, net, reason,
                ))
                position = None
                last_exit_i = i

        # Entry is based only on the prior completed bar.
        if position is None and i > 0 and i - last_exit_i > cfg.cooldown_bars and sig.iloc[i - 1] in (1, -1):
            side = "LONG" if sig.iloc[i - 1] == 1 else "SHORT"
            entry = _slipped_price(float(row.open), side, cfg.slippage_bps, True)
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
            unrealized = (mark - position["entry_price"]) * position["quantity"] * cfg.contract_multiplier
            if position["side"] == "SHORT":
                unrealized = -unrealized

        equity = capital + unrealized
        if (
            position is not None
            and cfg.max_daily_loss_pct is not None
            and equity <= day_start_capital * (1 - cfg.max_daily_loss_pct)
        ):
            fill = _slipped_price(float(row.close), position["side"], cfg.slippage_bps, False)
            qty = position["quantity"]
            gross = (fill - position["entry_price"]) * qty * cfg.contract_multiplier
            if position["side"] == "SHORT":
                gross = -gross
            costs = cfg.brokerage_per_trade
            net = gross - costs
            capital += net
            trades.append(Trade(
                position["entry_time"], ts, position["side"],
                position["entry_price"], fill, qty,
                gross, costs, net, "DAILY_LOSS_LIMIT",
            ))
            position = None
            last_exit_i = i
            equity = capital

        equity_rows.append({"timestamp": ts, "equity": equity})

    if position is not None:
        row = df.iloc[-1]
        side = position["side"]
        fill = _slipped_price(float(row.close), side, cfg.slippage_bps, False)
        qty = position["quantity"]
        gross = (fill - position["entry_price"]) * qty * cfg.contract_multiplier
        if side == "SHORT":
            gross = -gross
        costs = cfg.brokerage_per_trade
        net = gross - costs
        capital += net
        trades.append(Trade(
            position["entry_time"], df.index[-1], side,
            position["entry_price"], fill, qty,
            gross, costs, net, "END_OF_DATA",
        ))
        equity_rows[-1]["equity"] = capital

    trade_df = pd.DataFrame([t.__dict__ for t in trades])
    equity_df = pd.DataFrame(equity_rows).set_index("timestamp")
    return trade_df, equity_df
