"""Multi-coin TW All in One paper trader for CoinDCX futures.

This runner uses the selected historical filter configuration when available,
subscribes to 5m candles plus executable order books, and uses one shared
₹100,000 paper account with a hard five-trade/day limit across all coins.
No authenticated exchange API and no order placement are used.
"""
from __future__ import annotations

import argparse
import json
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import socketio

from app.execution.paper import PaperBroker, PaperConfig
from app.signals.tw_all_in_one_filtered import filtered_signals

ACTIVE = "https://api.coindcx.com/exchange/v1/derivatives/futures/data/active_instruments?margin_currency_short_name[]=USDT"


def active_pairs() -> list[str]:
    r = requests.get(ACTIVE, timeout=20); r.raise_for_status()
    return sorted({x for x in r.json() if isinstance(x, str) and x.endswith("_USDT")})


class MultiPaper:
    def __init__(self, pairs: list[str], config: dict, seconds: int, out: Path):
        self.pairs = pairs
        self.cfg = config
        self.seconds = seconds
        self.out = out
        self.broker = PaperBroker(PaperConfig(
            initial_capital=100_000.0, risk_per_trade=0.0025,
            stop_pct=0.005, target_pct=0.01,
            fee_bps_per_side=5.0, slippage_bps=2.0,
            max_trades_per_day=5, max_daily_loss_pct=0.02,
        ))
        self.books = {}
        self.bars = {p: [] for p in pairs}
        self.active_pair = None
        self.last_signal = {p: 0 for p in pairs}
        self.start = time.monotonic()
        self.events = 0
        self.signals = 0
        self.out.mkdir(parents=True, exist_ok=True)
        self.journal = self.out / "tw_multi_paper.jsonl"

    def log(self, row):
        with self.journal.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")

    @staticmethod
    def levels(levels):
        if isinstance(levels, dict):
            vals = [(float(p), float(q)) for p, q in levels.items()]
        else:
            vals = [(float(x[0]), float(x[1])) for x in levels if isinstance(x, (list, tuple)) and len(x) >= 2]
        return vals

    def on_depth(self, response):
        data = response.get("data", response) if isinstance(response, dict) else {}
        pair = data.get("s") or data.get("pair")
        if pair not in self.pairs:
            # Some socket payloads omit the symbol; caller sets current pair before dispatch.
            pair = getattr(self, "dispatch_pair", None)
        if not pair: return
        bids = self.levels(data.get("bids", {})); asks = self.levels(data.get("asks", {}))
        if not bids or not asks: return
        bid, bq = max(bids, key=lambda x: x[0]); ask, aq = min(asks, key=lambda x: x[0])
        if bid <= 0 or ask <= bid: return
        self.books[pair] = (bid, ask, bq, aq)
        self.events += 1
        if self.active_pair == pair:
            self.broker.check_risk_exits(datetime.now(timezone.utc), bid, ask)

    def on_candle(self, pair: str, response):
        data = response.get("data", response) if isinstance(response, dict) else {}
        try:
            close = float(data.get("close", data.get("c")))
            volume = float(data.get("volume", data.get("v", 0)))
            ts_ms = int(data.get("Ets") or data.get("time") or data.get("T") or time.time() * 1000)
        except (TypeError, ValueError):
            return
        # Ignore duplicate/current open candle updates; only act on a new completed 5m bar.
        bucket = (ts_ms // 300000) * 300000
        hist = self.bars[pair]
        if hist and hist[-1][0] == bucket:
            hist[-1] = (bucket, close, volume)
            return
        hist.append((bucket, close, volume))
        if len(hist) < 250:
            return
        self.events += 1
        import pandas as pd
        idx = pd.to_datetime([x[0] for x in hist], unit="ms", utc=True)
        df = pd.DataFrame({"open": [x[1] for x in hist], "high": [x[1] for x in hist],
                           "low": [x[1] for x in hist], "close": [x[1] for x in hist],
                           "volume": [x[2] for x in hist]}, index=idx)
        sig = int(filtered_signals(df, **self.cfg).iloc[-1])
        if sig == self.last_signal[pair]:
            return
        self.last_signal[pair] = sig
        if sig == 0:
            return
        self.signals += 1
        now = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        book = self.books.get(pair)
        if not book: return
        bid, ask, _, _ = book
        if self.broker.position is not None:
            if self.active_pair == pair:
                pos = self.broker.position
                if (sig == 1 and pos.side == "SHORT") or (sig == -1 and pos.side == "LONG"):
                    self.broker.exit(now, bid, ask, "TW_OPPOSITE")
                    self.log({"event": "EXIT", "pair": pair, "reason": "TW_OPPOSITE", "time": now.isoformat()})
            return
        if self.broker.can_enter(now, bid, ask):
            if self.broker.enter(now, sig, bid, ask):
                self.active_pair = pair
                self.log({"event": "ENTRY", "pair": pair, "time": now.isoformat(), "signal": sig})

    def summary(self):
        rows = self.broker.journal
        return {
            "mode": "MULTI_COIN_TW_LIVE_DATA_PAPER",
            "pairs": len(self.pairs),
            "capital": 100000.0,
            "risk_per_trade": 0.0025,
            "max_trades_per_day": 5,
            "events": self.events,
            "signals": self.signals,
            "trades_entered": sum(r.get("event") == "ENTRY" for r in rows),
            "trades_closed": sum(r.get("event") == "EXIT" for r in rows),
            "realized_pnl": self.broker.realized_pnl,
            "ending_cash": self.broker.cash,
            "active_pair": self.active_pair,
            "config": self.cfg,
        }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=3600)
    ap.add_argument("--pairs", default="", help="Comma-separated pairs; empty means all active USDT futures")
    ap.add_argument("--max-pairs", type=int, default=0, help="0 means all")
    ap.add_argument("--config", default="research_artifacts/tw_all_coins/TW_ALL_COINS_RESULT.json")
    ap.add_argument("--out", default="data/tw_multi_paper")
    args = ap.parse_args()

    raw = json.loads(Path(args.config).read_text(encoding="utf-8")) if Path(args.config).exists() else {}
    cfg = raw.get("winner", {})
    if not cfg:
        cfg = {"hull_length": 8, "ema_length": 200, "ema_filter": True, "slope_filter": True,
               "volume_ratio_min": 1.2, "atr_expansion_min": 1.1, "ema_distance_min": 0.003,
               "rsi_filter": False, "cooldown_bars": 3}
    keys = {"hull_length", "ema_length", "ema_filter", "slope_filter", "volume_ratio_min", "atr_expansion_min", "ema_distance_min", "rsi_filter", "cooldown_bars"}
    cfg = {k: cfg[k] for k in keys if k in cfg}

    pairs = [x.strip() for x in args.pairs.split(",") if x.strip()] if args.pairs else active_pairs()
    if args.max_pairs > 0: pairs = pairs[:args.max_pairs]
    runner = MultiPaper(pairs, cfg, args.seconds, Path(args.out))
    sio = socketio.Client(reconnection=True, logger=False, engineio_logger=False)

    @sio.event
    def connect():
        for pair in pairs:
            sio.emit("join", {"channelName": f"{pair}@orderbook@50-futures"})
            sio.emit("join", {"channelName": f"{pair}_5m-futures"})
        runner.log({"event": "CONNECTED", "time": datetime.now(timezone.utc).isoformat(), "pairs": len(pairs)})
        print(f"CONNECTED: {len(pairs)} pairs | TW PAPER ONLY", flush=True)

    @sio.on("depth-snapshot")
    def depth(data):
        runner.on_depth(data)

    @sio.on("candlestick")
    def candle(data):
        payload = data.get("data", data) if isinstance(data, dict) else {}
        pair = payload.get("s") or payload.get("pair") or payload.get("symbol")
        if pair in pairs: runner.on_candle(pair, data)

    signal.signal(signal.SIGINT, lambda *_: sio.disconnect())
    signal.signal(signal.SIGTERM, lambda *_: sio.disconnect())
    sio.connect("https://stream.coindcx.com", transports=["websocket"], wait_timeout=15)
    try:
        while time.monotonic() - runner.start < args.seconds:
            time.sleep(10)
            print(json.dumps(runner.summary(), default=str), flush=True)
    finally:
        if runner.broker.position is not None and runner.active_pair in runner.books:
            bid, ask, _, _ = runner.books[runner.active_pair]
            runner.broker.exit(datetime.now(timezone.utc), bid, ask, "SESSION_END")
        if sio.connected: sio.disconnect()
        summary = runner.summary()
        Path(args.out).mkdir(parents=True, exist_ok=True)
        (Path(args.out) / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        print(json.dumps(summary, indent=2, default=str), flush=True)


if __name__ == "__main__": main()
