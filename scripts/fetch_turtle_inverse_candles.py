#!/usr/bin/env python3
"""Fetch public CoinDCX futures candles for the Turtle Soup research lab."""
import json, time, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

PAIR = "B-BTC_USDT"
RESOLUTION = "5"
DAYS = 30
BASE = "https://public.coindcx.com/market_data/candlesticks"
now = int(time.time())
seconds = int(RESOLUTION) * 60
need = DAYS * 86400 // seconds + 100
chunk = 850
all_rows = []
to_ts = now

for _ in range((need + chunk - 1) // chunk):
    from_ts = max(0, to_ts - chunk * seconds - 60)
    query = urllib.parse.urlencode({
        "pair": PAIR, "from": from_ts, "to": to_ts,
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
            candle = {
                "time": stamp,
                "open": float(row["open"]), "high": float(row["high"]),
                "low": float(row["low"]), "close": float(row["close"])
            }
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
    to_ts = oldest // 1000 - seconds
    time.sleep(0.15)

unique = {row["time"]: row for row in all_rows}
candles = sorted(unique.values(), key=lambda row: row["time"])[-need:]
if len(candles) < 100:
    raise SystemExit(f"Too few candles fetched from CoinDCX: {len(candles)}")
out = Path("docs/turtle-inverse-data")
out.mkdir(parents=True, exist_ok=True)
payload = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "pair": PAIR, "resolution": RESOLUTION, "days": DAYS,
    "count": len(candles), "candles": candles
}
(out / "btc-usdt-5m-30d.json").write_text(json.dumps(payload, separators=(",", ":")))
print(f"Published dataset prepared: {len(candles)} candles, {candles[0]['time']} to {candles[-1]['time']}")
