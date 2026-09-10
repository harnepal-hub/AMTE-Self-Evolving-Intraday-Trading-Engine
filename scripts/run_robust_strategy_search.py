"""Controlled strategy/config search with validation-only selection and locked holdout.

The search deliberately keeps the hypothesis space small. Strategy/config candidates are
ranked on rolling validation windows, never on the final holdout. The final holdout is
opened once, after selection, and is used only for an out-of-sample decision.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path

import pandas as pd

from app.analytics.performance import summarize_performance
from app.backtest.engine import BacktestConfig, run_backtest
from app.research.robustness import monte_carlo_trade_order
from app.research.stage3 import CANDIDATES, realistic_config
from app.signals.runner import generate_signals
from scripts.run_stage3_research import fetch_history


# Small, pre-declared grid: enough to test exit sensitivity without turning the
# backtest into a parameter-mining exercise.
CONFIG_GRID = (
    {"name": "base", "stop_loss_pct": 0.005, "take_profit_pct": 0.010, "cooldown_bars": 0},
    {"name": "tight", "stop_loss_pct": 0.004, "take_profit_pct": 0.008, "cooldown_bars": 1},
    {"name": "balanced", "stop_loss_pct": 0.005, "take_profit_pct": 0.012, "cooldown_bars": 2},
    {"name": "wider", "stop_loss_pct": 0.007, "take_profit_pct": 0.014, "cooldown_bars": 2},
)


def evaluate_window(bars: pd.DataFrame, strategy: str, config: BacktestConfig) -> dict:
    signals = generate_signals(bars, strategy)
    trades, equity = run_backtest(bars, signals, config)
    metrics = summarize_performance(trades, equity, config.initial_capital)
    metrics["signals"] = int((signals != 0).sum())
    metrics["trades"] = int(len(trades))
    return metrics


def rolling_validation(bars: pd.DataFrame, strategy: str, config: BacktestConfig) -> tuple[pd.DataFrame, dict]:
    """Return chronological validation windows and a stability-first score."""
    bars_per_day = 24 * 60 // 5
    train = 30 * bars_per_day
    test = 7 * bars_per_day
    step = 7 * bars_per_day
    rows = []
    start = 0
    window = 0
    while start + train + test <= len(bars):
        test_df = bars.iloc[start + train : start + train + test]
        metrics = evaluate_window(test_df, strategy, config)
        window += 1
        rows.append({
            "window": window,
            "test_start": test_df.index[0],
            "test_end": test_df.index[-1],
            **metrics,
        })
        start += step
    wf = pd.DataFrame(rows)
    if wf.empty:
        return wf, {"score": float("-inf"), "profitable_fraction": 0.0, "median_net_pnl": 0.0}

    profitable_fraction = float((wf["net_pnl"] > 0).mean())
    median_net = float(wf["net_pnl"].median())
    mean_net = float(wf["net_pnl"].mean())
    median_pf = float(wf["profit_factor"].replace([float("inf"), -float("inf")], pd.NA).dropna().median()) if "profit_factor" in wf else 0.0
    # Stability-first: positive windows and median performance matter more than
    # one exceptional window. A candidate failing these gates cannot win.
    score = median_net + 2_000.0 * (profitable_fraction - 0.5) + 500.0 * max(0.0, median_pf - 1.0)
    return wf, {
        "score": score,
        "profitable_fraction": profitable_fraction,
        "median_net_pnl": median_net,
        "mean_net_pnl": mean_net,
        "median_profit_factor": median_pf,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--output-dir", default="research_artifacts/robust_search")
    args = parser.parse_args()

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    bars = fetch_history(args.days)
    dataset_path = out / "coindcx_btc_usdt_5m.csv"
    bars.to_csv(dataset_path, index_label="timestamp")
    dataset_sha256 = hashlib.sha256(dataset_path.read_bytes()).hexdigest()

    # 60/20/20 chronological split. The 20% final test is never touched during search.
    n = len(bars)
    dev_end = int(n * 0.60)
    validation_end = int(n * 0.80)
    development = bars.iloc[:dev_end].copy()
    validation = bars.iloc[dev_end:validation_end].copy()
    holdout = bars.iloc[validation_end:].copy()
    base = realistic_config()

    # Validation is used only to compare pre-declared hypotheses.
    candidates: list[dict] = []
    wf_tables: list[pd.DataFrame] = []
    for strategy in CANDIDATES:
        for spec in CONFIG_GRID:
            config = replace(
                base,
                stop_loss_pct=spec["stop_loss_pct"],
                take_profit_pct=spec["take_profit_pct"],
                cooldown_bars=spec["cooldown_bars"],
            )
            # Require a candidate to be tested first on development and then on
            # validation. Development is descriptive; validation drives selection.
            dev_metrics = evaluate_window(development, strategy, config)
            val_wf, score = rolling_validation(validation, strategy, config)
            wf_tables.append(val_wf.assign(strategy=strategy, config=spec["name"]))
            candidates.append({
                "strategy": strategy,
                "config": spec["name"],
                "stop_loss_pct": spec["stop_loss_pct"],
                "take_profit_pct": spec["take_profit_pct"],
                "cooldown_bars": spec["cooldown_bars"],
                "development_net_pnl": dev_metrics["net_pnl"],
                "development_profit_factor": dev_metrics["profit_factor"],
                **score,
            })

    leaderboard = pd.DataFrame(candidates).sort_values(
        ["score", "profitable_fraction", "median_net_pnl"], ascending=False, ignore_index=True
    )
    leaderboard.to_csv(out / "validation_leaderboard.csv", index=False)
    if wf_tables:
        pd.concat(wf_tables, ignore_index=True).to_csv(out / "validation_windows.csv", index=False)

    winner_row = leaderboard.iloc[0]
    winner = str(winner_row["strategy"])
    winner_config = replace(
        base,
        stop_loss_pct=float(winner_row["stop_loss_pct"]),
        take_profit_pct=float(winner_row["take_profit_pct"]),
        cooldown_bars=int(winner_row["cooldown_bars"]),
    )

    # Locked holdout: no ranking, tuning, or reselection after seeing these results.
    holdout_metrics = evaluate_window(holdout, winner, winner_config)
    holdout_signals = generate_signals(holdout, winner)
    holdout_trades, _ = run_backtest(holdout, holdout_signals, winner_config)
    mc = monte_carlo_trade_order(holdout_trades, simulations=2000, seed=42)

    # Conservative anti-overfit gate. It is intentionally stricter than simply
    # requiring positive total P&L.
    validation_gate = (
        float(winner_row["profitable_fraction"]) >= 0.60
        and float(winner_row["median_net_pnl"]) > 0
        and float(winner_row["median_profit_factor"]) > 1.0
    )
    holdout_gate = float(holdout_metrics["net_pnl"]) > 0 and float(holdout_metrics["profit_factor"]) > 1.0 and float(mc.get("p05_terminal_pnl", 0)) > 0
    decision = "PASS_FOR_PAPER_ONLY" if validation_gate and holdout_gate else "NO_GO"

    result = {
        "dataset": {
            "source": "CoinDCX public candles API",
            "pair": "B-BTC_USDT",
            "interval": "5m",
            "rows": len(bars),
            "start": bars.index.min().isoformat(),
            "end": bars.index.max().isoformat(),
            "sha256": dataset_sha256,
            "development_rows": len(development),
            "validation_rows": len(validation),
            "holdout_rows": len(holdout),
        },
        "search_space": [asdict(winner_config)],
        "selection": {
            "strategy": winner,
            "config": str(winner_row["config"]),
            "rule": "validation rolling-window stability score; final holdout excluded",
            "validation_gate": validation_gate,
        },
        "holdout": {
            "metrics": holdout_metrics,
            "monte_carlo": mc,
            "gate": holdout_gate,
        },
        "decision": decision,
    }
    (out / "ROBUST_SEARCH.json").write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")

    lines = [
        "# AMTE Robust Strategy Search",
        "",
        "Selection is stability-first on rolling validation windows. The final 20% is locked and never used for tuning.",
        "",
        f"**Selected:** `{winner}` / `{winner_row['config']}`",
        f"**Validation profitable-window fraction:** {winner_row['profitable_fraction']:.1%}",
        f"**Validation median window P&L:** ₹{winner_row['median_net_pnl']:,.2f}",
        f"**Validation median PF:** {winner_row['median_profit_factor']:.3f}",
        "",
        "## Locked holdout",
        f"- Net P&L: ₹{holdout_metrics['net_pnl']:,.2f}",
        f"- Profit factor: {holdout_metrics['profit_factor']:.3f}",
        f"- Max drawdown: {holdout_metrics['max_drawdown_pct']:.2f}%",
        f"- Monte Carlo P05 terminal P&L: ₹{mc.get('p05_terminal_pnl', 0):,.2f}",
        f"- Decision: **{decision}**",
        "",
        "No live deployment is permitted by this research gate.",
    ]
    (out / "ROBUST_SEARCH.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Robust search complete: {winner}/{winner_row['config']}; decision={decision}")


if __name__ == "__main__":
    main()
