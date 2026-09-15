"""Multi-coin TW All in One paper trader for CoinDCX futures."""
from __future__ import annotations

import argparse
import json
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import socketio

from app.execution.paper import PaperBroker, PaperConfig
from app.signals.tw_all_in_one_filtered import filtered_signals

ACTIVE = "https://api.coindcx.com/exchange/v1/derivatives/futures/data/active_instruments?margin_currency_short_name[]=USDT"
BOOK = "https://public.coindcx.com/market_data/v3/orderbook/{pair}-futures/50"


def active_pairs() -> list[str]:
    r = requests.get(ACTIVE, timeout=20)
    r.raise_for_status()
    return sorted({x for x in r.json() if isinstance(x, str) and x.endswith("_USDT")})


def _rows_from_candle_response(response: dict, allowed_pairs: set[str]) -> list[dict]:
    """Normalize CoinDCX futures candlestick payloads.

    The API has returned candle data both as a list and as a single object in
    different examples. Pair identity may also be available only in channel.
    """
    if not isinstance(response, dict):
        return []
    raw = response.get("data", [])
    rows = raw if isinstance(raw, list) else [raw]
    channel = str(response.get("channel", ""))
    channel_pair = channel.split("_5m-futures", 1)[0] if "_5m-futures" in channel else ""
    out: list[dict] = []
    for data in rows:
        if not isinstance(data, dict):
            continue
        row = dict(data)
        pair = row.get("pair") or row.get("s") or row.get("symbol") or channel_pair
        if pair in allowed_pairs:
            row["pair"] = pair
            out.append(row)
    return out


class MultiPaper:
    def __init__(self, pairs, config, seconds, out):
        self.pairs, self.cfg, self.seconds, self.out = pairs, config, seconds, out
        self.broker = PaperBroker(PaperConfig(initial_capital=100_000.0, risk_per_trade=0.0025,
            stop_pct=0.005, target_pct=0.01, fee_bps_per_side=5.0, slippage_bps=2.0,
            max_trades_per_day=5, max_daily_loss_pct=0.02))
        self.books = {}; self.bars = {p: [] for p in pairs}; self.active_pair = None
        self.last_signal = {p: 0 for p in pairs}; self.start = time.monotonic()
        self.events = 0; self.signals = 0; self.out.mkdir(parents=True, exist_ok=True)
        self.journal = self.out / "tw_multi_paper.jsonl"

    def log(self, row):
        with self.journal.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")

    def refresh_book(self, pair):
        try:
            data = requests.get(BOOK.format(pair=pair), timeout=5).json()
            bids, asks = data.get("bids", {}), data.get("asks", {})
            if not bids or not asks:
                return None
            bid, bq = max((float(p), float(q)) for p, q in bids.items())
            ask, aq = min((float(p), float(q)) for p, q in asks.items())
            if bid <= 0 or ask <= bid:
                return None
            self.books[pair] = (bid, ask, bq, aq)
            return self.books[pair]
        except Exception:
            return None

    def on_candle(self, response):
        for data in _rows_from_candle_response(response, set(self.pairs)):
            try:
                op = float(data.get("open", data.get("o")))
                hi = float(data.get("high", data.get("h")))
                lo = float(data.get("low", data.get("l")))
                close = float(data.get("close", data.get("c")))
                volume = float(data.get("volume", data.get("v")))
                ts_ms = int(data.get("open_time", data.get("t", 0)))
                if ts_ms < 10_000_000_000:
                    ts_ms *= 1000
            except (TypeError, ValueError):
                continue
            pair = data["pair"]
            bucket = (ts_ms // 300000) * 300000
            hist = self.bars[pair]
            row = (bucket, op, hi, lo, close, volume)
            if hist and hist[-1][0] == bucket:
                hist[-1] = row
                continue
            hist.append(row)
            if len(hist) < 250:
                continue
            self.events += 1
            idx = pd.to_datetime([x[0] for x in hist], unit="ms", utc=True)
            df = pd.DataFrame({"open":[x[1] for x in hist],"high":[x[2] for x in hist],
                "low":[x[3] for x in hist],"close":[x[4] for x in hist],"volume":[x[5] for x in hist]}, index=idx)
            sig = int(filtered_signals(df, **self.cfg).iloc[-1])
            if sig == self.last_signal[pair]:
                continue
            self.last_signal[pair] = sig
            if not sig:
                continue
            self.signals += 1
            now = datetime.fromtimestamp(ts_ms/1000, tz=timezone.utc)
            book = self.refresh_book(pair)
            if not book:
                continue
            bid, ask, _, _ = book
            if self.broker.position is not None:
                if self.active_pair == pair:
                    pos = self.broker.position
                    if (sig == 1 and pos.side == "SHORT") or (sig == -1 and pos.side == "LONG"):
                        self.broker.exit(now, bid, ask, "TW_OPPOSITE")
                        self.log({"event":"EXIT","pair":pair,"reason":"TW_OPPOSITE","time":now.isoformat()})
                        self.active_pair = None
                continue
            if self.broker.can_enter(now, bid, ask) and self.broker.enter(now, sig, bid, ask):
                self.active_pair = pair
                self.log({"event":"ENTRY","pair":pair,"time":now.isoformat(),"signal":sig})

    def summary(self):
        rows = self.broker.journal
        return {"mode":"MULTI_COIN_TW_LIVE_DATA_PAPER","pairs":len(self.pairs),"capital":100000.0,
            "risk_per_trade":0.0025,"max_trades_per_day":5,"events":self.events,"signals":self.signals,
            "trades_entered":sum(r.get("event")=="ENTRY" for r in rows),"trades_closed":sum(r.get("event")=="EXIT" for r in rows),
            "realized_pnl":self.broker.realized_pnl,"ending_cash":self.broker.cash,"active_pair":self.active_pair,"config":self.cfg}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--seconds",type=int,default=3600); ap.add_argument("--pairs",default="")
    ap.add_argument("--max-pairs",type=int,default=0); ap.add_argument("--config",default="research_artifacts/tw_all_coins/TW_ALL_COINS_RESULT.json")
    ap.add_argument("--out",default="data/tw_multi_paper"); a=ap.parse_args()
    raw=json.loads(Path(a.config).read_text()) if Path(a.config).exists() else {}; cfg=raw.get("winner",{})
    if not cfg: cfg={"hull_length":8,"ema_length":200,"ema_filter":True,"slope_filter":True,"volume_ratio_min":1.2,"atr_expansion_min":1.1,"ema_distance_min":0.003,"rsi_filter":False,"cooldown_bars":3}
    keys={"hull_length","ema_length","ema_filter","slope_filter","volume_ratio_min","atr_expansion_min","ema_distance_min","rsi_filter","cooldown_bars"}; cfg={k:cfg[k] for k in keys}
    pairs=[x.strip() for x in a.pairs.split(",") if x.strip()] if a.pairs else active_pairs()
    if a.max_pairs>0: pairs=pairs[:a.max_pairs]
    r=MultiPaper(pairs,cfg,a.seconds,Path(a.out)); sio=socketio.Client(reconnection=True,logger=False,engineio_logger=False)
    @sio.event
    def connect():
        for pair in pairs: sio.emit("join",{"channelName":f"{pair}_5m-futures"})
        print(f"CONNECTED: {len(pairs)} pairs | TW PAPER ONLY",flush=True)
    @sio.on("candlestick")
    def candle(data): r.on_candle(data)
    signal.signal(signal.SIGINT,lambda *_:sio.disconnect()); signal.signal(signal.SIGTERM,lambda *_:sio.disconnect())
    sio.connect("https://stream.coindcx.com",transports=["websocket"],wait_timeout=15)
    try:
        while time.monotonic()-r.start<a.seconds:
            if r.active_pair:
                book=r.refresh_book(r.active_pair)
                if book: r.broker.check_risk_exits(datetime.now(timezone.utc),book[0],book[1])
            time.sleep(5); print(json.dumps(r.summary(),default=str),flush=True)
    finally:
        if r.broker.position is not None and r.active_pair:
            book=r.refresh_book(r.active_pair)
            if book: r.broker.exit(datetime.now(timezone.utc),book[0],book[1],"SESSION_END")
        if sio.connected: sio.disconnect()
        summary=r.summary(); Path(a.out).mkdir(parents=True,exist_ok=True)
        (Path(a.out)/"summary.json").write_text(json.dumps(summary,indent=2,default=str)); print(json.dumps(summary,indent=2,default=str),flush=True)

if __name__=="__main__": main()
