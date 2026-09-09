from __future__ import annotations

import argparse
import json

from app.analytics.performance import summarize_performance
from app.backtest.engine import BacktestConfig, run_backtest
from app.data.loader import load_ohlcv_csv
from app.signals.runner import generate_signals


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an AMTE intraday strategy backtest")
    parser.add_argument("csv", help="CSV containing timestamp, open, high, low, close, volume")
    parser.add_argument("--strategy", default="ema_trend", choices=["ema_trend", "momentum_breakout", "vwap_reversion", "orb_breakout"])
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--risk-per-trade", type=float, default=0.01)
    parser.add_argument("--stop-loss-pct", type=float, default=0.005)
    parser.add_argument("--take-profit-pct", type=float, default=0.01)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--fee-bps-per-side", type=float, default=0.0)
    args = parser.parse_args()

    bars = load_ohlcv_csv(args.csv)
    signals = generate_signals(bars, args.strategy)
    config = BacktestConfig(
        initial_capital=args.capital,
        risk_per_trade=args.risk_per_trade,
        stop_loss_pct=args.stop_loss_pct,
        take_profit_pct=args.take_profit_pct,
        slippage_bps=args.slippage_bps,
        fee_bps_per_side=args.fee_bps_per_side,
    )
    trades, equity = run_backtest(bars, signals, config)
    print(json.dumps(summarize_performance(trades, equity, args.capital), indent=2))
    if not trades.empty:
        print("\nTrades:")
        print(trades.to_string(index=False))


if __name__ == "__main__":
    main()
