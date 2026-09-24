"""Live BTC/USDT paper trader using the supplied TW All in One signal.

Research-only: public CoinDCX market data in, simulated fills out. No exchange
order placement is implemented here.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Allow `python scripts/run_paper_trading.py` from the repository root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import socketio

from app.execution.paper import PaperBroker, PaperConfig
from app.signals.tw_all_in_one import TWAllInOneSignal


@dataclass
class Book:
    bid: float = 0.0
    ask: float = 0.0
    bid_qty: float = 0.0
    ask_qty: float = 0.0


class CandleBuilder:
    """Build UTC 5-minute candles from live trade ticks."""
    def __init__(self):
        self.bucket = None
        self.o = self.h = self.l = self.c = None
        self.q = 0.0

    def update(self, price: float, qty: float, ts_ms: int):
        bucket = int(ts_ms // 300_000) * 300_000
        if self.bucket is None:
            self.bucket = bucket
        if bucket != self.bucket:
            finished = None
            if self.c is not None:
                finished = (self.bucket, self.o, self.h, self.l, self.c, self.q)
            self.bucket = bucket
            self.o = self.h = self.l = self.c = price
            self.q = qty
            return finished
        if self.c is None:
            self.o = self.h = self.l = self.c = price
        else:
            self.h = max(self.h, price); self.l = min(self.l, price); self.c = price
        self.q += qty
        return None


class TWPaperRunner:
    def __init__(self, pair: str, out: Path, duration: int, length: int,
                 ema_filter: bool, slope_filter: bool, cooldown_bars: int):
        self.pair = pair
        self.out = out
        self.duration = duration
        self.signal = TWAllInOneSignal(length=length, ema_length=100,
                                       ema_filter=ema_filter, slope_filter=slope_filter)
        self.broker = PaperBroker(PaperConfig(
            initial_capital=100_000.0, risk_per_trade=0.0025,
            stop_pct=0.005, target_pct=0.01,
            fee_bps_per_side=5.0, slippage_bps=2.0,
            max_trades_per_day=10, max_daily_loss_rs=2000.0,
        ))
        self.book = Book()
        self.candles = CandleBuilder()
        self.last_signal_bar = -10**9
        self.bar_index = -1
        self.start = time.time()
        self.stop = False
        self.events = 0
        self.signals = 0
        self.out.mkdir(parents=True, exist_ok=True)
        self.journal = self.out / "tw_all_in_one_paper.jsonl"

    @staticmethod
    def _levels(levels):
        if isinstance(levels, dict):
            out = []
            for p, q in levels.items():
                try: out.append((float(p), float(q)))
                except (TypeError, ValueError): pass
            return out
        out = []
        for x in levels or []:
            if isinstance(x, dict):
                p = x.get("price"); q = x.get("quantity", x.get("qty", x.get("size")))
            elif isinstance(x, (list, tuple)) and len(x) >= 2:
                p, q = x[0], x[1]
            else: continue
            try: out.append((float(p), float(q)))
            except (TypeError, ValueError): pass
        return out

    def _write(self, row):
        with self.journal.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")

    def on_depth(self, response):
        data = response.get("data", response) if isinstance(response, dict) else {}
        bids = self._levels(data.get("bids", {})); asks = self._levels(data.get("asks", {}))
        if not bids or not asks: return
        bid, bq = max(bids, key=lambda x: x[0]); ask, aq = min(asks, key=lambda x: x[0])
        if bid <= 0 or ask <= bid: return
        self.book = Book(bid, ask, bq, aq)
        self.events += 1
        self.broker.check_risk_exits(datetime.now(timezone.utc), bid, ask)

    def on_trade(self, response):
        data = response.get("data", response) if isinstance(response, dict) else {}
        try:
            price = float(data.get("p")); qty = float(data.get("q"))
            ts = int(data.get("T") or time.time() * 1000)
        except (TypeError, ValueError): return
        self.events += 1
        finished = self.candles.update(price, qty, ts)
        if finished is None: return
        _, _, _, _, close, volume = finished
        self.bar_index += 1
        sig = self.signal.update(close)
        now = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        if sig and self.bar_index - self.last_signal_bar >= 1:
            self.signals += 1
            self.last_signal_bar = self.bar_index
            if self.broker.position is not None:
                if (sig == 1 and self.broker.position.side == "SHORT") or (sig == -1 and self.broker.position.side == "LONG"):
                    self.broker.exit(now, self.book.bid, self.book.ask, "TW_OPPOSITE")
            elif self.book.bid > 0 and self.book.ask > self.book.bid and self.broker.can_enter(now, self.book.bid, self.book.ask):
                self.broker.enter(now, sig, self.book.bid, self.book.ask)
        self._write({"event": "BAR", "time": now.isoformat(), "close": close,
                     "volume": volume, "signal": sig, "capital": self.broker.cash,
                     "trades_today": self.broker.trades_today})

    def status(self):
        return {"elapsed_s": round(time.time() - self.start, 1),
                "events": self.events, "bars": self.bar_index + 1,
                "signals": self.signals, "cash": round(self.broker.cash, 2),
                "realized_pnl": round(self.broker.realized_pnl, 2),
                "position": self.broker.position.side if self.broker.position else None,
                "trades_today": self.broker.trades_today}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair", default="B-BTC_USDT")
    ap.add_argument("--duration", type=int, default=3600)
    ap.add_argument("--length", type=int, default=16)
    ap.add_argument("--ema-filter", action="store_true")
    ap.add_argument("--slope-filter", action="store_true")
    ap.add_argument("--cooldown-bars", type=int, default=1)
    ap.add_argument("--out", type=Path, default=Path("data/paper"))
    args = ap.parse_args()
    r = TWPaperRunner(args.pair, args.out, args.duration, args.length,
                      args.ema_filter, args.slope_filter, args.cooldown_bars)
    sio = socketio.Client(reconnection=True, logger=False, engineio_logger=False)

    @sio.event
    def connect():
        sio.emit("join", {"channelName": f"{args.pair}@orderbook@50-futures"})
        sio.emit("join", {"channelName": f"{args.pair}@trades-futures"})
        r._write({"event": "CONNECTED", "time": datetime.now(timezone.utc).isoformat()})

    @sio.event
    def disconnect():
        r._write({"event": "DISCONNECTED", "time": datetime.now(timezone.utc).isoformat()})

    @sio.on("depth-snapshot")
    def depth(data): r.on_depth(data)

    @sio.on("new-trade")
    def trade(data): r.on_trade(data)

    def stop(*_): r.stop = True
    signal.signal(signal.SIGINT, stop); signal.signal(signal.SIGTERM, stop)
    sio.connect("https://stream.coindcx.com", transports=["websocket"], wait_timeout=10)
    try:
        while not r.stop and time.time() - r.start < args.duration:
            time.sleep(10)
            print(json.dumps(r.status()))
    finally:
        if sio.connected: sio.disconnect()
        if r.broker.position is not None and r.book.bid > 0:
            r.broker.exit(datetime.now(timezone.utc), r.book.bid, r.book.ask, "SESSION_END")
        print(json.dumps(r.status()))


if __name__ == "__main__": main()
