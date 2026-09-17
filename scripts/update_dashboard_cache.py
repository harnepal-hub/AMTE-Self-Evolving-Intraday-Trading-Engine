from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

PUBLIC = "https://public.coindcx.com"
ACTIVE = "https://api.coindcx.com/exchange/v1/derivatives/futures/data/active_instruments?margin_currency_short_name[]=USDT"
OUT = Path("dashboard_data/market_cache.json")


def get(url, params=None):
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def candles(pair: str, now: int):
    params = {
        "pair": pair,
        "from": now - 300 * 5 * 60,
        "to": now,
        "resolution": "5",
        "pcode": "f",
    }
    data = get(f"{PUBLIC}/market_data/candlesticks", params).get("data", [])
    return pair, data[-300:]


def main():
    now = int(time.time())
    prices = get(f"{PUBLIC}/market_data/v3/current_prices/futures/rt").get("prices", {})
    active = get(ACTIVE)
    pairs = [p for p in active if p in prices and p.endswith("_USDT")]
    pairs.sort(key=lambda p: float(prices[p].get("v", 0) or 0), reverse=True)
    pairs = pairs[:30]

    out = {"updated_at": now, "prices": {p: prices[p] for p in pairs}, "candles": {}}
    with ThreadPoolExecutor(max_workers=8) as ex:
        jobs = [ex.submit(candles, p, now) for p in pairs]
        for job in as_completed(jobs):
            try:
                p, rows = job.result()
                if rows:
                    out["candles"][p] = rows
            except Exception as exc:
                print("cache error:", exc)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"cached {len(out['candles'])} pairs")


if __name__ == "__main__":
    main()
