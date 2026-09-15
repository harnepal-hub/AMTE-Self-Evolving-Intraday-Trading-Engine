"""Causal Python adaptation of the supplied TW All in One Pine signal.

The source uses EHMA(16), SHULL=HULL[2], and crossunder/crossover signals.
This module adds optional EMA trend filtering and slope confirmation for
research/paper-trading experiments; defaults reproduce the signal logic.
"""
from __future__ import annotations

from collections import deque


def _ema(values: list[float], length: int) -> float:
    if len(values) < length:
        return float("nan")
    alpha = 2.0 / (length + 1.0)
    value = float(values[0])
    for x in values[1:]:
        value = alpha * float(x) + (1 - alpha) * value
    return value


def ehma(values: list[float], length: int) -> float:
    """EHMA equivalent: EMA(2*EMA(src,n/2)-EMA(src,n), sqrt(n))."""
    half = max(1, int(length / 2))
    root = max(1, round(length ** 0.5))
    if len(values) < length:
        return float("nan")
    # Build the synthetic series causally, then smooth it with EMA(root).
    fast = []
    slow = []
    synthetic = []
    for i in range(len(values)):
        a = _ema(values[: i + 1], half)
        b = _ema(values[: i + 1], length)
        if a == a and b == b:
            synthetic.append(2 * a - b)
            fast.append(a); slow.append(b)
    if len(synthetic) < root:
        return float("nan")
    return _ema(synthetic, root)


class TWAllInOneSignal:
    def __init__(self, length: int = 16, ema_length: int = 100,
                 ema_filter: bool = False, slope_filter: bool = False):
        if length < 2 or ema_length < 2:
            raise ValueError("lengths must be >= 2")
        self.length = length
        self.ema_length = ema_length
        self.ema_filter = ema_filter
        self.slope_filter = slope_filter
        self.closes: deque[float] = deque(maxlen=max(ema_length + 20, length * 8))
        self.hulls: deque[float] = deque(maxlen=8)
        self.ema_values: deque[float] = deque(maxlen=3)

    def update(self, close: float) -> int:
        self.closes.append(float(close))
        vals = list(self.closes)
        hull = ehma(vals, self.length)
        if hull != hull:
            return 0
        self.hulls.append(hull)
        ema = _ema(vals, self.ema_length)
        if ema == ema:
            self.ema_values.append(ema)
        if len(self.hulls) < 3:
            return 0
        mhull = self.hulls[-1]
        shull = self.hulls[-3]
        prev_mhull = self.hulls[-2]
        prev_shull = self.hulls[-4] if len(self.hulls) >= 4 else None
        # Pine: buy = crossunder(SHULL,MHULL), sell = crossover(SHULL,MHULL).
        buy = prev_shull is not None and prev_shull >= prev_mhull and shull < mhull
        sell = prev_shull is not None and prev_shull <= prev_mhull and shull > mhull
        if self.ema_filter and ema == ema:
            if buy and close <= ema: buy = False
            if sell and close >= ema: sell = False
        if self.slope_filter and len(self.hulls) >= 3:
            rising = self.hulls[-1] > self.hulls[-2]
            falling = self.hulls[-1] < self.hulls[-2]
            buy = buy and rising
            sell = sell and falling
        return 1 if buy else -1 if sell else 0
