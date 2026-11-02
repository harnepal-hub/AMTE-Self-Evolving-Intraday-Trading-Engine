"""Research stronger TW filters specifically for signals that pass the current AI gate.

This is research-only: it does not change the live paper engine. It compares the
current TW+AI rule with stricter causal trend alignment and ADX/DI confluence on
the same BTC 5m candles, fills, stops, targets, fees and slippage. The final
chronological slice is reported as a reference, not a pristine holdout: earlier
research has already examined this market/history.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from app.backtest.engine import BacktestConfig, run_backtest
from app.analytics.performance import summarize_performance
from app.signals.tw_all_in_one_filtered import filtered_signals
from scripts.run_signal_ablation import CFG, ai_proxy_series
from scripts.run_stage3_research import fetch_history


def trend_features(bars: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Causal EMA20/50 trend alignment and Wilder-style ADX/+DI/-DI."""
    high = bars["high"].astype(float)
    low = bars["low"].astype(float)
    close = bars["close"].astype(float)
    prev_close = close.shift(1)

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    alpha = 1 / 14
    atr = tr.ewm(alpha=alpha, adjust=False, min_periods=14).mean()
    plus_di = 100 * plus_dm.ewm(alpha=alpha, adjust=False, min_periods=14).mean() / atr.replace(0, float("nan"))
    minus_di = 100 * minus_dm.ewm(alpha=alpha, adjust=False, min_periods=14).mean() / atr.replace(0, float("nan"))
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float("nan"))
    adx = dx.ewm(alpha=alpha, adjust=False, min_periods=14).mean()

    ema20 = close.ewm(span=20, adjust=False, min_periods=20).mean()
    ema50 = close.ewm(span=50, adjust=False, min_periods=50).mean()
    long_aligned = (close > ema20) & (ema20 > ema50) & (plus_di > minus_di)
    short_aligned = (close < ema20) & (ema20 < ema50) & (minus_di > plus_di)
    return long_aligned, short_aligned, adx, (plus_di - minus_di)


def candidate_signals(bars: pd.DataFrame) -> dict[str, pd.Series]:
    tw = filtered_signals(bars, **CFG).astype(int)
    ai_side, _ = ai_proxy_series(bars)
    accepted = tw.where(tw.eq(ai_side), 0).astype(int)

    long_ok, short_ok, adx, di_spread = trend_features(bars)
    aligned = ((accepted.eq(1) & long_ok) | (accepted.eq(-1) & short_ok))
    signals = {"CURRENT_TW_PLUS_AI": accepted}
    signals["AI_TREND_ALIGNMENT"] = accepted.where(aligned, 0).astype(int)
    for threshold in (18.0, 22.0, 25.0):
        ok = aligned & adx.ge(threshold)
        signals[f"AI_TREND_ADX_{int(threshold)}"] = accepted.where(ok, 0).astype(int)
    return signals


def config() -> BacktestConfig:
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


def score(bars: pd.DataFrame, signals: pd.Series, cfg: BacktestConfig) -> dict:
    trades, equity = run_backtest(bars, signals, cfg)
    metrics = summarize_performance(trades, equity, cfg.initial_capital)
    pnl = trades["net_pnl"] if not trades.empty else pd.Series(dtype=float)
    metrics["signal_count"] = int(signals.ne(0).sum())
    metrics["mean_net_pnl_per_trade"] = round(float(pnl.mean()), 4) if not pnl.empty else None
    metrics["fees_total"] = round(float(trades["costs"].sum()), 4) if not trades.empty else 0.0
    return metrics


def main() -> None:
    out = Path("research_artifacts/ai_accepted_tw_filter")
    out.mkdir(parents=True, exist_ok=True)
    bars = fetch_history(180)
    bars.to_csv(out / "coindcx_btc_usdt_5m.csv", index_label="timestamp")
    digest = hashlib.sha256((out / "coindcx_btc_usdt_5m.csv").read_bytes()).hexdigest()

    all_signals = candidate_signals(bars)
    split = int(len(bars) * 0.8)
    periods = {
        "development": (bars.iloc[:split], slice(0, split)),
        "later_reference": (bars.iloc[split:], slice(split, len(bars))),
    }
    cfg = config()
    report = {
        "status": "completed",
        "research_only": True,
        "promotion": "NOT_ALLOWED_AUTOMATICALLY",
        "dataset": {
            "source": "CoinDCX public futures candles",
            "pair": "B-BTC_USDT",
            "timeframe": "5m",
            "rows": len(bars),
            "start": bars.index.min().isoformat(),
            "end": bars.index.max().isoformat(),
            "sha256": digest,
            "split": "first 80% development; final 20% later reference",
            "holdout_caveat": "Not a pristine holdout; earlier AMTE research has already examined this market/history.",
        },
        "features": "Causal EMA20/EMA50 alignment, directional +DI/-DI agreement, ADX thresholds 18/22/25",
        "ai_gate": "Current deterministic EMA/RSI/volume rule proxy; not a trained model or calibrated probability.",
        "assumptions": {
            "initial_capital_simulation_units": cfg.initial_capital,
            "risk_per_trade": cfg.risk_per_trade,
            "stop_loss_pct": cfg.stop_loss_pct,
            "take_profit_pct": cfg.take_profit_pct,
            "fee_bps_per_side": cfg.fee_bps_per_side,
            "slippage_bps_per_side": cfg.slippage_bps,
            "pnl_note": "BTC quote-currency P&L is in simulation units, not INR.",
            "live_engine_changed": False,
        },
        "results": {},
    }
    rows = []
    for period, (period_bars, idx) in periods.items():
        report["results"][period] = {}
        for name, full in all_signals.items():
            selected = full.iloc[idx].copy()
            metrics = score(period_bars, selected, cfg)
            report["results"][period][name] = metrics
            rows.append({"period": period, "variant": name, **metrics})
    pd.DataFrame(rows).to_csv(out / "comparison.csv", index=False)
    (out / "comparison.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")

    lines = [
        "# AI-accepted TW filter research",
        "",
        f"- Pair/timeframe: B-BTC_USDT, 5m; {len(bars):,} candles.",
        f"- Dataset: {bars.index.min().isoformat()} to {bars.index.max().isoformat()}",
        f"- SHA-256: `{digest}`",
        "- This is research only; the live paper engine configuration is unchanged.",
        "- P&L is in simulation units, not INR.",
        "- The later 20% is a reference period, not a pristine holdout.",
        "- ADX/DI and EMA alignment are causal and use only current/past candles.",
        "",
    ]
    for period in ("development", "later_reference"):
        lines += [f"## {period.replace('_', ' ').title()}", "", "| Variant | Trades | Win rate | Net P&L | Expectancy/trade | PF | Max DD |", "|---|---:|---:|---:|---:|---:|---:|"]
        for name, m in report["results"][period].items():
            def fmt(k, suffix=""):
                v = m.get(k)
                return "n/a" if v is None else f"{v}{suffix}"
            lines.append(
                f"| {name} | {m.get('trades', 0)} | {fmt('win_rate_pct', '%')} | "
                f"{fmt('net_pnl')} | {fmt('mean_net_pnl_per_trade')} | "
                f"{fmt('profit_factor')} | {fmt('max_drawdown_pct', '%')} |"
            )
        lines.append("")
    lines += [
        "## Promotion rule",
        "",
        "Do not promote based on win rate alone or development P&L. A challenger must improve net expectancy/profit factor after fees, retain a useful trade count, avoid unacceptable drawdown, and then pass a genuinely future paper-shadow evaluation. No automatic live promotion.",
        "",
    ]
    (out / "COMPARISON.md").write_text("\n".join(lines), encoding="utf-8")
    print("AI_ACCEPTED_TW_FILTER_RESULT=" + json.dumps(report, separators=(",", ":"), default=str))


if __name__ == "__main__":
    main()
