"""Run Stage 4 walk-forward and Stage 5 robustness validation after Stage 3 selection."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from app.backtest.engine import run_backtest
from app.research.robustness import monte_carlo_trade_order
from app.research.stage3 import evaluate_candidates, realistic_config
from app.research.walk_forward import evaluate_stress, evaluate_walk_forward
from app.signals.runner import generate_signals
from scripts.run_stage3_research import fetch_history


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--output-dir", default="research_artifacts/stage4_5")
    args = parser.parse_args()

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    bars = fetch_history(args.days)
    dataset_path = out / "coindcx_btc_usdt_5m.csv"
    bars.to_csv(dataset_path, index_label="timestamp")
    dataset_sha256 = hashlib.sha256(dataset_path.read_bytes()).hexdigest()

    split = int(len(bars) * 0.80)
    development = bars.iloc[:split].copy()
    holdout = bars.iloc[split:].copy()
    config = realistic_config()

    leaderboard = evaluate_candidates(development, config)
    winner = str(leaderboard.iloc[0]["strategy"])

    # Stage 4: chronological out-of-sample windows on development only.
    bars_per_day = 24 * 60 // 5
    wf = evaluate_walk_forward(
        development,
        winner,
        config,
        train_bars=30 * bars_per_day,
        test_bars=7 * bars_per_day,
        step_bars=7 * bars_per_day,
    )
    wf.to_csv(out / "stage4_walk_forward.csv", index=False)

    # Stage 5: locked future holdout is evaluated only after strategy selection.
    stress = evaluate_stress(holdout, winner, config)
    stress.to_csv(out / "stage5_cost_stress.csv", index=False)
    holdout_signals = generate_signals(holdout, winner)
    holdout_trades, holdout_equity = run_backtest(holdout, holdout_signals, config)
    mc = monte_carlo_trade_order(holdout_trades, simulations=2000, seed=42)
    (out / "stage5_monte_carlo.json").write_text(json.dumps(mc, indent=2) + "\n", encoding="utf-8")

    wf_profitable = int((wf["net_pnl"] > 0).sum()) if not wf.empty else 0
    wf_count = len(wf)
    holdout_row = stress.loc[stress["scenario"] == "base"].iloc[0].to_dict()
    final = {
        "status": "completed",
        "dataset": {
            "source": "CoinDCX public candles API",
            "pair": "B-BTC_USDT",
            "interval": "5m",
            "rows": len(bars),
            "start": bars.index.min().isoformat(),
            "end": bars.index.max().isoformat(),
            "sha256": dataset_sha256,
            "development_rows": len(development),
            "holdout_rows": len(holdout),
        },
        "selection": {
            "winner": winner,
            "rule": "highest development combined net P&L only",
        },
        "stage4": {
            "windows": wf_count,
            "profitable_windows": wf_profitable,
            "profitable_window_fraction": (wf_profitable / wf_count) if wf_count else 0.0,
            "total_net_pnl": float(wf["net_pnl"].sum()) if not wf.empty else 0.0,
            "median_window_net_pnl": float(wf["net_pnl"].median()) if not wf.empty else 0.0,
        },
        "stage5_holdout": {
            "base": holdout_row,
            "monte_carlo": mc,
            "decision": "PASS_FOR_PAPER_ONLY" if holdout_row.get("net_pnl", 0) > 0 and mc.get("p05_terminal_pnl", 0) > 0 else "NO_GO",
        },
    }
    (out / "FINAL_VALIDATION.json").write_text(json.dumps(final, indent=2, default=str) + "\n", encoding="utf-8")

    lines = [
        "# AMTE Stage 4–5 Validation",
        "",
        f"**Selected strategy:** `{winner}`",
        "",
        "Selection used development data only. The final 20% holdout was not used to choose the strategy.",
        "",
        "## Stage 4 — Walk-forward",
        f"- Windows: {wf_count}",
        f"- Profitable windows: {wf_profitable}/{wf_count}",
        f"- Total window net P&L: ₹{wf['net_pnl'].sum():,.2f}" if not wf.empty else "- No windows produced",
        f"- Median window net P&L: ₹{wf['net_pnl'].median():,.2f}" if not wf.empty else "- Median unavailable",
        "",
        "## Stage 5 — Locked holdout + stress",
        f"- Holdout base net P&L: ₹{holdout_row.get('net_pnl', 0):,.2f}",
        f"- Holdout profit factor: {holdout_row.get('profit_factor', 0):.4f}",
        f"- Holdout max drawdown: {holdout_row.get('max_drawdown_pct', 0):.2f}%",
        f"- Monte Carlo P05 terminal P&L: ₹{mc.get('p05_terminal_pnl', 0):,.2f}",
        f"- Monte Carlo worst terminal P&L: ₹{mc.get('worst_terminal_pnl', 0):,.2f}",
        f"- Decision: **{final['stage5_holdout']['decision']}**",
        "",
        "A PASS here means eligible for paper trading validation, not live trading. Live deployment remains blocked until paper-trading evidence, operational checks, and exchange execution controls are verified.",
    ]
    (out / "FINAL_VALIDATION.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Stage 4–5 complete: {winner}; decision={final['stage5_holdout']['decision']}")


if __name__ == "__main__":
    main()
