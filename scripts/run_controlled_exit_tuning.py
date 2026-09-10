"""Controlled ATR-exit tuning with a locked post-burned-data gate.

The experiment is deliberately small and pre-declared. The previously exposed
Stage-3/regime-discovery holdout is treated as burned. Tuning uses only the
first 80% of the historical sample (60% development + 20% validation). A
final test is attempted only on bars strictly after the burned cutoff. If
there is not enough genuinely new data, the experiment fails closed as
NO_GO_PENDING_FRESH_DATA rather than reusing the burned holdout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from app.features.indicators import add_features
from app.analytics.performance import summarize_performance
from scripts.run_stage3_research import fetch_history


BURNED_CUTOFF = pd.Timestamp("2026-09-10 06:50:00+00:00")
# Small, economically interpretable family. No optimization outside this set.
EXIT_FAMILIES = tuple(
    (sl, tp, hold)
    for sl in (0.8, 1.0, 1.2)
    for tp in (1.5, 2.0)
    for hold in (3, 6, 12)
)


@dataclass(frozen=True)
class Costs:
    fee_bps: float = 5.0
    slippage_bps: float = 2.0
    risk_per_trade: float = 0.005
    initial_capital: float = 100_000.0


def _signals(df: pd.DataFrame) -> pd.Series:
    f = add_features(df)
    trend_up = f["ema_fast"] > f["ema_slow"]
    trend_down = f["ema_fast"] < f["ema_slow"]
    strong = f["trend_strength"] >= 0.60
    vol = f["volume_ratio"] >= 1.05
    mom_up = f["momentum"] > 0
    mom_down = f["momentum"] < 0
    above = f["close"] > f["vwap"]
    below = f["close"] < f["vwap"]
    reclaim_up = above & f["close"].shift(1).le(f["vwap"].shift(1))
    reclaim_down = below & f["close"].shift(1).ge(f["vwap"].shift(1))
    long = reclaim_up & strong & mom_up & vol
    short = reclaim_down & strong & mom_down & vol
    return pd.Series(0, index=f.index, dtype=int).mask(long, 1).mask(short, -1)


def _slip(price: float, side: int, bps: float, entry: bool) -> float:
    x = bps / 10_000.0
    if side == 1:
        return price * (1 + x if entry else 1 - x)
    return price * (1 - x if entry else 1 + x)


def _cost(price: float, qty: float, c: Costs) -> float:
    return price * qty * c.fee_bps / 10_000.0


def backtest(df: pd.DataFrame, signals: pd.Series, sl_atr: float, tp_atr: float, max_hold: int, costs: Costs = Costs()):
    """Minimal dynamic-exit event loop matching AMTE's next-open semantics."""
    f = add_features(df)
    capital = costs.initial_capital
    position = None
    trades = []
    equity = []
    sig = signals.reindex(df.index).fillna(0).astype(int)

    for i in range(len(df)):
        row = df.iloc[i]
        ts = df.index[i]
        if position is not None:
            side = position["side"]
            sl = position["sl"]
            tp = position["tp"]
            exit_px = None
            reason = None
            if i - position["entry_i"] >= max_hold:
                exit_px, reason = float(row.open), "MAX_HOLD"
            elif side == 1:
                if row.open < sl:
                    exit_px, reason = float(row.open), "STOP_GAP"
                elif row.open > tp:
                    exit_px, reason = float(row.open), "TARGET_GAP"
                elif row.low <= sl:
                    exit_px, reason = sl, "STOP"
                elif row.high >= tp:
                    exit_px, reason = tp, "TARGET"
            else:
                if row.open > sl:
                    exit_px, reason = float(row.open), "STOP_GAP"
                elif row.open < tp:
                    exit_px, reason = float(row.open), "TARGET_GAP"
                elif row.high >= sl:
                    exit_px, reason = sl, "STOP"
                elif row.low <= tp:
                    exit_px, reason = tp, "TARGET"
            if exit_px is not None:
                fill = _slip(exit_px, side, costs.slippage_bps, False)
                gross = (fill - position["entry"]) * position["qty"] * side
                exit_cost = _cost(fill, position["qty"], costs)
                net = gross - position["entry_cost"] - exit_cost
                capital += gross - exit_cost
                trades.append({
                    "entry_time": position["entry_time"], "exit_time": ts,
                    "side": "LONG" if side == 1 else "SHORT",
                    "entry_price": position["entry"], "exit_price": fill,
                    "quantity": position["qty"], "gross_pnl": gross,
                    "costs": position["entry_cost"] + exit_cost,
                    "net_pnl": net, "exit_reason": reason,
                })
                position = None

        if position is None and i > 0 and sig.iloc[i - 1] in (1, -1):
            side = int(sig.iloc[i - 1])
            entry = _slip(float(row.open), side, costs.slippage_bps, True)
            atr = float(f.iloc[i - 1]["atr"])
            if pd.notna(atr) and atr > 0:
                stop_distance = sl_atr * atr
                qty = int((capital * costs.risk_per_trade) / stop_distance)
                if qty > 0:
                    entry_cost = _cost(entry, qty, costs)
                    capital -= entry_cost
                    if side == 1:
                        sl, tp = entry - stop_distance, entry + tp_atr * atr
                    else:
                        sl, tp = entry + stop_distance, entry - tp_atr * atr
                    position = {
                        "entry_i": i, "entry_time": ts, "entry": entry,
                        "qty": qty, "side": side, "sl": sl, "tp": tp,
                        "entry_cost": entry_cost,
                    }

        mark = float(row.close)
        unrealized = 0.0 if position is None else (mark - position["entry"]) * position["qty"] * position["side"]
        equity.append({"timestamp": ts, "equity": capital + unrealized})

    if position is not None:
        row = df.iloc[-1]
        fill = _slip(float(row.close), position["side"], costs.slippage_bps, False)
        gross = (fill - position["entry"]) * position["qty"] * position["side"]
        exit_cost = _cost(fill, position["qty"], costs)
        capital += gross - exit_cost
        trades.append({
            "entry_time": position["entry_time"], "exit_time": df.index[-1],
            "side": "LONG" if position["side"] == 1 else "SHORT",
            "entry_price": position["entry"], "exit_price": fill,
            "quantity": position["qty"], "gross_pnl": gross,
            "costs": position["entry_cost"] + exit_cost,
            "net_pnl": gross - position["entry_cost"] - exit_cost,
            "exit_reason": "END_OF_DATA",
        })
        equity[-1]["equity"] = capital

    return pd.DataFrame(trades), pd.DataFrame(equity).set_index("timestamp")


def _metrics(df: pd.DataFrame, family: tuple[float, float, int]) -> dict:
    sl, tp, hold = family
    t, e = backtest(df, _signals(df), sl, tp, hold)
    m = summarize_performance(t, e, 100_000)
    return {
        "sl_atr": sl, "tp_atr": tp, "max_hold_bars": hold,
        "trades": int(m.get("trades", 0)), "net_pnl": float(m.get("net_pnl", 0)),
        "profit_factor": float(m.get("profit_factor", 0)),
        "max_drawdown_pct": float(m.get("max_drawdown_pct", 0)),
        "win_rate": float(m.get("win_rate", 0)),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--min-fresh-bars", type=int, default=24 * 12 * 14)
    p.add_argument("--output-dir", default="research_artifacts/controlled_exit_tuning")
    args = p.parse_args()
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    bars = fetch_history(args.days).sort_index()
    dataset = out / "coindcx_btc_usdt_5m.csv"
    bars.to_csv(dataset, index_label="timestamp")
    sha256 = hashlib.sha256(dataset.read_bytes()).hexdigest()

    n = len(bars)
    d1, d2 = int(n * 0.60), int(n * 0.80)
    development, validation = bars.iloc[:d1], bars.iloc[d1:d2]
    fresh = bars.loc[bars.index > BURNED_CUTOFF]

    rows = []
    for family in EXIT_FAMILIES:
        dev = _metrics(development, family)
        val = _metrics(validation, family)
        # Stability is measured across 7-day validation blocks.
        block = []
        block_size = 24 * 12 * 7
        for start in range(0, len(validation), block_size):
            chunk = validation.iloc[start:start + block_size]
            if len(chunk) < block_size:
                continue
            block.append(_metrics(chunk, family))
        positive_fraction = sum(x["net_pnl"] > 0 for x in block) / len(block) if block else 0.0
        median_pf = float(pd.Series([x["profit_factor"] for x in block]).median()) if block else 0.0
        rows.append({**dev, "development_pnl": dev["net_pnl"],
                     "validation_pnl": val["net_pnl"], "validation_pf": val["profit_factor"],
                     "validation_trades": val["trades"],
                     "validation_positive_fraction": positive_fraction,
                     "validation_median_pf": median_pf})

    board = pd.DataFrame(rows)
    # Stability first; profitability and DD are tie-breakers, not an excuse
    # to choose the most profitable parameter from the grid.
    board = board.sort_values(
        ["validation_positive_fraction", "validation_median_pf", "validation_pnl", "max_drawdown_pct"],
        ascending=[False, False, False, False], ignore_index=True,
    )
    board.to_csv(out / "tuning_leaderboard.csv", index=False)
    winner = board.iloc[0].to_dict()
    family = (float(winner["sl_atr"]), float(winner["tp_atr"]), int(winner["max_hold_bars"]))

    fresh_metrics = None
    decision = "NO_GO_PENDING_FRESH_DATA"
    if len(fresh) >= args.min_fresh_bars:
        fresh_metrics = _metrics(fresh, family)
        decision = "PASS_FOR_PAPER_ONLY" if (
            fresh_metrics["net_pnl"] > 0 and fresh_metrics["profit_factor"] > 1.05
            and winner["validation_positive_fraction"] >= 0.60
        ) else "NO_GO"

    result = {
        "dataset": {"rows": n, "sha256": sha256, "development_rows": len(development),
                    "validation_rows": len(validation), "fresh_rows": len(fresh),
                    "burned_cutoff": str(BURNED_CUTOFF)},
        "search": {"candidate_count": len(EXIT_FAMILIES), "candidates": [list(x) for x in EXIT_FAMILIES],
                   "winner": family, "selection": "validation stability first; P&L only after stability"},
        "fresh_holdout": fresh_metrics,
        "decision": decision,
    }
    (out / "TUNING_RESULT.json").write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    (out / "TUNING_RESULT.md").write_text(
        "# AMTE Controlled ATR Exit Tuning\n\n"
        f"Winner: **SL {family[0]:.1f} ATR / TP {family[1]:.1f} ATR / max hold {family[2]} bars**\n\n"
        f"Fresh bars after burned cutoff: **{len(fresh):,}**\n\n"
        + board.to_markdown(index=False) + "\n\n"
        + f"Decision: **{decision}**\n",
        encoding="utf-8",
    )
    print(f"Controlled exit tuning complete: winner={family}; decision={decision}; fresh_rows={len(fresh)}; sha256={sha256}")


if __name__ == "__main__":
    main()
