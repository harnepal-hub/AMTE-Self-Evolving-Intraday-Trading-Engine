"""Research-only diagnostics for identifying where existing signals fail.

No strategy parameters are tuned here. The report partitions existing strategy
trades by direction, UTC hour, volatility regime and trend regime, using the
same causal feature pipeline and cost-aware backtester.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import pandas as pd

from app.backtest.engine import BacktestConfig, backtest
from app.signals.runner import generate_signals

STRATEGIES = (
    "pullback_continuation", "volatility_breakout", "momentum_regime",
    "regime_breakout", "momentum_breakout", "connors_rsi2",
    "brooks_first_pullback", "regime_vwap_reversion", "trend_pullback",
    "vwap_reversion", "orb_breakout", "ema_trend",
)

def load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["timestamp"])
    return df.sort_values("timestamp").reset_index(drop=True)

def run(df: pd.DataFrame, strategy: str, cfg: BacktestConfig) -> pd.DataFrame:
    sig = generate_signals(df, strategy)
    result = backtest(sig, cfg)
    rows = []
    for t in result.trades:
        entry = pd.Timestamp(t.entry_time)
        row = sig[sig["timestamp"] == entry]
        if row.empty:
            continue
        r = row.iloc[-1]
        rows.append({
            "strategy": strategy, "entry_time": entry, "direction": t.direction,
            "net_pnl": t.net_pnl, "hour": entry.hour,
            "atr_pct": float(r.get("atr_pct", 0.0) or 0.0),
            "trend_strength": float(r.get("trend_strength", 0.0) or 0.0),
        })
    return pd.DataFrame(rows)

def summarize(trades: pd.DataFrame, key: str) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    g = trades.groupby(key, dropna=False)
    out = g["net_pnl"].agg(["count", "sum", "mean"]).reset_index()
    out["win_rate"] = g["net_pnl"].apply(lambda x: (x > 0).mean()).values
    return out.sort_values("sum", ascending=False)

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", default="research_artifacts/regime_diagnostics")
    args = ap.parse_args()
    out = Path(args.output); out.mkdir(parents=True, exist_ok=True)
    df = load(Path(args.input))
    cfg = BacktestConfig(initial_capital=100_000, risk_per_trade=.005,
        stop_loss_pct=.005, take_profit_pct=.01, fee_bps_per_side=5,
        slippage_bps=2, square_off_at_session_end=False)
    all_trades = pd.concat([run(df, s, cfg) for s in STRATEGIES], ignore_index=True)
    all_trades.to_csv(out / "trades.csv", index=False)
    for key, name in [("direction", "direction"), ("hour", "hour"),
                      ("strategy", "strategy"), ("atr_bucket", "atr_bucket"),
                      ("trend_bucket", "trend_bucket")]:
        if key == "atr_bucket":
            all_trades[key] = pd.qcut(all_trades["atr_pct"], 4, duplicates="drop")
        elif key == "trend_bucket":
            all_trades[key] = pd.qcut(all_trades["trend_strength"], 4, duplicates="drop")
        summarize(all_trades, key).to_csv(out / f"{name}.csv", index=False)
    report = []
    for strategy, g in all_trades.groupby("strategy"):
        report.append({"strategy": strategy, "trades": len(g),
                       "net_pnl": g.net_pnl.sum(), "win_rate": (g.net_pnl > 0).mean(),
                       "best_hour": int(g.groupby("hour").net_pnl.sum().idxmax()),
                       "best_direction": g.groupby("direction").net_pnl.sum().idxmax()})
    pd.DataFrame(report).sort_values("net_pnl", ascending=False).to_csv(out / "summary.csv", index=False)
    print(f"Diagnostics complete: trades={len(all_trades)} output={out}")

if __name__ == "__main__": main()
