"""Strategy runner used by research and reproducible backtests."""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from app.features.indicators import add_features
from app.signals.strategies import STRATEGIES, or_breakout

StrategyFn = Callable[[pd.Series], int]


def generate_signals(bars: pd.DataFrame, strategy: str) -> pd.Series:
    """Generate a causal {-1,0,1} signal series aligned to input bars."""
    if strategy == "orb_breakout":
        return or_breakout(bars)
    try:
        fn = STRATEGIES[strategy]
    except KeyError as exc:
        choices = sorted((*STRATEGIES, "orb_breakout"))
        raise ValueError(f"Unknown strategy {strategy!r}; choose from {choices}") from exc
    features = add_features(bars)
    signals = features.apply(fn, axis=1).astype(int)
    signals.name = strategy
    return signals
