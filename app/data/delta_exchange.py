"""Public historical-candle adapter for Delta Exchange India."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import requests

BASE_URL = "https://api.india.delta.exchange"
CANDLE_PATH = "/v2/history/candles"
MAX_CANDLES_PER_REQUEST = 2000


def _unix_seconds(value: str | datetime | int | float) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def fetch_historical_candles(
    symbol: str,
    resolution: str = "5m",
    start: str | datetime | int | float = "2026-01-01T00:00:00+00:00",
    end: str | datetime | int | float | None = None,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Fetch historical OHLCV candles and return AMTE canonical columns.

    Delta Exchange limits one historical-candle response to 2000 candles, so
    longer ranges are automatically split into non-overlapping time windows.
    The public endpoint does not require API credentials.
    """
    start_ts = _unix_seconds(start)
    end_ts = _unix_seconds(end) if end is not None else int(datetime.now(timezone.utc).timestamp())
    if start_ts >= end_ts:
        raise ValueError("start must be earlier than end")

    # Conservative window sizes.  The API accepts 5m candles; keeping a
    # margin below 2000 avoids boundary ambiguity between adjacent requests.
    seconds_per_bar = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
                       "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600,
                       "1d": 86400, "1w": 604800}.get(resolution)
    if seconds_per_bar is None:
        raise ValueError(f"Unsupported resolution: {resolution}")
    window = seconds_per_bar * (MAX_CANDLES_PER_REQUEST - 1)

    client = session or requests.Session()
    rows: list[dict] = []
    cursor = start_ts
    while cursor < end_ts:
        chunk_end = min(cursor + window, end_ts)
        response = client.get(
            f"{BASE_URL}{CANDLE_PATH}",
            params={"resolution": resolution, "symbol": symbol, "start": cursor, "end": chunk_end},
            headers={"Accept": "application/json"},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("success", False):
            raise RuntimeError(f"Delta historical-candle request failed: {payload}")
        rows.extend(payload.get("result", []))
        if chunk_end >= end_ts:
            break
        cursor = chunk_end

    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"],
                            index=pd.DatetimeIndex([], name="timestamp", tz="UTC"))

    frame = pd.DataFrame(rows)
    required = ["time", "open", "high", "low", "close", "volume"]
    missing = set(required) - set(frame.columns)
    if missing:
        raise ValueError(f"Delta response missing fields: {sorted(missing)}")
    frame = frame.drop_duplicates(subset=["time"]).sort_values("time")
    frame["timestamp"] = pd.to_datetime(frame["time"], unit="s", utc=True)
    frame = frame.set_index("timestamp")[["open", "high", "low", "close", "volume"]]
    return frame.astype(float)
