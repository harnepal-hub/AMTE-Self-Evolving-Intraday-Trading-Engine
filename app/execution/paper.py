"""Live-quote paper execution simulator; never sends exchange orders."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class PaperConfig:
    initial_capital: float = 100_000.0
    risk_per_trade: float = 0.0025
    stop_pct: float = 0.005
    target_pct: float = 0.01
    fee_bps_per_side: float = 5.0
    slippage_bps: float = 2.0
    max_trades_per_day: int = 5
    max_daily_loss_pct: float = 0.02


@dataclass
class PaperPosition:
    side: str
    entry_time: object
    entry_price: float
    quantity: float
    stop_price: float
    target_price: float
    entry_fee: float


class PaperBroker:
    """Deterministic paper broker using bid/ask quotes and conservative fills."""

    def __init__(self, config: PaperConfig | None = None) -> None:
        self.cfg = config or PaperConfig()
        if self.cfg.initial_capital <= 0 or not 0 < self.cfg.risk_per_trade <= 1:
            raise ValueError("invalid capital or risk")
        if self.cfg.stop_pct <= 0 or self.cfg.target_pct <= 0:
            raise ValueError("stop_pct and target_pct must be positive")
        if self.cfg.max_trades_per_day < 1:
            raise ValueError("max_trades_per_day must be positive")
        self.cash = self.cfg.initial_capital
        self.position: PaperPosition | None = None
        self.day: date | None = None
        self.day_start = self.cash
        self.trades_today = 0
        self.locked = False
        self.realized_pnl = 0.0
        self.journal: list[dict] = []

    def _roll_day(self, ts) -> None:
        d = ts.date() if hasattr(ts, "date") else ts
        if self.day != d:
            self.day = d
            self.day_start = self.cash
            self.trades_today = 0
            self.locked = False

    def _fee(self, price: float, qty: float) -> float:
        return price * qty * self.cfg.fee_bps_per_side / 10_000.0

    def _fill(self, side: str, bid: float, ask: float, entry: bool) -> float:
        if bid <= 0 or ask <= 0 or ask < bid:
            raise ValueError("invalid quote")
        impact = self.cfg.slippage_bps / 10_000.0
        if side == "LONG":
            px = ask if entry else bid
            return px * (1 + impact if entry else 1 - impact)
        if side == "SHORT":
            px = bid if entry else ask
            return px * (1 - impact if entry else 1 + impact)
        raise ValueError("side must be LONG or SHORT")

    def _quantity(self, entry: float) -> float:
        risk_cash = self.cash * self.cfg.risk_per_trade
        risk_unit = entry * self.cfg.stop_pct
        return max(0.0, risk_cash / risk_unit) if risk_unit > 0 else 0.0

    def equity(self, bid: float, ask: float) -> float:
        if self.position is None:
            return self.cash
        mark = (bid + ask) / 2.0
        gross = ((mark - self.position.entry_price) if self.position.side == "LONG" else (self.position.entry_price - mark)) * self.position.quantity
        return self.cash + gross

    def can_enter(self, ts, bid: float, ask: float) -> bool:
        self._roll_day(ts)
        if self.position is not None or self.locked or self.trades_today >= self.cfg.max_trades_per_day:
            return False
        if self.equity(bid, ask) <= self.day_start * (1 - self.cfg.max_daily_loss_pct):
            self.locked = True
            return False
        return True

    def enter(self, ts, signal: int, bid: float, ask: float) -> bool:
        if signal not in (-1, 1) or not self.can_enter(ts, bid, ask):
            return False
        side = "LONG" if signal == 1 else "SHORT"
        px = self._fill(side, bid, ask, True)
        qty = self._quantity(px)
        if qty <= 0:
            return False
        fee = self._fee(px, qty)
        self.cash -= fee
        if side == "LONG":
            stop, target = px * (1 - self.cfg.stop_pct), px * (1 + self.cfg.target_pct)
        else:
            stop, target = px * (1 + self.cfg.stop_pct), px * (1 - self.cfg.target_pct)
        self.position = PaperPosition(side, ts, px, qty, stop, target, fee)
        self.trades_today += 1
        self.journal.append({"event": "ENTRY", "time": ts, "side": side, "price": px, "quantity": qty, "fee": fee})
        return True

    def exit(self, ts, bid: float, ask: float, reason: str = "SIGNAL") -> dict | None:
        if self.position is None:
            return None
        p = self.position
        px = self._fill(p.side, bid, ask, False)
        gross = ((px - p.entry_price) if p.side == "LONG" else (p.entry_price - px)) * p.quantity
        fee = self._fee(px, p.quantity)
        net = gross - p.entry_fee - fee
        self.cash += gross - fee
        self.realized_pnl += net
        row = {"event": "EXIT", "time": ts, "side": p.side, "entry_price": p.entry_price, "exit_price": px, "quantity": p.quantity, "gross_pnl": gross, "fees": p.entry_fee + fee, "net_pnl": net, "reason": reason}
        self.journal.append(row)
        self.position = None
        if self.cash <= self.day_start * (1 - self.cfg.max_daily_loss_pct):
            self.locked = True
        return row

    def check_risk_exits(self, ts, bid: float, ask: float) -> dict | None:
        if self.position is None:
            return None
        p = self.position
        if p.side == "LONG":
            if bid <= p.stop_price:
                return self.exit(ts, bid, ask, "STOP_LOSS")
            if bid >= p.target_price:
                return self.exit(ts, bid, ask, "TAKE_PROFIT")
        else:
            if ask >= p.stop_price:
                return self.exit(ts, bid, ask, "STOP_LOSS")
            if ask <= p.target_price:
                return self.exit(ts, bid, ask, "TAKE_PROFIT")
        return None
