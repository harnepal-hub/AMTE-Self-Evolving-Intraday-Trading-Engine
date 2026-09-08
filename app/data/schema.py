"""Canonical market-data schema used across AMTE."""

from dataclasses import dataclass
from datetime import datetime

REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class OHLCVBar:
    """One completed market bar in normalized form."""

    timestamp: datetime
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float

    def validate(self) -> None:
        if self.high < max(self.open, self.close):
            raise ValueError("high must be >= open and close")
        if self.low > min(self.open, self.close):
            raise ValueError("low must be <= open and close")
        if self.volume < 0:
            raise ValueError("volume cannot be negative")
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("OHLC prices must be positive")
