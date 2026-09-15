"""Run the filtered TW All in One candidate in live-data paper mode only."""
from __future__ import annotations

import argparse
import json
import signal
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import socketio

from app.execution.paper import PaperBroker, PaperConfig
from app.signals.tw_all_in_one import TWAllInOneSignal


class Runner:
    def __init__(self, pair: str, out: Path, seconds: int):
        self.pair, self.out, self.seconds = pair, out, seconds
        self.signal = TWAllInOneSignal(length=8, ema_length=200, ema_filter=True, slope_filter=True)
        self.broker = PaperBroker(PaperConfig(initial_capital=100_000, risk_per_trade=.0025,
            stop_pct=.005, target_pct=.01, fee_bps_per_side=5, slippage_bps=2,
            max_trades_per_day=5, max_daily_loss_pct=.02))
        self.bid = self.ask = 0.0
        self.bucket = None; self.o = self.h = self.l = self.c = 0.0; self.volume = 0.0
        self.closes = deque(maxlen=51); self.volumes = deque(maxlen=51); self.ranges = deque(maxlen=51)
        self.start = time.monotonic(); self.bars = self.events = self.raw_signals = self.accepted = 0
        self.out.mkdir(parents=True, exist_ok=True); self.journal = self.out / "tw_filtered_paper.jsonl"

    def write(self, row):
        with self.journal.open("a", encoding="utf-8") as f: f.write(json.dumps(row, default=str) + "\n")

    @staticmethod
    def levels(x):
        if isinstance(x, dict):
            return [(float(p), float(q)) for p, q in x.items()]
        out=[]
        for z in x or []:
            try: out.append((float(z[0]), float(z[1])))
            except (TypeError, ValueError, IndexError): pass
        return out

    def book(self, payload):
        d=payload.get("data", payload) if isinstance(payload, dict) else {}
        b=self.levels(d.get("bids",{})); a=self.levels(d.get("asks",{}))
        if not b or not a: return
        self.bid=max(b)[0]; self.ask=min(a)[0]
        if self.bid<=0 or self.ask<=self.bid: return
        self.events+=1; self.broker.check_risk_exits(datetime.now(timezone.utc), self.bid, self.ask)

    def trade(self, payload):
        d=payload.get("data", payload) if isinstance(payload, dict) else {}
        try: price=float(d.get("p")); qty=float(d.get("q")); ts=int(d.get("T") or time.time()*1000)
        except (TypeError, ValueError): return
        self.events+=1; bucket=(ts//300000)*300000
        if self.bucket is None: self.bucket=bucket
        if bucket!=self.bucket:
            if self.c:
                self.finish_bar(ts)
            self.bucket=bucket; self.o=self.h=self.l=self.c=price; self.volume=qty; return
        if not self.c: self.o=self.h=self.l=self.c=price
        else: self.h=max(self.h,price); self.l=min(self.l,price); self.c=price
        self.volume+=qty

    def finish_bar(self, ts):
        self.bars+=1; close=self.c; volume=self.volume
        self.closes.append(close); self.volumes.append(volume); self.ranges.append((self.h-self.l)/close if close else 0)
        sig=self.signal.update(close); self.raw_signals += int(sig!=0)
        # Additional causal filters, all based on completed/past bars.
        vol_ok=len(self.volumes)>=21 and volume >= 2*sum(list(self.volumes)[-21:-1])/20
        atr_now=np_mean(list(self.ranges)[-14:]) if len(self.ranges)>=14 else 0
        atr_base=np_mean(list(self.ranges)[-51:-1]) if len(self.ranges)>=51 else 0
        atr_ok=atr_base>0 and atr_now >= 1.2*atr_base
        ema200=np_ema(list(self.closes),200) if len(self.closes)>=200 else None
        dist_ok=ema200 is not None and abs(close/ema200-1)>=.01
        accepted=bool(sig and vol_ok and atr_ok and dist_ok)
        if accepted: self.accepted+=1
        now=datetime.fromtimestamp(ts/1000,tz=timezone.utc)
        if accepted and self.bid>0 and self.ask>self.bid:
            if self.broker.position is not None:
                if (sig==1 and self.broker.position.side=="SHORT") or (sig==-1 and self.broker.position.side=="LONG"):
                    self.broker.exit(now,self.bid,self.ask,"TW_FILTER_OPPOSITE")
            elif self.broker.can_enter(now,self.bid,self.ask): self.broker.enter(now,sig,self.bid,self.ask)
        self.write({"event":"BAR","time":now.isoformat(),"close":close,"volume":volume,"tw_signal":sig,"accepted":accepted,"capital":self.broker.cash})

    def run(self):
        sio=socketio.Client(reconnection=True,logger=False,engineio_logger=False)
        sio.event(self.connect); sio.event(self.disconnect); sio.on("depth-snapshot")(self.book); sio.on("new-trade")(self.trade)
        sio.connect("https://stream.coindcx.com",transports=["websocket"],wait_timeout=15)
        try:
            while time.monotonic()-self.start<self.seconds: time.sleep(10); print(self.status(),flush=True)
        finally:
            if sio.connected: sio.disconnect()
            if self.broker.position and self.bid>0: self.broker.exit(datetime.now(timezone.utc),self.bid,self.ask,"SESSION_END")
            print(self.status(),flush=True)

    def connect(self):
        self.write({"event":"CONNECTED","time":datetime.now(timezone.utc).isoformat()})
        # Channels are joined by the callback closure in main.
    def disconnect(self): self.write({"event":"DISCONNECTED","time":datetime.now(timezone.utc).isoformat()})
    def status(self):
        return {"bars":self.bars,"events":self.events,"raw_signals":self.raw_signals,"accepted_signals":self.accepted,"trades":self.broker.trades_today,"realized_pnl":round(self.broker.realized_pnl,2)}


def np_mean(x): return sum(x)/len(x) if x else 0.0

def np_ema(x,n):
    a=2/(n+1); v=x[0]
    for z in x[1:]: v=a*z+(1-a)*v
    return v


def main():
    p=argparse.ArgumentParser(); p.add_argument("--pair",default="B-BTC_USDT"); p.add_argument("--seconds",type=int,default=3600); p.add_argument("--out",default="data/tw_filtered_paper")
    a=p.parse_args(); r=Runner(a.pair,Path(a.out),a.seconds)
    sio=socketio.Client(reconnection=True,logger=False,engineio_logger=False)
    @sio.event
    def connect():
        r.connect(); sio.emit("join",{"channelName":f"{a.pair}@orderbook@50-futures"}); sio.emit("join",{"channelName":f"{a.pair}@trades-futures"})
    @sio.event
    def disconnect(): r.disconnect()
    sio.on("depth-snapshot")(r.book); sio.on("new-trade")(r.trade)
    def stop(*_): r.seconds=0
    signal.signal(signal.SIGINT,stop); signal.signal(signal.SIGTERM,stop)
    sio.connect("https://stream.coindcx.com",transports=["websocket"],wait_timeout=15)
    try:
        while time.monotonic()-r.start<a.seconds: time.sleep(10); print(r.status(),flush=True)
    finally:
        if sio.connected:sio.disconnect()
        if r.broker.position and r.bid>0:r.broker.exit(datetime.now(timezone.utc),r.bid,r.ask,"SESSION_END")
        print(r.status(),flush=True)
if __name__=="__main__":main()
