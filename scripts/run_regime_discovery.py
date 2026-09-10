"""Discover simple regime-conditioned intraday edges with a strict OOS gate.

The search is intentionally small: a fixed family of causal conditions is
combined into interpretable long/short archetypes. Selection happens on a
60% development / 20% validation split. The final 20% is touched exactly once
for the selected candidate. No parameter is tuned on the final holdout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from app.backtest.engine import BacktestConfig, run_backtest
from app.features.indicators import add_features
from app.analytics.performance import summarize_performance
from scripts.run_stage3_research import fetch_history


def _signals(df: pd.DataFrame, name: str) -> pd.Series:
    f = add_features(df)
    trend_up = f["ema_fast"] > f["ema_slow"]
    trend_down = f["ema_fast"] < f["ema_slow"]
    strong = f["trend_strength"] >= 0.60
    weak = f["trend_strength"] <= 0.65
    vol = f["volume_ratio"] >= 1.05
    expanding = f["atr_pct"] >= f["atr_pct_mean"]
    compressed = f["atr_pct"] <= f["atr_pct_mean"] * 0.90
    mom_up = f["momentum"] > 0
    mom_down = f["momentum"] < 0
    above_vwap = f["close"] > f["vwap"]
    below_vwap = f["close"] < f["vwap"]
    near_ema = (f["close"] - f["ema_fast"]).abs() <= f["atr"] * 0.50
    breakout_up = f["close"] > f["prior_high"]
    breakout_down = f["close"] < f["prior_low"]

    long = pd.Series(False, index=f.index)
    short = pd.Series(False, index=f.index)
    if name == "trend_pullback_confirmed":
        long = trend_up & strong & near_ema & mom_up & above_vwap
        short = trend_down & strong & near_ema & mom_down & below_vwap
    elif name == "trend_breakout_confirmed":
        long = trend_up & strong & vol & expanding & breakout_up & above_vwap
        short = trend_down & strong & vol & expanding & breakout_down & below_vwap
    elif name == "compression_breakout":
        long = trend_up & vol & compressed & breakout_up
        short = trend_down & vol & compressed & breakout_down
    elif name == "vwap_reclaim_momentum":
        reclaim_up = above_vwap & f["close"].shift(1).le(f["vwap"].shift(1))
        reclaim_down = below_vwap & f["close"].shift(1).ge(f["vwap"].shift(1))
        long = reclaim_up & strong & mom_up & vol
        short = reclaim_down & strong & mom_down & vol
    elif name == "weak_regime_vwap_fade":
        far = (f["close"] - f["vwap"]).abs() >= f["atr"] * 0.75
        long = weak & far & below_vwap
        short = weak & far & above_vwap
    elif name == "momentum_expansion":
        long = trend_up & strong & vol & expanding & mom_up
        short = trend_down & strong & vol & expanding & mom_down
    elif name == "trend_pullback_volume":
        long = trend_up & strong & near_ema & vol & mom_up
        short = trend_down & strong & near_ema & vol & mom_down
    elif name == "breakout_vwap_volume":
        long = breakout_up & above_vwap & vol
        short = breakout_down & below_vwap & vol
    else:
        raise ValueError(name)
    return pd.Series(0, index=f.index, dtype=int).mask(long, 1).mask(short, -1)


CANDIDATES = (
    "trend_pullback_confirmed",
    "trend_breakout_confirmed",
    "compression_breakout",
    "vwap_reclaim_momentum",
    "weak_regime_vwap_fade",
    "momentum_expansion",
    "trend_pullback_volume",
    "breakout_vwap_volume",
)


def _config() -> BacktestConfig:
    return BacktestConfig(
        initial_capital=100_000,
        risk_per_trade=0.005,
        stop_loss_pct=0.005,
        take_profit_pct=0.01,
        fee_bps_per_side=5.0,
        slippage_bps=2.0,
        square_off_at_session_end=False,
    )


def _score(row: dict) -> tuple:
    # Stability first, return second. This deliberately avoids selecting on
    # raw development P&L alone.
    return (
        row["validation_positive_fraction"],
        row["validation_median_pf"],
        row["validation_median_pnl"],
        row["development_pnl"],
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--output-dir", default="research_artifacts/regime_discovery")
    args = p.parse_args()
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    bars = fetch_history(args.days)
    dataset = out / "coindcx_btc_usdt_5m.csv"
    bars.to_csv(dataset, index_label="timestamp")
    sha256 = hashlib.sha256(dataset.read_bytes()).hexdigest()
    n = len(bars)
    d1, d2 = int(n * 0.60), int(n * 0.80)
    development, validation, holdout = bars.iloc[:d1], bars.iloc[d1:d2], bars.iloc[d2:]
    cfg = _config()

    rows = []
    for name in CANDIDATES:
        dev_trades, dev_eq = run_backtest(development, _signals(development, name), cfg)
        val_trades, val_eq = run_backtest(validation, _signals(validation, name), cfg)
        dev = summarize_performance(dev_trades, dev_eq, cfg.initial_capital)
        val = summarize_performance(val_trades, val_eq, cfg.initial_capital)
        # Validation is divided into chronological 7-day blocks; no tuning is
        # performed inside those blocks.
        block_pnls, block_pfs = [], []
        for block in range(0, len(validation), 24 * 12 * 7):
            chunk = validation.iloc[block:block + 24 * 12 * 7]
            if len(chunk) < 24 * 12 * 3:
                continue
            t, e = run_backtest(chunk, _signals(chunk, name), cfg)
            m = summarize_performance(t, e, cfg.initial_capital)
            block_pnls.append(float(m["net_pnl"]))
            block_pfs.append(float(m["profit_factor"]))
        rows.append({
            "strategy": name,
            "development_pnl": float(dev["net_pnl"]),
            "development_pf": float(dev["profit_factor"]),
            "validation_pnl": float(val["net_pnl"]),
            "validation_pf": float(val["profit_factor"]),
            "validation_trades": int(val.get("trades", 0)),
            "validation_positive_fraction": sum(x > 0 for x in block_pnls) / len(block_pnls) if block_pnls else 0.0,
            "validation_median_pnl": float(pd.Series(block_pnls).median()) if block_pnls else 0.0,
            "validation_median_pf": float(pd.Series(block_pfs).median()) if block_pfs else 0.0,
        })

    leaderboard = pd.DataFrame(rows)
    leaderboard["score"] = leaderboard.apply(lambda r: _score(r.to_dict()), axis=1)
    leaderboard = leaderboard.sort_values("score", ascending=False, ignore_index=True)
    leaderboard.to_csv(out / "discovery_leaderboard.csv", index=False)
    winner = str(leaderboard.iloc[0]["strategy"])

    # Final holdout is locked: evaluate the selected rule once, without using
    # its result for selection or further modification.
    holdout_trades, holdout_eq = run_backtest(holdout, _signals(holdout, winner), cfg)
    holdout_metrics = summarize_performance(holdout_trades, holdout_eq, cfg.initial_capital)
    holdout_metrics = {k: (float(v) if hasattr(v, "item") else v) for k, v in holdout_metrics.items()}
    decision = "PASS_FOR_PAPER_ONLY" if (
        holdout_metrics.get("net_pnl", 0) > 0
        and holdout_metrics.get("profit_factor", 0) > 1.05
        and leaderboard.iloc[0]["validation_positive_fraction"] >= 0.60
    ) else "NO_GO"

    report = {
        "dataset": {"rows": n, "sha256": sha256, "development_rows": len(development), "validation_rows": len(validation), "holdout_rows": len(holdout)},
        "selection": {"winner": winner, "method": "validation stability first; development P&L only as final tie-breaker", "candidates": list(CANDIDATES)},
        "holdout": holdout_metrics,
        "decision": decision,
    }
    (out / "DISCOVERY_RESULT.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    (out / "DISCOVERY_RESULT.md").write_text(
        "# AMTE Regime Discovery\n\n"
        f"Winner: **{winner}**\n\n"
        "Selection used only development/validation data. The final 20% holdout was evaluated once after selection.\n\n"
        + leaderboard.to_markdown(index=False) + "\n\n"
        + f"Holdout net P&L: ₹{holdout_metrics.get('net_pnl', 0):,.2f}\n\n"
        + f"Holdout profit factor: {holdout_metrics.get('profit_factor', 0):.4f}\n\n"
        + f"Decision: **{decision}**\n",
        encoding="utf-8",
    )
    print(f"Regime discovery complete: {winner}; decision={decision}; sha256={sha256}")


if __name__ == "__main__":
    main()
