from __future__ import annotations

import argparse
from pathlib import Path

from app.data.delta_exchange import fetch_historical_candles


def main() -> None:
    parser = argparse.ArgumentParser(description="Download public Delta Exchange historical candles")
    parser.add_argument("--symbol", default="BTCUSD")
    parser.add_argument("--resolution", default="5m")
    parser.add_argument("--start", required=True, help="ISO-8601 start timestamp")
    parser.add_argument("--end", required=True, help="ISO-8601 end timestamp")
    parser.add_argument("--output", default="data/raw/BTCUSD_5m.csv")
    args = parser.parse_args()

    frame = fetch_historical_candles(args.symbol, args.resolution, args.start, args.end)
    if frame.empty:
        raise SystemExit("No candles returned")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output, index_label="timestamp")
    print(f"Saved {len(frame):,} candles to {output}")
    print(f"Range: {frame.index.min()} -> {frame.index.max()}")


if __name__ == "__main__":
    main()
