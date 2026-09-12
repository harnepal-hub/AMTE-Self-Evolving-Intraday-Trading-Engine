"""Run AMTE BTC/USDT futures paper trading from CoinDCX public WebSocket data.

Research-only. Never sends exchange orders.
"""
from __future__ import annotations

import argparse
import csv
import json
import signal
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import socketio

from app.execution.paper import PaperBroker, PaperConfig


@dataclass
class Book:
    bid: float = 0.0
    ask: float = 0.0
    bid_qty: float = 0.0
    ask_qty: float = 0.0
    ts: float = 0.0


class PaperRunner:
    def __init__(self, pair: str, out: Path, duration: int, imbalance: float, cooldown: int):
        self.pair = pair
        self.out = out
        self.duration = duration
        self.imbalance_threshold = imbalance
        self.cooldown = cooldown
        self.book = Book()
        self.last_signal = 0.0
        self.last_trade_ts = 0.0
        self.prices = deque(maxlen=120)
        self.trade_flow = deque(maxlen=120)
        self.start = time.time()
        self.stop = False
        self.events = 0
        self.signals = 0
        self.journal = out / "paper_trades.csv"
        self.out.mkdir(parents=True, exist_ok=True)
        self.broker = PaperBroker(PaperConfig(
            initial_capital=100_000.0,
            risk_per_trade=0.0025,
            max_trades_per_day=5,
            daily_loss_limit_pct=0.02,
            fee_bps_per_side=5.0,
            slippage_bps=2.0,
            stop_loss_pct=0.005,
            take_profit_pct=0.01,
        ))
        self._ensure_journal()

    def _ensure_journal(self):
        if not self.journal.exists():
            with self.journal.open("w", newline="") as f:
                csv.writer(f).writerow([
                    "timestamp", "event", "side", "price", "qty", "bid", "ask",
                    "imbalance", "trade_flow", "capital", "reason"
                ])

    def log(self, event, side="", price=0.0, qty=0.0, imbalance=0.0, flow=0.0, reason=""):
        with self.journal.open("a", newline="") as f:
            csv.writer(f).writerow([
                datetime.now(timezone.utc).isoformat(), event, side, price, qty,
                self.book.bid, self.book.ask, imbalance, flow, self.broker.capital, reason
            ])

    @staticmethod
    def _levels(payload):
        if isinstance(payload, dict):
            bids, asks = payload.get("bids", []), payload.get("asks", [])
        else:
            return [], []
        def norm(levels):
            out = []
            for x in levels or []:
                if isinstance(x, dict):
                    p = x.get("price"); q = x.get("quantity", x.get("qty", x.get("size")))
                else:
                    p, q = (x[0], x[1]) if len(x) >= 2 else (None, None)
                try:
                    if p is not None and q is not None: out.append((float(p), float(q)))
                except (TypeError, ValueError): pass
            return out
        return norm(bids), norm(asks)

    def on_book(self, payload):
        bids, asks = self._levels(payload)
        if not bids or not asks: return
        bid, bq = max(bids, key=lambda x: x[0]); ask, aq = min(asks, key=lambda x: x[0])
        if bid <= 0 or ask <= bid: return
        self.book = Book(bid, ask, bq, aq, time.time())
        self.events += 1
        mid = (bid + ask) / 2
        self.prices.append(mid)
        denom = bq + aq
        imb = (bq - aq) / denom if denom else 0.0
        flow = sum(self.trade_flow) / max(1, len(self.trade_flow))
        now = time.time()
        if now - self.last_signal < self.cooldown: return
        if self.broker.position is not None: return
        if imb >= self.imbalance_threshold and flow >= 0:
            self._enter("long", ask, imb, flow)
        elif imb <= -self.imbalance_threshold and flow <= 0:
            self._enter("short", bid, imb, flow)

    def on_trade(self, payload):
        try:
            qty = float(payload.get("quantity", payload.get("qty", 0)))
            maker = payload.get("is_maker")
            if qty <= 0: return
            # Maker flag is only a directional proxy; do not call it verified aggressor side.
            signed = -qty if bool(maker) else qty
            self.trade_flow.append(signed)
            self.events += 1
        except (TypeError, ValueError): return

    def _enter(self, side, price, imb, flow):
        self.signals += 1
        try:
            self.broker.open_position(side=side, bid=self.book.bid, ask=self.book.ask)
            self.last_signal = time.time()
            self.last_trade_ts = self.last_signal
            self.log("ENTRY", side, price, getattr(self.broker.position, "quantity", 0), imb, flow, "microstructure experimental")
        except Exception as exc:
            self.log("REJECT", side, price, 0, imb, flow, str(exc))

    def status(self):
        pos = self.broker.position
        return {
            "elapsed_s": round(time.time() - self.start, 1),
            "events": self.events,
            "signals": self.signals,
            "capital": round(self.broker.capital, 2),
            "position": getattr(pos, "side", None),
            "trades_today": getattr(self.broker, "trades_today", 0),
        }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair", default="B-BTC_USDT")
    ap.add_argument("--duration", type=int, default=900)
    ap.add_argument("--imbalance", type=float, default=0.35)
    ap.add_argument("--cooldown", type=int, default=30)
    ap.add_argument("--out", type=Path, default=Path("data/paper"))
    args = ap.parse_args()
    r = PaperRunner(args.pair, args.out, args.duration, args.imbalance, args.cooldown)
    sio = socketio.Client(reconnection=True, logger=False, engineio_logger=False)

    @sio.event
    def connect():
        sio.emit("join", {"channelName": f"{args.pair}@orderbook@50-futures"})
        sio.emit("join", {"channelName": f"{args.pair}@trades-futures"})
        r.log("CONNECTED", reason="CoinDCX public futures websocket")

    @sio.event
    def disconnect(): r.log("DISCONNECTED", reason="websocket")

    @sio.on("depth-snapshot")
    def depth(data): r.on_book(data if isinstance(data, dict) else {})

    @sio.on("new-trade")
    def trade(data): r.on_trade(data if isinstance(data, dict) else {})

    def stop(*_): r.stop = True
    signal.signal(signal.SIGINT, stop); signal.signal(signal.SIGTERM, stop)
    sio.connect("https://stream.coindcx.com", transports=["websocket"], wait_timeout=10)
    try:
        while not r.stop and time.time() - r.start < args.duration:
            time.sleep(5)
            print(json.dumps(r.status()))
    finally:
        if sio.connected: sio.disconnect()
        print(json.dumps(r.status()))

if __name__ == "__main__": main()
