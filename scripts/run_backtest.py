from __future__ import annotations

import argparse
import json

from app.analytics.performance import summarize_performance
from app.backtest.engine import BacktestConfig, run_backtest
from app.data.loader import load_ohlcv_csv


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an AMTE intraday backtest")
    parser.add_argument("csv", help="CSV containing timestamp, open, high, low, close, volume")
    args = parser.parse_args()

    bars = load_ohlcv_csv(args.csv)
    # Placeholder until the signal pipeline is connected to the runner.
    signals = bars["close"].diff().gt(0).astype(int)
    trades, equity = run_backtest(bars, signals, BacktestConfig())
    print(json.dumps(summarize_performance(trades, equity, 100_000.0), indent=2))
    if not trades.empty:
        print("\nTrades:")
        print(trades.to_string(index=False))


if __name__ == "__main__":
    main()
