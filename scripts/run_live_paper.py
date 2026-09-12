"""Run AMTE in LIVE DATA / PAPER EXECUTION mode only.

No authenticated exchange API and no order-placement endpoint are used here.
The runner consumes CoinDCX public futures websocket quotes, creates a deliberately
conservative experimental order-flow signal, and routes fills through PaperBroker.
This is for evidence collection, not a validated production strategy.
"""
from __future__ import annotations

import argparse
import csv
import json
import signal
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import socketio

from app.execution.paper import PaperBroker, PaperConfig


class LivePaperRunner:
    def __init__(self, pair: str, out: Path, duration: int, imbalance_threshold: float,
                 flow_window: int = 40) -> None:
        self.pair = pair
        self.out = out
        self.duration = duration
        self.threshold = imbalance_threshold
        self.flow_window = flow_window
        self.broker = PaperBroker(PaperConfig())
        self.bid = self.ask = None
        self.bid_qty = self.ask_qty = 0.0
        self.flow = deque(maxlen=flow_window)
        self.mid_hist = deque(maxlen=12)
        self.start = time.monotonic()
        self.last_signal = 0
        self.sio = socketio.Client(reconnection=True, reconnection_attempts=5, logger=False, engineio_logger=False)
        self.sio.on("connect", handler=self.on_connect)
        self.sio.on("depth-snapshot", handler=self.on_depth)
        self.sio.on("new-trade", handler=self.on_trade)
        self.sio.on("disconnect", handler=self.on_disconnect)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _levels(payload):
        data = payload.get("data", payload) if isinstance(payload, dict) else {}
        bids = data.get("bids", [])
        asks = data.get("asks", [])
        def top(levels):
            if isinstance(levels, dict):
                vals = [(float(p), float(q)) for p, q in levels.items()]
            else:
                vals = [(float(x[0]), float(x[1])) for x in levels if len(x) >= 2]
            return vals[0] if vals else (None, 0.0)
        return top(bids), top(asks)

    def on_connect(self):
        self.sio.emit("join", {"channelName": f"{self.pair}@orderbook@50-futures"})
        self.sio.emit("join", {"channelName": f"{self.pair}@trades-futures"})
        print(f"CONNECTED {self.pair} {self._now()}", flush=True)

    def on_disconnect(self):
        print("DISCONNECTED", flush=True)

    def on_depth(self, payload):
        try:
            (bp, bq), (ap, aq) = self._levels(payload)
            if bp is None or ap is None or ap < bp:
                return
            self.bid, self.ask, self.bid_qty, self.ask_qty = bp, ap, bq, aq
            self.mid_hist.append((bp + ap) / 2.0)
            self._step()
        except (TypeError, ValueError, KeyError):
            return

    def on_trade(self, payload):
        try:
            data = payload.get("data", payload)
            qty = float(data.get("quantity", data.get("q", 0)))
            if qty <= 0:
                return
            # CoinDCX exposes is_maker. It is retained only as a directional proxy;
            # this is explicitly not claimed to be verified aggressor classification.
            maker = bool(data.get("is_maker", False))
            self.flow.append(-qty if maker else qty)
            self._step()
        except (TypeError, ValueError, KeyError):
            return

    def _signal(self) -> int:
        if self.bid is None or self.ask is None or len(self.flow) < self.flow_window:
            return 0
        total = sum(abs(x) for x in self.flow)
        if total <= 0:
            return 0
        flow_ratio = sum(self.flow) / total
        depth_total = self.bid_qty + self.ask_qty
        depth_imb = (self.bid_qty - self.ask_qty) / depth_total if depth_total else 0.0
        mids = [x for x in self.mid_hist]
        if len(mids) < 6:
            return 0
        momentum = (mids[-1] / mids[-6]) - 1.0
        # Experimental gate: require aligned flow, top-of-book imbalance and
        # non-zero short-term momentum. No claim of historical profitability.
        if flow_ratio >= self.threshold and depth_imb >= self.threshold / 2 and momentum > 0:
            return 1
        if flow_ratio <= -self.threshold and depth_imb <= -self.threshold / 2 and momentum < 0:
            return -1
        return 0

    def _step(self):
        if time.monotonic() - self.start >= self.duration:
            return
        self.broker.check_risk_exits(datetime.now(timezone.utc), self.bid, self.ask)
        sig = self._signal()
        if sig and sig != self.last_signal and self.broker.can_enter(datetime.now(timezone.utc), self.bid, self.ask):
            self.broker.enter(datetime.now(timezone.utc), sig, self.bid, self.ask)
        self.last_signal = sig

    def run(self):
        self.out.mkdir(parents=True, exist_ok=True)
        print(f"LIVE PAPER ONLY | {self.pair} | {self.duration}s | capital=100000 | risk=0.25% | max_trades=5", flush=True)
        self.sio.connect("https://stream.coindcx.com", transports=["websocket"], wait_timeout=15)
        while time.monotonic() - self.start < self.duration:
            time.sleep(1)
        if self.broker.position is not None and self.bid is not None:
            self.broker.exit(datetime.now(timezone.utc), self.bid, self.ask, "SESSION_END")
        self.sio.disconnect()
        self.write_results()

    def write_results(self):
        journal = self.out / "paper_journal.csv"
        rows = self.broker.journal
        if rows:
            keys = sorted({k for r in rows for k in r})
            with journal.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=keys)
                w.writeheader(); w.writerows(rows)
        summary = {
            "mode": "LIVE_DATA_PAPER_EXECUTION",
            "exchange": "CoinDCX",
            "pair": self.pair,
            "capital": 100000.0,
            "risk_per_trade": 0.0025,
            "max_trades_per_day": 5,
            "trades_entered": sum(1 for r in rows if r.get("event") == "ENTRY"),
            "trades_closed": sum(1 for r in rows if r.get("event") == "EXIT"),
            "realized_pnl": self.broker.realized_pnl,
            "ending_cash": self.broker.cash,
            "note": "Experimental signal; not historically validated and not for live-money execution.",
        }
        (self.out / "paper_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary, indent=2), flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pair", default="B-BTC_USDT")
    p.add_argument("--seconds", type=int, default=300)
    p.add_argument("--threshold", type=float, default=0.35)
    p.add_argument("--out", default="data/paper_live")
    a = p.parse_args()
    runner = LivePaperRunner(a.pair, Path(a.out), a.seconds, a.threshold)
    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, lambda *_: runner.sio.disconnect())
    runner.run()


if __name__ == "__main__":
    main()
