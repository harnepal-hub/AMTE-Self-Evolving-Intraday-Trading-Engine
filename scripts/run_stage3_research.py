"""Fetch CoinDCX BTC 5m history and run the holdout-safe Stage 3 study."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from app.research.stage3 import CANDIDATES, evaluate_candidates, evaluate_directional, realistic_config

BASE_URL = "https://public.coindcx.com/market_data/candles"
PAIR = "B-BTC_USDT"
INTERVAL = "5m"
LIMIT = 1000


def fetch_history(days: int) -> pd.DataFrame:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    end_ms = int(end.timestamp() * 1000)
    start_ms = int(start.timestamp() * 1000)
    rows: list[dict] = []
    session = requests.Session()
    cursor = end_ms
    while cursor > start_ms:
        params = {"pair": PAIR, "interval": INTERVAL, "startTime": start_ms, "endTime": cursor, "limit": LIMIT}
        response = session.get(BASE_URL, params=params, timeout=30)
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break
        rows.extend(batch)
        oldest = min(int(item["time"]) for item in batch)
        next_cursor = oldest - 1
        if next_cursor >= cursor:
            break
        cursor = next_cursor
        if len(batch) < LIMIT:
            break
        time.sleep(0.05)

    frame = pd.DataFrame(rows)
    if frame.empty:
        raise RuntimeError("CoinDCX returned no candles")
    frame["time"] = pd.to_datetime(frame["time"], unit="ms", utc=True)
    frame = frame.rename(columns={"time": "timestamp"})
    frame = frame.set_index("timestamp")["open high low close volume".split()]
    frame = frame.apply(pd.to_numeric, errors="raise").sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    frame = frame.loc[(frame.index >= pd.Timestamp(start)) & (frame.index <= pd.Timestamp(end))]
    if len(frame) < 20_000:
        raise RuntimeError(f"Historical sample too small: {len(frame):,} rows")
    return frame


def fmt(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--output-dir", default="research_artifacts/stage3")
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

    development_leaderboard = evaluate_candidates(development, config)
    holdout_leaderboard = evaluate_candidates(holdout, config)
    directional_rows: list[pd.DataFrame] = []
    for strategy in CANDIDATES:
        directional = evaluate_directional(development, config, strategy)
        directional_rows.append(directional)
    directional_table = pd.concat(directional_rows, ignore_index=True)

    development_leaderboard.to_csv(out / "development_leaderboard.csv", index=False)
    directional_table.to_csv(out / "development_directional.csv", index=False)
    holdout_leaderboard.to_csv(out / "holdout_reference_only.csv", index=False)

    winner = development_leaderboard.iloc[0]["strategy"]
    report = {
        "stage": "Stage 3 strategy research",
        "status": "completed",
        "dataset": {
            "source": "CoinDCX public candles API",
            "pair": PAIR,
            "interval": INTERVAL,
            "rows": len(bars),
            "start": bars.index.min().isoformat(),
            "end": bars.index.max().isoformat(),
            "sha256": dataset_sha256,
            "development_rows": len(development),
            "future_holdout_rows": len(holdout),
            "development_fraction": 0.80,
        },
        "assumptions": {
            "initial_capital": config.initial_capital,
            "risk_per_trade": config.risk_per_trade,
            "stop_loss_pct": config.stop_loss_pct,
            "take_profit_pct": config.take_profit_pct,
            "fee_bps_per_side": config.fee_bps_per_side,
            "slippage_bps": config.slippage_bps,
        },
        "candidates": list(CANDIDATES),
        "selection_rule": "Highest development-sample combined net P&L; holdout is not used for selection.",
        "development_winner": winner,
        "development_leaderboard": development_leaderboard.to_dict(orient="records"),
        "development_directional": directional_table.to_dict(orient="records"),
        "holdout_reference_only": holdout_leaderboard.to_dict(orient="records"),
    }
    (out / "stage3_report.json").write_text(json.dumps(report, indent=2, default=fmt) + "\n", encoding="utf-8")

    lines = [
        "# AMTE Stage 3 Strategy Research",
        "",
        "## Dataset",
        f"- Source: CoinDCX public candles API ({PAIR}, {INTERVAL})",
        f"- Rows: {len(bars):,}",
        f"- Period: {bars.index.min().isoformat()} to {bars.index.max().isoformat()}",
        f"- Development: first 80% ({len(development):,} rows)",
        f"- Future holdout: final 20% ({len(holdout):,} rows), not used for selection",
        f"- SHA-256: `{dataset_sha256}`",
        "",
        "## Common assumptions",
        f"- Capital: {config.initial_capital:,.0f}",
        f"- Risk/trade: {config.risk_per_trade:.2%}",
        f"- Stop/target: {config.stop_loss_pct:.2%} / {config.take_profit_pct:.2%}",
        f"- Fee: {config.fee_bps_per_side:.1f} bps/side",
        f"- Slippage: {config.slippage_bps:.1f} bps/side",
        "",
        "## Development leaderboard",
        development_leaderboard.to_markdown(index=False),
        "",
        "## Development long/short/combined",
        directional_table.to_markdown(index=False),
        "",
        f"## Candidate selected without holdout: **{winner}**",
        "Selection uses only development combined net P&L, with no future-holdout information.",
        "The holdout table is retained only as a locked reference for later validation.",
        "",
        "## Reproducibility",
        "Run `python scripts/run_stage3_research.py --days 365` after installing requirements.",
    ]
    (out / "STAGE3_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Stage 3 complete: {winner}; rows={len(bars):,}; sha256={dataset_sha256}")


if __name__ == "__main__":
    main()
