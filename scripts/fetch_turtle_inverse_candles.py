#!/usr/bin/env python3
"""Fetch and backtest multiple CoinDCX futures pairs for the Turtle Soup research lab."""
import json, time, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

PAIRS = [
    "B-BTC_USDT", "B-ETH_USDT", "B-SOL_USDT", "B-XRP_USDT",
    "B-BNB_USDT", "B-DOGE_USDT", "B-ADA_USDT", "B-SUI_USDT",
    "B-AVAX_USDT", "B-LINK_USDT", "B-TAO_USDT", "B-ZEC_USDT",
]
RESOLUTION = "5"
DAYS = 30
LOOKBACK = 20
BASE = "https://public.coindcx.com/market_data/candlesticks"
CFG = {"capital": 100000.0, "risk": 0.10, "sl": 0.50, "tp": 1.0,
       "fee": 5.0, "slip": 2.0, "maxTrades": 10}
SECONDS = int(RESOLUTION) * 60
NEED = DAYS * 86400 // SECONDS + 100
CHUNK = 850

def fetch_candles(pair):
    now = int(time.time())
    all_rows = []
    to_ts = now
    for _ in range((NEED + CHUNK - 1) // CHUNK):
        from_ts = max(0, to_ts - CHUNK * SECONDS - 60)
        query = urllib.parse.urlencode({
            "pair": pair, "from": from_ts, "to": to_ts,
            "resolution": RESOLUTION, "pcode": "f"
        })
        req = urllib.request.Request(
            BASE + "?" + query,
            headers={"User-Agent": "AMTE-TurtleSoup-Research/1.0", "Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        rows = payload.get("data", []) if isinstance(payload, dict) else payload
        part = []
        for row in rows:
            try:
                stamp = int(float(row["time"]))
                if stamp < 10**12:
                    stamp *= 1000
                candle = {"time": stamp, "open": float(row["open"]),
                          "high": float(row["high"]), "low": float(row["low"]),
                          "close": float(row["close"])}
                if candle["time"] and candle["close"] > 0:
                    part.append(candle)
            except (KeyError, TypeError, ValueError):
                continue
        if not part:
            break
        all_rows.extend(part)
        oldest = min(x["time"] for x in part)
        if oldest >= to_ts * 1000 or len(part) < 2:
            break
        to_ts = oldest // 1000 - SECONDS
        time.sleep(0.12)
    unique = {row["time"]: row for row in all_rows}
    candles = sorted(unique.values(), key=lambda row: row["time"])[-NEED:]
    if len(candles) < LOOKBACK + 5:
        raise ValueError(f"Too few candles: {len(candles)}")
    return candles

def get_signals(rows, length):
    out = [0] * len(rows)
    for i in range(length + 1, len(rows)):
        hi = max(x["high"] for x in rows[i-length:i])
        lo = min(x["low"] for x in rows[i-length:i])
        x = rows[i]
        if x["low"] < lo and x["close"] > lo:
            out[i] = 1
        elif x["high"] > hi and x["close"] < hi:
            out[i] = -1
    return out

def simulate(rows, signals, inverse):
    equity = CFG["capital"]
    peak = equity
    max_dd = 0.0
    pos = None
    trades = []
    daily = {}
    fee = CFG["fee"] / 10000
    slip = CFG["slip"] / 10000
    fees_total = 0.0
    for i in range(1, len(rows)):
        x = rows[i]
        day = datetime.fromtimestamp(x["time"] / 1000, timezone.utc).date().isoformat()
        daily.setdefault(day, 0)
        if pos:
            exit_price = None
            reason = ""
            if pos["side"] == 1:
                if x["low"] <= pos["sl"]:
                    exit_price, reason = pos["sl"] * (1-slip), "STOP_LOSS"
                elif x["high"] >= pos["tp"]:
                    exit_price, reason = pos["tp"] * (1-slip), "TAKE_PROFIT"
            else:
                if x["high"] >= pos["sl"]:
                    exit_price, reason = pos["sl"] * (1+slip), "STOP_LOSS"
                elif x["low"] <= pos["tp"]:
                    exit_price, reason = pos["tp"] * (1+slip), "TAKE_PROFIT"
            if exit_price is not None:
                gross = (exit_price - pos["entry"]) * pos["qty"] * pos["side"]
                exit_fee = exit_price * pos["qty"] * fee
                all_fees = pos["entry_fee"] + exit_fee
                net = gross - all_fees
                equity += gross - exit_fee
                fees_total += exit_fee
                trades.append({"net": net, "reason": reason})
                pos = None
                peak = max(peak, equity)
                max_dd = max(max_dd, peak-equity)
        if not pos and signals[i-1] and daily[day] < CFG["maxTrades"]:
            side = -signals[i-1] if inverse else signals[i-1]
            entry = x["open"] * (1+slip if side == 1 else 1-slip)
            risk_cash = max(0, equity) * CFG["risk"] / 100
            distance = entry * CFG["sl"] / 100
            qty = risk_cash / distance if distance > 0 else 0
            if qty > 0:
                entry_fee = entry * qty * fee
                if equity > entry_fee:
                    equity -= entry_fee
                    fees_total += entry_fee
                    pos = {"side": side, "entry": entry, "qty": qty,
                           "sl": entry*(1-CFG["sl"]/100 if side == 1 else 1+CFG["sl"]/100),
                           "tp": entry*(1+CFG["tp"]/100 if side == 1 else 1-CFG["tp"]/100),
                           "entry_fee": entry_fee}
                    daily[day] += 1
    wins = [t for t in trades if t["net"] > 0]
    losses = [t for t in trades if t["net"] < 0]
    gross_win = sum(t["net"] for t in wins)
    gross_loss = abs(sum(t["net"] for t in losses))
    net = equity - CFG["capital"]
    return {
        "final_equity": round(equity, 2), "net_pnl": round(net, 2),
        "return_pct": round(net/CFG["capital"]*100, 3),
        "closed_trades": len(trades),
        "win_rate_pct": round(len(wins)/len(trades)*100, 2) if trades else 0,
        "profit_factor": round(gross_win/gross_loss, 3) if gross_loss else (None if not gross_win else "Infinity"),
        "max_drawdown": round(max_dd, 2),
        "avg_net_per_trade": round(sum(t["net"] for t in trades)/len(trades), 2) if trades else 0,
        "fees_paid": round(fees_total, 2),
        "open_position_at_end": bool(pos),
    }

out = Path("docs/turtle-inverse-data")
out.mkdir(parents=True, exist_ok=True)
results = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "resolution": RESOLUTION, "days": DAYS, "lookback": LOOKBACK,
    "settings": CFG, "pairs": []
}
for pair in PAIRS:
    try:
        candles = fetch_candles(pair)
        signals = get_signals(candles, LOOKBACK)
        normal = simulate(candles, signals, False)
        inverse = simulate(candles, signals, True)
        item = {
            "pair": pair, "candles": len(candles),
            "start_time": candles[0]["time"], "end_time": candles[-1]["time"],
            "signal_count": sum(1 for s in signals if s),
            "normal": normal, "inverse": inverse,
            "better": "normal" if normal["net_pnl"] > inverse["net_pnl"] else (
                "inverse" if inverse["net_pnl"] > normal["net_pnl"] else "tie"),
            "difference_inverse_minus_normal": round(inverse["net_pnl"] - normal["net_pnl"], 2)
        }
        if pair == "B-BTC_USDT":
            cache = {"generated_at": results["generated_at"], "pair": pair,
                     "resolution": RESOLUTION, "days": DAYS,
                     "count": len(candles), "candles": candles}
            (out / "btc-usdt-5m-30d.json").write_text(json.dumps(cache, separators=(",", ":")))
        print(f'{pair}: normal ₹{normal["net_pnl"]:.2f}, inverse ₹{inverse["net_pnl"]:.2f}, {len(candles)} candles')
        results["pairs"].append(item)
    except Exception as exc:
        print(f"{pair}: ERROR {exc}")
        results["pairs"].append({"pair": pair, "error": str(exc)})
    time.sleep(0.25)
(out / "multi-pair-results.json").write_text(json.dumps(results, separators=(",", ":")))
success = sum(1 for x in results["pairs"] if "normal" in x)
print(f"Completed multi-pair test: {success}/{len(PAIRS)} pairs")
if success == 0:
    raise SystemExit("No pairs could be tested")
