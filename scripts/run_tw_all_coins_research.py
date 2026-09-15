"""Cross-coin historical research for the supplied TW All in One signal.

Downloads active CoinDCX USDT futures 5-minute candles, filters the frequent TW
signals with a small pre-declared grid, selects configurations by cross-coin
validation stability, and evaluates the locked test period. No live orders.
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from app.analytics.performance import summarize_performance
from app.backtest.engine import BacktestConfig, run_backtest
from app.signals.tw_all_in_one_filtered import filtered_signals

BASE = "https://public.coindcx.com/market_data/candlesticks"
ACTIVE = "https://api.coindcx.com/exchange/v1/derivatives/futures/data/active_instruments?margin_currency_short_name[]=USDT"
CHUNK_DAYS = 7

GRID = [
    {"hull_length": h, "ema_length": e, "ema_filter": ef, "slope_filter": sf,
     "volume_ratio_min": v, "atr_expansion_min": a, "ema_distance_min": d,
     "rsi_filter": r, "cooldown_bars": c}
    for h in (8, 12, 16)
    for e in (100, 200)
    for ef in (True,)
    for sf in (True, False)
    for v in (0.0, 1.2, 1.5)
    for a in (0.0, 1.1)
    for d in (0.0, 0.003)
    for r in (False,)
    for c in (0, 3)
]

CFG = BacktestConfig(
    initial_capital=100_000.0,
    risk_per_trade=0.0025,
    stop_loss_pct=0.005,
    take_profit_pct=0.01,
    fee_bps_per_side=5.0,
    slippage_bps=2.0,
    max_daily_loss_pct=0.02,
    cooldown_bars=0,
    square_off_at_session_end=False,
)


def active_pairs() -> list[str]:
    r = requests.get(ACTIVE, timeout=30)
    r.raise_for_status()
    pairs = [x for x in r.json() if isinstance(x, str) and x.endswith("_USDT")]
    return sorted(set(pairs))


def fetch_pair(pair: str, days: int) -> pd.DataFrame:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    rows = []
    s = requests.Session()
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=CHUNK_DAYS), end)
        p = {"pair": pair, "from": int(cur.timestamp()), "to": int(nxt.timestamp()),
             "resolution": "5", "pcode": "f"}
        rr = s.get(BASE, params=p, timeout=30)
        rr.raise_for_status()
        payload = rr.json()
        batch = payload.get("data", []) if isinstance(payload, dict) else payload
        rows.extend(batch or [])
        cur = nxt
        time.sleep(0.03)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    df = df.set_index("timestamp")[["open", "high", "low", "close", "volume"]]
    df = df.apply(pd.to_numeric, errors="coerce").sort_index()
    df = df[~df.index.duplicated(keep="last")].dropna()
    return df


def cap_daily_signals(sig: pd.Series, max_per_day: int = 5) -> pd.Series:
    out = sig.copy()
    counts: dict[object, int] = {}
    for i, x in enumerate(out.to_numpy()):
        if not x:
            continue
        day = out.index[i].date()
        n = counts.get(day, 0)
        if n >= max_per_day:
            out.iloc[i] = 0
        else:
            counts[day] = n + 1
    return out


def metrics(bars: pd.DataFrame, spec: dict) -> dict:
    sig = filtered_signals(bars, **spec)
    sig = cap_daily_signals(sig, 5)
    trades, equity = run_backtest(bars, sig, CFG)
    m = summarize_performance(trades, equity, CFG.initial_capital)
    m["signals"] = int((sig != 0).sum())
    return m


def forward_edge(bars: pd.DataFrame, spec: dict, horizon: int = 3) -> dict:
    sig = cap_daily_signals(filtered_signals(bars, **spec), 5)
    fwd = bars["close"].shift(-horizon) / bars["close"] - 1
    valid = sig != 0
    signed = fwd.where(valid) * sig.where(valid)
    return {
        "signals": int(valid.sum()),
        "edge_bps": float(signed.mean() * 10000) if valid.any() else 0.0,
        "edge_win_rate": float((signed > 0).sum() / valid.sum()) if valid.any() else 0.0,
    }


def score_validation(rows: list[dict]) -> float:
    df = pd.DataFrame(rows)
    if df.empty:
        return -1e18
    pf = pd.to_numeric(df["profit_factor"], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0)
    profitable = float((df["net_pnl"] > 0).mean())
    med_pnl = float(df["net_pnl"].median())
    med_pf = float(pf.median())
    return med_pnl + 3000 * (profitable - 0.5) + 1500 * max(0, med_pf - 1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--max-coins", type=int, default=0, help="0 means every active USDT futures instrument")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--out", default="research_artifacts/tw_all_coins")
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)

    pairs = active_pairs()
    if args.max_coins > 0:
        pairs = pairs[:args.max_coins]
    print(f"Active USDT futures instruments selected: {len(pairs)}", flush=True)

    data: dict[str, pd.DataFrame] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_pair, p, args.days): p for p in pairs}
        for fut in as_completed(futs):
            pair = futs[fut]
            try:
                df = fut.result()
                if len(df) >= 2000:
                    data[pair] = df
                    print(f"{pair}: {len(df):,} bars", flush=True)
                else:
                    print(f"{pair}: skipped ({len(df):,} bars)", flush=True)
            except Exception as exc:
                print(f"{pair}: ERROR {exc}", flush=True)

    # Chronological 60/20/20 split per coin. Selection happens only on validation.
    split_data = {}
    for pair, df in data.items():
        n = len(df)
        a, b = int(n * .60), int(n * .80)
        split_data[pair] = (df.iloc[:a], df.iloc[a:b], df.iloc[b:])

    candidates = []
    for idx, spec in enumerate(GRID):
        val_rows = []
        dev_rows = []
        for pair, (_, val, _) in split_data.items():
            try:
                m = metrics(val, spec)
                m["pair"] = pair
                val_rows.append(m)
            except Exception as exc:
                print(f"validation error {pair} grid={idx}: {exc}", flush=True)
        if not val_rows:
            continue
        candidates.append({
            "grid_id": idx,
            **spec,
            "coins_tested": len(val_rows),
            "profitable_coin_fraction": float((pd.DataFrame(val_rows)["net_pnl"] > 0).mean()),
            "median_net_pnl": float(pd.DataFrame(val_rows)["net_pnl"].median()),
            "median_profit_factor": float(pd.to_numeric(pd.DataFrame(val_rows)["profit_factor"], errors="coerce").replace([np.inf, -np.inf], np.nan).median()),
            "score": score_validation(val_rows),
        })

    leaderboard = pd.DataFrame(candidates).sort_values("score", ascending=False).reset_index(drop=True)
    leaderboard.to_csv(out / "validation_leaderboard.csv", index=False)
    if leaderboard.empty:
        raise RuntimeError("No configuration produced validation results")

    winner = leaderboard.iloc[0].to_dict()
    spec = {k: winner[k] for k in GRID[0]}
    # Locked test: evaluate the selected universal filter stack across every coin.
    test_rows = []
    dev_rows = []
    for pair, (dev, val, test) in split_data.items():
        dm = metrics(dev, spec); vm = metrics(val, spec); tm = metrics(test, spec)
        fe = forward_edge(test, spec)
        test_rows.append({"pair": pair, **tm, **{f"test_{k}": v for k, v in fe.items()}})
        dev_rows.append({"pair": pair, **dm})

    test_df = pd.DataFrame(test_rows)
    test_df.to_csv(out / "locked_test_by_coin.csv", index=False)
    dev_df = pd.DataFrame(dev_rows)
    dev_df.to_csv(out / "development_by_coin.csv", index=False)

    pf = pd.to_numeric(test_df["profit_factor"], errors="coerce").replace([np.inf, -np.inf], np.nan)
    summary = {
        "data_source": "CoinDCX public USDT futures 5m candles",
        "days": args.days,
        "coins_selected": len(pairs),
        "coins_with_usable_history": len(data),
        "grid_size": len(GRID),
        "winner": spec,
        "validation": {
            "profitable_coin_fraction": winner["profitable_coin_fraction"],
            "median_net_pnl": winner["median_net_pnl"],
            "median_profit_factor": winner["median_profit_factor"],
        },
        "locked_test": {
            "profitable_coin_fraction": float((test_df["net_pnl"] > 0).mean()),
            "median_net_pnl": float(test_df["net_pnl"].median()),
            "median_profit_factor": float(pf.median()),
            "total_net_pnl_if_100k_per_coin": float(test_df["net_pnl"].sum()),
            "total_trades": int(test_df["trades"].sum()),
            "median_max_drawdown_pct": float(test_df["max_drawdown_pct"].median()),
        },
        "decision": "PASS_FOR_PAPER_ONLY" if (
            winner["profitable_coin_fraction"] >= .55 and winner["median_profit_factor"] > 1.0
            and float((test_df["net_pnl"] > 0).mean()) >= .50 and float(pf.median()) > 1.0
        ) else "NO_GO",
        "note": "The test set is locked after universal filter selection; no test metric is used to tune the winner.",
    }
    (out / "TW_ALL_COINS_RESULT.json").write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str), flush=True)


if __name__ == "__main__": main()
