"""Live multi-coin TW All in One paper trader for CoinDCX futures.

Research-only: public CoinDCX data in, simulated fills out. No exchange order
placement is implemented. The runner uses REST candle polling as a reliability
fallback so a missed websocket candle cannot silently drop a 5m signal.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import requests

from app.execution.paper import PaperBroker, PaperConfig
from app.signals.tw_all_in_one_filtered import filtered_signals

ACTIVE = "https://api.coindcx.com/exchange/v1/derivatives/futures/data/active_instruments?margin_currency_short_name[]=USDT"
PRICES = "https://public.coindcx.com/market_data/v3/current_prices/futures/rt"
BOOK = "https://public.coindcx.com/market_data/v3/orderbook/{pair}-futures/50"
CANDLES = "https://public.coindcx.com/market_data/candlesticks"


def get_json(url: str, *, params=None, timeout: int = 15):
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def active_pairs(max_pairs: int = 30) -> list[str]:
    active = {x for x in get_json(ACTIVE) if isinstance(x, str) and x.endswith("_USDT")}
    try:
        prices = get_json(PRICES)
        ranked = sorted(
            ((p, float(v.get("v", 0.0))) for p, v in prices.items()
             if p in active and isinstance(v, dict)),
            key=lambda x: x[1], reverse=True,
        )
        if ranked:
            return [p for p, _ in ranked[:max_pairs]]
    except Exception:
        pass
    return sorted(active)[:max_pairs]


def fetch_bars(pair: str, bars: int = 260) -> list[tuple[int, float, float, float, float, float]]:
    now = int(time.time())
    span = bars * 300 + 900
    raw = get_json(CANDLES, params={
        "pair": pair, "from": now - span, "to": now,
        "resolution": "5", "pcode": "f",
    }).get("data", [])
    out = []
    for x in raw:
        try:
            out.append((int(x["time"]), float(x["open"]), float(x["high"]),
                        float(x["low"]), float(x["close"]), float(x["volume"])))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out)[-bars:]


def orderbook(pair: str):
    try:
        d = get_json(BOOK.format(pair=pair), timeout=5)
        bids, asks = d.get("bids", {}), d.get("asks", {})
        if not bids or not asks:
            return None
        bid, bq = max((float(p), float(q)) for p, q in bids.items())
        ask, aq = min((float(p), float(q)) for p, q in asks.items())
        if bid <= 0 or ask <= bid:
            return None
        return bid, ask, bq, aq
    except Exception:
        return None


def frame(rows):
    idx = pd.to_datetime([x[0] for x in rows], unit="ms", utc=True)
    return pd.DataFrame({
        "open": [x[1] for x in rows], "high": [x[2] for x in rows],
        "low": [x[3] for x in rows], "close": [x[4] for x in rows],
        "volume": [x[5] for x in rows],
    }, index=idx)


class MultiPaper:
    def __init__(self, pairs, cfg, seconds, out):
        self.pairs = pairs
        self.cfg = cfg
        self.seconds = seconds
        self.out = out
        self.broker = PaperBroker(PaperConfig(
            initial_capital=100_000.0, risk_per_trade=0.0025,
            stop_pct=0.005, target_pct=0.01,
            fee_bps_per_side=5.0, slippage_bps=2.0,
            max_trades_per_day=5, max_daily_loss_pct=0.02,
        ))
        self.bars = {}
        self.last_closed_bucket = {p: None for p in pairs}
        self.last_signal = {p: 0 for p in pairs}
        self.active_pair = None
        self.events = 0
        self.signals = 0
        self.errors = 0
        self.start = time.monotonic()
        self.out.mkdir(parents=True, exist_ok=True)
        self.journal = self.out / "tw_multi_paper.jsonl"
        self._warmup()

    def log(self, row):
        with self.journal.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")

    def _warmup(self):
        for pair in self.pairs:
            try:
                rows = fetch_bars(pair)
                self.bars[pair] = rows
                if rows:
                    # Do not trade the warm-up signal; only establish the last signal state.
                    self.last_signal[pair] = int(filtered_signals(frame(rows), **self.cfg).iloc[-1])
                    self.last_closed_bucket[pair] = rows[-1][0]
            except Exception as exc:
                self.bars[pair] = []
                self.errors += 1
                self.log({"event": "WARMUP_ERROR", "pair": pair, "error": str(exc)})

    def process_pair(self, pair):
        try:
            rows = fetch_bars(pair)
            if len(rows) < 250:
                return
            # CoinDCX returns the current in-progress candle as the last row.
            current_bucket = (int(time.time()) // 300) * 300000
            closed = [r for r in rows if r[0] < current_bucket]
            if not closed:
                return
            closed = closed[-250:]
            bucket = closed[-1][0]
            if self.last_closed_bucket[pair] == bucket:
                return
            self.last_closed_bucket[pair] = bucket
            self.bars[pair] = rows[-260:]
            self.events += 1

            sig = int(filtered_signals(frame(closed), **self.cfg).iloc[-1])
            if sig == self.last_signal[pair]:
                return
            self.last_signal[pair] = sig
            if not sig:
                return
            self.signals += 1
            now = datetime.fromtimestamp(bucket / 1000, tz=timezone.utc)
            book = orderbook(pair)
            if not book:
                self.log({"event": "SIGNAL_NO_BOOK", "pair": pair, "signal": sig,
                          "time": now.isoformat()})
                return
            bid, ask, _, _ = book

            if self.broker.position is not None:
                if self.active_pair == pair:
                    pos = self.broker.position
                    if (sig == 1 and pos.side == "SHORT") or (sig == -1 and pos.side == "LONG"):
                        self.broker.exit(now, bid, ask, "TW_OPPOSITE")
                        self.log({"event": "EXIT", "pair": pair, "reason": "TW_OPPOSITE",
                                  "time": now.isoformat()})
                        self.active_pair = None
                return

            if self.broker.can_enter(now, bid, ask) and self.broker.enter(now, sig, bid, ask):
                self.active_pair = pair
                self.log({"event": "ENTRY", "pair": pair, "signal": sig,
                          "time": now.isoformat(), "bid": bid, "ask": ask})
        except Exception as exc:
            self.errors += 1
            self.log({"event": "PAIR_ERROR", "pair": pair, "error": str(exc)})

    def risk_check(self):
        if not self.active_pair or self.broker.position is None:
            return
        book = orderbook(self.active_pair)
        if book:
            self.broker.check_risk_exits(datetime.now(timezone.utc), book[0], book[1])
            if self.broker.position is None:
                self.active_pair = None

    def summary(self):
        rows = self.broker.journal
        return {
            "mode": "MULTI_COIN_TW_LIVE_DATA_PAPER",
            "status": "LIVE_PAPER",
            "started_at": datetime.fromtimestamp(time.time() - (time.monotonic() - self.start), tz=timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "pairs": len(self.pairs), "pair_list": self.pairs,
            "capital": 100000.0, "risk_per_trade": 0.0025,
            "max_trades_per_day": 5, "events": self.events, "signals": self.signals,
            "errors": self.errors,
            "trades_entered": sum(r.get("event") == "ENTRY" for r in rows),
            "trades_closed": sum(r.get("event") == "EXIT" for r in rows),
            "realized_pnl": self.broker.realized_pnl,
            "ending_cash": self.broker.cash,
            "active_pair": self.active_pair,
            "config": self.cfg,
        }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=240)
    ap.add_argument("--pairs", default="")
    ap.add_argument("--max-pairs", type=int, default=30)
    ap.add_argument("--config", default="research_artifacts/tw_all_coins/TW_ALL_COINS_RESULT.json")
    ap.add_argument("--out", default="data/tw_multi_paper")
    a = ap.parse_args()

    raw = json.loads(Path(a.config).read_text()) if Path(a.config).exists() else {}
    cfg = raw.get("winner", {})
    if not cfg:
        cfg = {"hull_length": 8, "ema_length": 200, "ema_filter": True,
               "slope_filter": True, "volume_ratio_min": 1.2,
               "atr_expansion_min": 1.1, "ema_distance_min": 0.003,
               "rsi_filter": False, "cooldown_bars": 3}
    keys = {"hull_length", "ema_length", "ema_filter", "slope_filter",
            "volume_ratio_min", "atr_expansion_min", "ema_distance_min",
            "rsi_filter", "cooldown_bars"}
    cfg = {k: cfg[k] for k in keys}

    pairs = [x.strip() for x in a.pairs.split(",") if x.strip()] if a.pairs else active_pairs(a.max_pairs)
    pairs = pairs[:a.max_pairs]
    r = MultiPaper(pairs, cfg, a.seconds, Path(a.out))

    while time.monotonic() - r.start < a.seconds:
        for pair in pairs:
            r.process_pair(pair)
        r.risk_check()
        time.sleep(15)

    if r.broker.position is not None and r.active_pair:
        book = orderbook(r.active_pair)
        if book:
            r.broker.exit(datetime.now(timezone.utc), book[0], book[1], "SESSION_END")
        r.active_pair = None

    summary = r.summary()
    (Path(a.out) / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps(summary, indent=2, default=str), flush=True)


if __name__ == "__main__":
    main()
