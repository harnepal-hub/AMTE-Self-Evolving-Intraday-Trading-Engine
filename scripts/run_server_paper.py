"""Server-side multi-coin paper engine for AMTE training.

Public CoinDCX futures data only. No real exchange orders.
Training mode intentionally has no trade-count cap; the 2% daily loss lock
remains active. Every candidate signal is logged before any entry decision,
including rejected signals. Trades include MFE/MAE for later self-evolution.
"""
from __future__ import annotations

import argparse, csv, json, time, uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.signals.tw_all_in_one_filtered import filtered_signals

ACTIVE = "https://api.coindcx.com/exchange/v1/derivatives/futures/data/active_instruments?margin_currency_short_name[]=USDT"
PRICES = "https://public.coindcx.com/market_data/v3/current_prices/futures/rt"
CANDLES = "https://public.coindcx.com/market_data/candlesticks"
BOOK = "https://public.coindcx.com/market_data/v3/orderbook/{pair}-futures/50"
IST = ZoneInfo("Asia/Kolkata")

# Frozen paper configuration for this training phase.
RISK_PER_TRADE = 0.0010
STOP_PCT = 0.005
TARGET_PCT = 0.01
FEE_BPS = 5.0
SLIPPAGE_BPS = 2.0
MAX_DAILY_LOSS_RS = 2000.0
MAX_TRADES_PER_DAY = 10
CFG = {
    "hull_length": 8, "ema_length": 200, "ema_filter": True,
    "slope_filter": True, "volume_ratio_min": 1.2,
    "atr_expansion_min": 1.1, "ema_distance_min": 0.003,
    "rsi_filter": False, "cooldown_bars": 3,
}

DATA = ROOT / "data" / "paper_live"
STATE_PATH = DATA / "state.json"
SUMMARY_PATH = DATA / "summary.json"
MARKET_PATH = DATA / "market.json"
TRADE_CSV = DATA / "trade_journal.csv"
SIGNAL_CSV = DATA / "signal_journal.csv"

TRADE_FIELDS = [
    "trade_id","signal_id","pair","side","entry_time","exit_time",
    "entry_price","exit_price","stop_price","target_price","quantity",
    "entry_fee","exit_fee","fees","gross_pnl","net_pnl","reason",
    "hold_seconds","mfe_price","mae_price","mfe_pct","mae_pct",
    "mfe_r","mae_r","signal_source","ai_confidence",
]
SIGNAL_FIELDS = [
    "signal_id","time","bar_time","pair","side","tw_signal","ai_side",
    "ai_confidence","decision","reject_reason","entry_price","bid","ask",
    "spread_bps","signal_source","filter_config",
]


def get_json(url, params=None, timeout=12):
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def active_pairs(n):
    active = {x for x in get_json(ACTIVE) if isinstance(x, str) and x.endswith("_USDT")}
    prices = get_json(PRICES).get("prices", {})
    ranked = sorted(
        ((p, float(v.get("v", 0))) for p, v in prices.items()
         if p in active and isinstance(v, dict)),
        key=lambda x: x[1], reverse=True,
    )
    return [p for p, _ in ranked[:n]] if ranked else sorted(active)[:n]


def fetch_bars(pair, bars=260):
    now = int(time.time())
    j = get_json(CANDLES, {
        "pair": pair, "from": now - bars * 300 - 900, "to": now,
        "resolution": 5, "pcode": "f",
    })
    out = []
    for x in j.get("data", []):
        try:
            out.append({
                "time": int(x["time"]), "open": float(x["open"]),
                "high": float(x["high"]), "low": float(x["low"]),
                "close": float(x["close"]), "volume": float(x["volume"]),
            })
        except (KeyError, TypeError, ValueError):
            pass
    return sorted(out, key=lambda x: x["time"])[-bars:]


def frame(rows):
    return pd.DataFrame(
        {
            "open": [x["open"] for x in rows], "high": [x["high"] for x in rows],
            "low": [x["low"] for x in rows], "close": [x["close"] for x in rows],
            "volume": [x["volume"] for x in rows],
        },
        index=pd.to_datetime([x["time"] for x in rows], unit="ms", utc=True),
    )


def book(pair):
    try:
        d = get_json(BOOK.format(pair=pair), timeout=5)
        bids, asks = d.get("bids", {}), d.get("asks", {})
        if not bids or not asks:
            return None
        bid = max(float(p) for p in bids)
        ask = min(float(p) for p in asks)
        return (bid, ask) if 0 < bid < ask else None
    except Exception:
        return None


def ema(v, n):
    return float(pd.Series(v, dtype=float).ewm(span=n, adjust=False).mean().iloc[-1])


def rsi(v, n=14):
    s = pd.Series(v, dtype=float)
    d = s.diff()
    up, dn = d.clip(lower=0), -d.clip(upper=0)
    au = up.ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    ad = dn.ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    z = 100 - 100 / (1 + au / ad.replace(0, pd.NA))
    return float(z.iloc[-1]) if pd.notna(z.iloc[-1]) else 50.0


def ai_proxy(rows):
    if len(rows) < 60:
        return "NO TRADE", 50.0
    c = [x["close"] for x in rows]
    v = [x["volume"] for x in rows]
    last, e20, e50 = c[-1], ema(c, 20), ema(c, 50)
    rr = rsi(c)
    vr = v[-1] / (sum(v[-20:]) / 20 or 1)
    score = 50 + (12 if last > e20 else -12) + (12 if e20 > e50 else -12)
    if 50 < rr < 70:
        score += 10
    if 30 < rr < 50:
        score -= 10
    if vr > 1.2:
        score += 8
    return ("LONG" if score >= 62 else "SHORT" if score <= 38 else "NO TRADE",
            float(max(0, min(100, score))))


def default_state():
    return {
        "version": 7, "day_ist": "", "cash": 100000.0, "realized_pnl": 0.0, "peak_equity": 100000.0, "max_drawdown_rs": 0.0,
        "trades_today": 0, "locked": False, "position": None,
        "last_signal": {}, "last_processed_bar": {}, "pairs": [], "events": 0, "signals": 0,
        "rejected_signals": 0, "accepted_signals": 0, "errors": 0,
        "updated_at": None, "heartbeat_at": None, "run_started_at": None, "run_finished_at": None,
        "status": "STARTING", "journal": [], "signal_map": {},
    }


def load_state(path):
    if not path.exists():
        return default_state()
    try:
        s = default_state()
        s.update(json.loads(path.read_text()))
        return s
    except Exception:
        return default_state()


def save_state(path, s):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(s, indent=2, default=str))


def append_csv(path, fields, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in fields})


def event(s, row):
    row = dict(row)
    row["time"] = datetime.now(timezone.utc).isoformat()
    s["journal"].insert(0, row)
    s["journal"] = s["journal"][:1000]


def roll_day(s):
    d = datetime.now(IST).date().isoformat()
    if s["day_ist"] != d:
        s["day_ist"] = d
        s["trades_today"] = 0
        s["locked"] = False
        s["realized_pnl"] = 0.0


def equity_mark(s, bid=None, ask=None):
    p = s.get("position")
    if not p or bid is None or ask is None:
        return float(s.get("cash", 0.0))
    mark = (float(bid) + float(ask)) / 2.0
    side = 1 if p["side"] == "LONG" else -1
    return float(s.get("cash", 0.0)) + (mark - p["entry_price"]) * p["quantity"] * side


def update_drawdown(s, bid=None, ask=None):
    eq = equity_mark(s, bid, ask)
    s["peak_equity"] = max(float(s.get("peak_equity", 100000.0)), eq)
    dd = max(0.0, s["peak_equity"] - eq)
    s["max_drawdown_rs"] = max(float(s.get("max_drawdown_rs", 0.0)), dd)
    if dd >= MAX_DAILY_LOSS_RS:
        s["locked"] = True
        return bool(s.get("position"))
    return False


def can_enter(s):
    return (not s["position"] and not s["locked"] and
            (MAX_TRADES_PER_DAY is None or s["trades_today"] < MAX_TRADES_PER_DAY))


def enter(s, pair, side, bid, ask, signal_id, signal_source, ai_score):
    if not can_enter(s):
        return False
    px0 = ask if side == 1 else bid
    impact = SLIPPAGE_BPS / 10000
    px = px0 * (1 + impact if side == 1 else 1 - impact)
    risk = s["cash"] * RISK_PER_TRADE
    qty = risk / (px * STOP_PCT)
    fee = px * qty * FEE_BPS / 10000
    if qty <= 0 or s["cash"] <= fee:
        return False
    side_name = "LONG" if side == 1 else "SHORT"
    s["cash"] -= fee
    trade_id = uuid.uuid4().hex
    s["trades_today"] += 1
    s["accepted_signals"] += 1
    s["position"] = {
        "trade_id": trade_id, "signal_id": signal_id, "pair": pair,
        "side": side_name, "entry_price": px, "quantity": qty,
        "entry_fee": fee, "stop_price": px * (0.995 if side == 1 else 1.005),
        "target_price": px * (1.01 if side == 1 else .99),
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "kind": signal_source, "ai_score": ai_score,
        "mfe_price": px, "mae_price": px,
    }
    event(s, {"event": "ENTRY", "trade_id": trade_id, "signal_id": signal_id,
              "pair": pair, "side": side_name, "entry_price": px, "quantity": qty,
              "stop_price": s["position"]["stop_price"],
              "target_price": s["position"]["target_price"],
              "signal_source": signal_source, "ai_confidence": ai_score})
    return True


def update_excursion(s, rows):
    p = s["position"]
    if not p or not rows:
        return
    side = 1 if p["side"] == "LONG" else -1
    for x in rows[-3:]:
        if side == 1:
            p["mfe_price"] = max(p["mfe_price"], x["high"])
            p["mae_price"] = min(p["mae_price"], x["low"])
        else:
            p["mfe_price"] = max(p["mfe_price"], 2*p["entry_price"] - x["low"])
            p["mae_price"] = min(p["mae_price"], 2*p["entry_price"] - x["high"])


def exit_position(s, bid, ask, reason):
    p = s["position"]
    if not p:
        return
    side = 1 if p["side"] == "LONG" else -1
    px0 = bid if side == 1 else ask
    impact = SLIPPAGE_BPS / 10000
    px = px0 * (1 - impact if side == 1 else 1 + impact)
    gross = (px - p["entry_price"]) * p["quantity"] * side
    fee = px * p["quantity"] * FEE_BPS / 10000
    net = gross - p["entry_fee"] - fee
    s["cash"] += gross - fee
    s["realized_pnl"] += net
    hold = (datetime.now(timezone.utc) -
            datetime.fromisoformat(p["opened_at"])).total_seconds()
    mfe_pct = abs(p["mfe_price"] - p["entry_price"]) / p["entry_price"]
    mae_pct = abs(p["mae_price"] - p["entry_price"]) / p["entry_price"]
    trade = {
        "event": "EXIT", "trade_id": p["trade_id"], "signal_id": p["signal_id"],
        "pair": p["pair"], "side": p["side"], "entry_time": p["opened_at"],
        "exit_time": datetime.now(timezone.utc).isoformat(),
        "entry_price": p["entry_price"], "exit_price": px,
        "stop_price": p["stop_price"], "target_price": p["target_price"],
        "quantity": p["quantity"], "entry_fee": p["entry_fee"], "exit_fee": fee,
        "fees": p["entry_fee"] + fee, "gross_pnl": gross, "net_pnl": net,
        "reason": reason, "hold_seconds": hold,
        "mfe_price": p["mfe_price"], "mae_price": p["mae_price"],
        "mfe_pct": mfe_pct, "mae_pct": mae_pct,
        "mfe_r": mfe_pct / STOP_PCT, "mae_r": mae_pct / STOP_PCT,
        "signal_source": p["kind"], "ai_confidence": p["ai_score"],
    }
    event(s, trade)
    append_csv(TRADE_CSV, TRADE_FIELDS, trade)
    s["position"] = None
    if s["realized_pnl"] <= -MAX_DAILY_LOSS_RS:
        s["locked"] = True


def risk_check(s):
    p = s["position"]
    if not p:
        return
    q = book(p["pair"])
    if not q:
        return
    bid, ask = q
    if p["side"] == "LONG":
        if bid <= p["stop_price"]:
            exit_position(s, bid, ask, "STOP_LOSS")
        elif bid >= p["target_price"]:
            exit_position(s, bid, ask, "TAKE_PROFIT")
    else:
        if ask >= p["stop_price"]:
            exit_position(s, bid, ask, "STOP_LOSS")
        elif ask <= p["target_price"]:
            exit_position(s, bid, ask, "TAKE_PROFIT")


def process(s, pair, bars_cache=None, rows=None):
    try:
        rows = fetch_bars(pair) if rows is None else rows
        if bars_cache is not None:
            bars_cache[pair] = rows[-80:]
        if len(rows) < 210:
            return
        if s["position"] and s["position"]["pair"] == pair:
            update_excursion(s, rows)

        bucket = (int(time.time()) // 300) * 300000
        closed = [r for r in rows if r["time"] < bucket]
        if len(closed) < 210:
            return

        # Count/process a 5m market event once per newly closed candle.
        # Polling the same closed candle must not inflate the event counter.
        bar_time = closed[-1]["time"]
        if s["last_processed_bar"].get(pair) == bar_time:
            return
        s["last_processed_bar"][pair] = bar_time
        s["events"] += 1

        sig = int(filtered_signals(frame(closed), **CFG).iloc[-1])
        ai_side, score = ai_proxy(closed)
        s["signal_map"][pair] = {"tw": sig, "ai": ai_side, "confidence": score, "bar_time": bar_time}
        if not sig:
            return

        signal_id = f"{pair}-{bar_time}-{sig}"
        if s["last_signal"].get(pair) == signal_id:
            return
        s["last_signal"][pair] = signal_id

        wanted = "LONG" if sig == 1 else "SHORT"
        q = book(pair)
        bid, ask = q if q else (None, None)
        spread_bps = ((ask - bid) / ((ask + bid) / 2) * 10000
                      if bid and ask else None)

        # This row is deliberately written BEFORE the entry decision.
        decision, reject = "ACCEPTED", ""
        if not q:
            decision, reject = "REJECTED", "NO_LIVE_ORDERBOOK"
        elif s["position"]:
            decision, reject = "REJECTED", "POSITION_ALREADY_OPEN"
        elif s["locked"]:
            decision, reject = "REJECTED", "DAILY_LOSS_LOCK"
        elif MAX_TRADES_PER_DAY is not None and s["trades_today"] >= MAX_TRADES_PER_DAY:
            decision, reject = "REJECTED", "DAILY_TRADE_CAP"
        elif ai_side != wanted:
            decision, reject = "REJECTED", "AI_CONFLICT"
        elif (wanted == "LONG" and score < 62) or (wanted == "SHORT" and score > 38):
            decision, reject = "REJECTED", "AI_LOW_CONFIDENCE"

        candidate = {
            "signal_id": signal_id, "time": datetime.now(timezone.utc).isoformat(),
            "bar_time": datetime.fromtimestamp(bar_time/1000, timezone.utc).isoformat(),
            "pair": pair, "side": wanted, "tw_signal": sig, "ai_side": ai_side,
            "ai_confidence": score, "decision": decision,
            "reject_reason": reject, "entry_price": (ask if sig == 1 else bid),
            "bid": bid, "ask": ask, "spread_bps": spread_bps,
            "signal_source": "TW_FILTERED_PLUS_AI",
            "filter_config": json.dumps(CFG, sort_keys=True),
        }
        append_csv(SIGNAL_CSV, SIGNAL_FIELDS, candidate)
        s["signals"] += 1
        if decision != "ACCEPTED":
            s["rejected_signals"] += 1
            event(s, {"event": "SIGNAL_REJECTED", **candidate})
            return

        entered = enter(s, pair, sig, bid, ask, signal_id, "TW_FILTERED_PLUS_AI", score)
        if not entered:
            s["rejected_signals"] += 1
            candidate["decision"] = "REJECTED"
            candidate["reject_reason"] = "ENTRY_GUARD"
            append_csv(SIGNAL_CSV, SIGNAL_FIELDS, candidate)
            event(s, {"event": "SIGNAL_REJECTED", **candidate})
    except Exception as exc:
        s["errors"] += 1
        event(s, {"event": "PAIR_ERROR", "pair": pair, "error": str(exc)})


def write_market_snapshot(s, pairs, bars_cache):
    try:
        prices = get_json(PRICES).get("prices", {})
        candles = {
            p: bars_cache.get(p, [])[-120:]
            for p in pairs
            if bars_cache.get(p)
        }
        snapshot = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "prices": {p: prices[p] for p in pairs if p in prices},
            "signals": s.get("signal_map", {}),
            "candles": candles,
            "source": "CoinDCX public futures REST",
            "candle_resolution": 5,
        }
        MARKET_PATH.write_text(json.dumps(snapshot, separators=(",", ":")))
    except Exception as exc:
        event(s, {"event": "MARKET_SNAPSHOT_ERROR", "error": str(exc)})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=4.0)
    ap.add_argument("--max-pairs", type=int, default=30)
    a = ap.parse_args()

    DATA.mkdir(parents=True, exist_ok=True)
    s = load_state(STATE_PATH)
    roll_day(s)

    try:
        pairs = active_pairs(a.max_pairs)
    except Exception as exc:
        pairs = s.get("pairs", [])[:a.max_pairs]
        s["errors"] += 1
        event(s, {"event": "ACTIVE_PAIRS_ERROR", "error": str(exc)})

    s["pairs"] = pairs
    s["run_started_at"] = datetime.now(timezone.utc).isoformat()
    s["run_finished_at"] = None
    s["status"] = "STARTING"
    end = time.monotonic() + a.minutes * 60
    bars_cache = {}

    # Fetch candle histories concurrently so all 30 coins are evaluated within
    # the 5-minute cadence. State mutation and signal decisions remain sequential.
    workers = min(12, max(1, len(pairs)))
    while time.monotonic() < end:
        fetched = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(fetch_bars, p): p for p in pairs}
            for fut in as_completed(futures):
                p = futures[fut]
                try:
                    fetched[p] = fut.result()
                except Exception as exc:
                    s["errors"] += 1
                    event(s, {"event": "FETCH_ERROR", "pair": p, "error": str(exc)})
        for p in pairs:
            process(s, p, bars_cache, fetched.get(p, []))
            risk_check(s)
            if s.get("position") and s["position"].get("pair") == p:
                q = book(p)
                if q:
                    bid, ask = q
                    if update_drawdown(s, bid, ask) and s.get("position"):
                        exit_position(s, bid, ask, "MAX_EQUITY_DRAWDOWN")
                        s["locked"] = True
            elif not s.get("position"):
                update_drawdown(s)
        s["updated_at"] = datetime.now(timezone.utc).isoformat()
        s["heartbeat_at"] = s["updated_at"]
        s["status"] = "LIVE_PAPER"
        save_state(STATE_PATH, s)
        time.sleep(15)

    # Do NOT flatten at the end of a GitHub Actions run. The paper position
    # must persist across runs so TP/SL can be monitored continuously.
    write_market_snapshot(s, pairs, bars_cache)
    s["run_finished_at"] = datetime.now(timezone.utc).isoformat()
    s["updated_at"] = s["run_finished_at"]
    s["heartbeat_at"] = s["run_finished_at"]
    save_state(STATE_PATH, s)

    summary = {
        "mode": "SERVER_SIDE_MULTI_COIN_TW_AI_PAPER_TRAINING",
        "status": s["status"], "updated_at": s["updated_at"],
        "run_started_at": s.get("run_started_at"), "run_finished_at": s.get("run_finished_at"),
        "day_ist": s["day_ist"], "capital": 100000.0, "cash": s["cash"],
        "realized_pnl": s["realized_pnl"], "trades_today": s["trades_today"],
        "max_trades_per_day": MAX_TRADES_PER_DAY, "max_daily_loss_rs": MAX_DAILY_LOSS_RS, "max_equity_drawdown_rs": MAX_DAILY_LOSS_RS,
        "pairs": len(pairs), "pair_list": pairs, "events": s["events"],
        "signals": s["signals"], "accepted_signals": s["accepted_signals"],
        "rejected_signals": s["rejected_signals"], "errors": s["errors"],
        "position": s["position"], "browser_required": False,
        "real_orders": False, "config": CFG,
    }
    save_state(STATE_PATH, s)
    save_state(SUMMARY_PATH, summary)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
