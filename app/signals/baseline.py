"""Minimal deterministic baseline signal for V1 validation."""

import pandas as pd


def generate_signal(row: pd.Series) -> int:
    """Return 1 for long, -1 for short, 0 for no trade.

    This baseline is intentionally simple. It is a benchmark, not the final
    AMTE strategy. It only acts when the moving-average features are available.
    """
    if pd.isna(row.get("sma_20")) or pd.isna(row.get("sma_50")):
        return 0
    if row["close"] > row["sma_20"] > row["sma_50"]:
        return 1
    if row["close"] < row["sma_20"] < row["sma_50"]:
        return -1
    return 0
