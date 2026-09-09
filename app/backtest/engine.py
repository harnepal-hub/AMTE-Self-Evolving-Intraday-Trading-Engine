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
    brokerage_per_side: float = 0.0
    fee_bps_per_side: float = 0.0
    fixed_cost_per_side: float = 0.0
    slippage_bps: float = 0.0
    quantity_step: float = 1.0
    lot_size: float = 1.0
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
    quantity: float
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


def _quantity(capital: float, entry: float, cfg: BacktestConfig) -> float:
    risk_cash = capital * cfg.risk_per_trade
    risk_per_unit = entry * cfg.stop_loss_pct * cfg.contract_multiplier
    step = max(cfg.quantity_step, cfg.lot_size)
    if risk_per_unit <= 0 or step <= 0:
        return 0.0
    raw = risk_cash / risk_per_unit
    steps = int(raw / step + 1e-12)
    return max(0.0, steps * step)


def _slipped_price(price: float, side: str, bps: float, is_entry: bool) -> float:
    """Apply adverse execution slippage to a market/stop/target fill."""
    if bps < 0:
        raise ValueError("slippage_bps cannot be negative")
    impact = bps / 10_000
    if side == "LONG":
        direction = 1 if is_entry else -1
    elif side == "SHORT":
        direction = -1 if is_entry else 1
    else:
        raise ValueError(f"Unsupported side: {side}")
    return price * (1 + direction * impact)


def _transaction_cost(price: float, quantity: float, cfg: BacktestConfig) -> float:
    """Calculate one-side trading costs from notional plus fixed brokerage."""
    if cfg.brokerage_per_side < 0 or cfg.fee_bps_per_side < 0 or cfg.fixed_cost_per_side < 0:
        raise ValueError("Trading costs cannot be negative")
    notional = price * quantity * cfg.contract_multiplier
    return (
        cfg.brokerage_per_side
        + notional * cfg.fee_bps_per_side / 10_000
        + cfg.fixed_cost_per_side
    )


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


def _gross_pnl(side: str, entry: float, exit_price: float, quantity: float, multiplier: float) -> float:
    gross = (exit_price - entry) * quantity * multiplier
    return gross if side == "LONG" else -gross


def run_backtest(
    bars: pd.DataFrame,
    signals: pd.Series,
    config: Optional[BacktestConfig] = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run a conservative OHLC backtest with long and short support.

    A signal on bar *t* is executed at bar *t+1* open. Stops and targets are
    checked using OHLC and, when both are touched in one bar, the stop wins.
    If a bar gaps through a stop/target, the fill is the bar open (then
    adverse slippage is applied). Session-end exits take precedence on the
    first bar at/after the configured close. Entry and exit trading costs are
    charged separately using the configured per-side brokerage/fee model.
    """
    cfg = config or BacktestConfig()
    required = {"open", "high", "low", "close"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"Missing OHLC columns: {sorted(missing)}")
    if cfg.initial_capital <= 0 or not 0 < cfg.risk_per_trade <= 1:
        raise ValueError("initial_capital must be positive and risk_per_trade must be in (0, 1]")
    if cfg.stop_loss_pct <= 0 or cfg.take_profit_pct <= 0:
        raise ValueError("stop_loss_pct and take_profit_pct must be positive")
    if cfg.max_daily_loss_pct is not None and not 0 < cfg.max_daily_loss_pct < 1:
        raise ValueError("max_daily_loss_pct must be in (0, 1)")
    if cfg.cooldown_bars < 0:
        raise ValueError("cooldown_bars cannot be negative")
    if cfg.quantity_step <= 0 or cfg.lot_size <= 0 or cfg.contract_multiplier <= 0:
        raise ValueError("quantity_step, lot_size and contract_multiplier must be positive")
    if cfg.slippage_bps < 0:
        raise ValueError("slippage_bps cannot be negative")

    df = bars.copy().sort_index()
    if df.empty:
        return pd.DataFrame(), pd.DataFrame(columns=["equity"])
    sig = signals.reindex(df.index).fillna(0).astype(int)
    if not sig.isin([-1, 0, 1]).all():
        raise ValueError("Signals must contain only -1, 0, or 1")

    capital = cfg.initial_capital
    equity_rows: list[dict] = []
    trades: list[Trade] = []
    position = None
    last_exit_i = -10**9
    day_start_capital = capital
    daily_loss_locked = False

    for i in range(len(df)):
        row = df.iloc[i]
        ts = df.index[i]
        current_date = ts.date()
        if i == 0 or df.index[i - 1].date() != current_date:
            day_start_capital = capital
            daily_loss_locked = False

        if position is not None:
            side = position["side"]
            sl = position["sl"]
            tp = position["tp"]
            exit_price = None
            reason = None

            # Once the bar reaches/passes the configured session close, the
            # position must be squared off rather than allowing a same-bar
            # stop/target to win the classification.
            if _session_close_hit(ts, cfg):
                exit_price, reason = float(row.close), "SESSION_END"
            elif side == "LONG":
                # An open beyond a protective level is a gap fill at the open.
                if row.open < sl:
                    exit_price, reason = float(row.open), "STOP_LOSS_GAP"
                elif row.open > tp:
                    exit_price, reason = float(row.open), "TAKE_PROFIT_GAP"
                elif row.low <= sl:
                    exit_price, reason = sl, "STOP_LOSS"
                elif row.high >= tp:
                    exit_price, reason = tp, "TAKE_PROFIT"
            else:
                if row.open > sl:
                    exit_price, reason = float(row.open), "STOP_LOSS_GAP"
                elif row.open < tp:
                    exit_price, reason = float(row.open), "TAKE_PROFIT_GAP"
                elif row.high >= sl:
                    exit_price, reason = sl, "STOP_LOSS"
                elif row.low <= tp:
                    exit_price, reason = tp, "TAKE_PROFIT"

            if exit_price is not None:
                fill = _slipped_price(float(exit_price), side, cfg.slippage_bps, False)
                qty = position["quantity"]
                gross = _gross_pnl(side, position["entry_price"], fill, qty, cfg.contract_multiplier)
                exit_cost = _transaction_cost(fill, qty, cfg)
                entry_cost = position["entry_cost"]
                costs = entry_cost + exit_cost
                net = gross - exit_cost
                capital += net
                trades.append(Trade(
                    position["entry_time"], ts, side,
                    position["entry_price"], fill, qty,
                    gross, costs, net, reason,
                ))
                position = None
                last_exit_i = i

        can_enter = (
            position is None
            and not daily_loss_locked
            and not _session_close_hit(ts, cfg)
            and i > 0
            and i - last_exit_i > cfg.cooldown_bars
            and sig.iloc[i - 1] in (1, -1)
        )
        if can_enter:
            side = "LONG" if sig.iloc[i - 1] == 1 else "SHORT"
            entry = _slipped_price(float(row.open), side, cfg.slippage_bps, True)
            qty = _quantity(capital, entry, cfg)
            if qty > 0:
                entry_cost = _transaction_cost(entry, qty, cfg)
                capital -= entry_cost
                sl, tp = _levels(side, entry, cfg)
                position = {
                    "side": side,
                    "entry_time": ts,
                    "entry_price": entry,
                    "quantity": qty,
                    "sl": sl,
                    "tp": tp,
                    "entry_cost": entry_cost,
                }

        unrealized = 0.0
        if position is not None:
            mark = float(row.close)
            unrealized = _gross_pnl(position["side"], position["entry_price"], mark, position["quantity"], cfg.contract_multiplier)

        equity = capital + unrealized
        if (
            position is not None
            and cfg.max_daily_loss_pct is not None
            and equity <= day_start_capital * (1 - cfg.max_daily_loss_pct)
        ):
            fill = _slipped_price(float(row.close), position["side"], cfg.slippage_bps, False)
            qty = position["quantity"]
            gross = _gross_pnl(position["side"], position["entry_price"], fill, qty, cfg.contract_multiplier)
            exit_cost = _transaction_cost(fill, qty, cfg)
            costs = position["entry_cost"] + exit_cost
            net = gross - exit_cost
            capital += net
            trades.append(Trade(
                position["entry_time"], ts, position["side"],
                position["entry_price"], fill, qty,
                gross, costs, net, "DAILY_LOSS_LIMIT",
            ))
            position = None
            last_exit_i = i
            daily_loss_locked = True
            equity = capital

        equity_rows.append({"timestamp": ts, "equity": equity})

    if position is not None:
        row = df.iloc[-1]
        side = position["side"]
        fill = _slipped_price(float(row.close), side, cfg.slippage_bps, False)
        qty = position["quantity"]
        gross = _gross_pnl(side, position["entry_price"], fill, qty, cfg.contract_multiplier)
        exit_cost = _transaction_cost(fill, qty, cfg)
        costs = position["entry_cost"] + exit_cost
        net = gross - exit_cost
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
