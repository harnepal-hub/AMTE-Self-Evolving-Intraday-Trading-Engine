"""Paired ablation: filtered TW signals alone versus TW plus the live AI-rule proxy.

Uses identical candles, risk, exits, fees and slippage for both variants. The AI
proxy is deterministic rule logic, not a trained model or calibrated probability.
No parameter search is performed; the final chronological 20% is reported as a
holdout and is never used to choose settings.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from app.analytics.performance import summarize_performance
from app.backtest.engine import BacktestConfig, run_backtest
from app.signals.tw_all_in_one_filtered import filtered_signals
from scripts.run_stage3_research import fetch_history

CFG = {
    "hull_length": 8,
    "ema_length": 200,
    "ema_filter": True,
    "slope_filter": True,
    "volume_ratio_min": 1.2,
    "atr_expansion_min": 1.1,
    "ema_distance_min": 0.003,
    "rsi_filter": False,
    "cooldown_bars": 3,
}


def ai_proxy_series(bars: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Causal vectorized replay of the current deterministic ai_proxy rules."""
    close = bars["close"].astype(float)
    volume = bars["volume"].astype(float)
    ema20 = close.ewm(span=20, adjust=False, min_periods=20).mean()
    ema50 = close.ewm(span=50, adjust=False, min_periods=50).mean()
    delta = close.diff()
    up = delta.clip(lower=0.0)
    down = -delta.clip(upper=0.0)
    avg_up = up.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    avg_down = down.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rsi = 100 - 100 / (1 + avg_up / avg_down.replace(0, float("nan")))
    rsi = rsi.fillna(50.0)
    vol_mean = volume.rolling(20, min_periods=20).mean()
    volume_ratio = volume / vol_mean.replace(0, float("nan"))

    score = (
        50
        + close.gt(ema20).map({True: 12, False: -12})
        + ema20.gt(ema50).map({True: 12, False: -12})
        + ((rsi.gt(50) & rsi.lt(70)).astype(int) * 10)
        - ((rsi.gt(30) & rsi.lt(50)).astype(int) * 10)
        + volume_ratio.gt(1.2).fillna(False).astype(int) * 8
    ).clip(0, 100)
    side = pd.Series(0, index=bars.index, dtype=int)
    side.loc[score >= 62] = 1
    side.loc[score <= 38] = -1
    return side, score.astype(float)


def research_config() -> BacktestConfig:
    # Matches the live paper engine's risk/stop/target/cost assumptions.
    # Fractional quantity precision is used because futures contracts are fractional.
    return BacktestConfig(
        initial_capital=100_000.0,
        risk_per_trade=0.001,
        stop_loss_pct=0.005,
        take_profit_pct=0.01,
        fee_bps_per_side=5.0,
        slippage_bps=2.0,
        quantity_step=1e-9,
        lot_size=1e-9,
        square_off_at_session_end=False,
        max_daily_loss_pct=0.02,
    )


def evaluate(bars: pd.DataFrame, signals: pd.Series, config: BacktestConfig) -> dict:
    trades, equity = run_backtest(bars, signals, config)
    metrics = summarize_performance(trades, equity, config.initial_capital)
    pnl = trades["net_pnl"] if not trades.empty else pd.Series(dtype=float)
    metrics["signal_count"] = int(signals.ne(0).sum())
    metrics["fees_total"] = round(float(trades["costs"].sum()), 2) if not trades.empty else 0.0
    metrics["fees_share_of_abs_gross_pnl_pct"] = (
        round(100 * metrics["fees_total"] / float(trades["gross_pnl"].abs().sum()), 2)
        if not trades.empty and float(trades["gross_pnl"].abs().sum()) > 0 else None
    )
    metrics["mean_net_pnl_per_trade"] = round(float(pnl.mean()), 2) if not pnl.empty else None
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--output-dir", default="research_artifacts/signal_ablation")
    args = parser.parse_args()

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    bars = fetch_history(args.days)
    dataset_path = out / "coindcx_btc_usdt_5m.csv"
    bars.to_csv(dataset_path, index_label="timestamp")
    digest = hashlib.sha256(dataset_path.read_bytes()).hexdigest()

    # Generate signals on the complete chronological dataset so the holdout's
    # indicators may use only prior candles, not lose their warm-up history.
    tw = filtered_signals(bars, **CFG).astype(int)
    ai_side, ai_score = ai_proxy_series(bars)
    combined = tw.where(tw.eq(ai_side), 0).astype(int)

    split = int(len(bars) * 0.80)
    slices = {
        "development": (bars.iloc[:split], slice(0, split)),
        "holdout": (bars.iloc[split:], slice(split, len(bars))),
    }
    config = research_config()
    report = {
        "status": "completed",
        "purpose": "paired signal ablation; no tuning or promotion",
        "dataset": {
            "source": "CoinDCX public futures candlesticks API",
            "pair": "B-BTC_USDT",
            "interval": "5m",
            "rows": len(bars),
            "start": bars.index.min().isoformat(),
            "end": bars.index.max().isoformat(),
            "sha256": digest,
            "development_rows": split,
            "holdout_rows": len(bars) - split,
        },
        "shared_assumptions": {
            "initial_capital": config.initial_capital,
            "pnl_currency_note": "P&L values are simulation units, not INR: BTC_USDT quote-currency results are not converted through USD/INR. Compare returns, win rate, profit factor and relative differences; do not interpret absolute amounts as rupees.",
            "risk_per_trade": config.risk_per_trade,
            "stop_loss_pct": config.stop_loss_pct,
            "take_profit_pct": config.take_profit_pct,
            "fee_bps_per_side": config.fee_bps_per_side,
            "slippage_bps_per_side": config.slippage_bps,
            "max_daily_loss_pct": config.max_daily_loss_pct,
            "single_position": True,
            "session_squareoff": False,
            "daily_trade_cap": "not modeled in this single-instrument historical replay; both variants use identical execution rules",
            "tw_filter_config": CFG,
            "ai_proxy": "EMA20/EMA50 + RSI + volume ratio deterministic rules; score is not a calibrated probability",
        },
        "overall_signal_counts": {
            "tw_filtered": int(tw.ne(0).sum()),
            "tw_and_ai_agree": int(combined.ne(0).sum()),
            "ai_agreement_rate_of_tw_signals_pct": round(100 * combined.ne(0).sum() / max(1, tw.ne(0).sum()), 2),
            "tw_long": int(tw.eq(1).sum()),
            "tw_short": int(tw.eq(-1).sum()),
            "combined_long": int(combined.eq(1).sum()),
            "combined_short": int(combined.eq(-1).sum()),
        },
        "results": {},
    }

    rows = []
    for period, (period_bars, idx) in slices.items():
        for variant, full_signals in (("TW_FILTERED_ONLY", tw), ("TW_FILTERED_PLUS_AI", combined)):
            selected = full_signals.iloc[idx].copy()
            metrics = evaluate(period_bars, selected, config)
            report["results"].setdefault(period, {})[variant] = metrics
            rows.append({"period": period, "variant": variant, **metrics})

    pd.DataFrame(rows).to_csv(out / "comparison.csv", index=False)
    (out / "comparison.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")

    lines = [
        "# TW-only vs TW + AI-rule proxy",
        "",
        f"- Dataset: B-BTC_USDT 5-minute, {len(bars):,} bars, {bars.index.min().isoformat()} to {bars.index.max().isoformat()}",
        f"- SHA-256: `{digest}`",
        "- Both variants use identical entries (next bar open), stop/target, sizing, fee, slippage and daily loss limit.",
        "- The AI component is deterministic rules, not a trained model or calibrated win probability.",
        "- Settings are fixed in advance; the final 20% is reported separately as holdout.",
        "- This is a single-instrument ablation; the live multi-coin 10-trades/day cap is not modeled.",
        "- P&L/fees are simulation units, not INR: BTC_USDT results are not converted through USD/INR. Compare percentage return and relative metrics, not absolute amounts as rupees.",
        "",
    ]
    for period in ("development", "holdout"):
        lines += [f"## {period.title()}", "", "| Variant | Trades | Win rate | Net P&L (units) | Expectancy/trade (units) | Profit factor | Max DD | Fees (units) |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for variant in ("TW_FILTERED_ONLY", "TW_FILTERED_PLUS_AI"):
            m = report["results"][period][variant]
            def val(key, suffix=""):
                x = m.get(key)
                return "n/a" if x is None else f"{x}{suffix}"
            lines.append(
                f"| {variant} | {m.get('trades', 0)} | {val('win_rate_pct', '%')} | {m.get('net_pnl', 0):,.2f} | {val('expectancy_per_trade', ' units')} | {val('profit_factor')} | {val('max_drawdown_pct', '%')} | {m.get('fees_total', 0):,.2f} |"
            )
        lines.append("")
    lines += [
        "## Interpretation rule",
        "",
        "Do not choose a variant from development results alone. The AI gate is useful only if it improves net expectancy/profit factor and drawdown on the untouched holdout without making trade count impractically small. No variant is promoted automatically; paper trading remains separate.",
        "",
    ]
    (out / "COMPARISON.md").write_text("\n".join(lines), encoding="utf-8")
    print("SIGNAL_ABLATION_RESULT=" + json.dumps(report, separators=(",", ":"), default=str))


if __name__ == "__main__":
    main()
