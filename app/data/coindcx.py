"""CoinDCX public candle adapter normalized to AMTE OHLCV."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import requests

BASE_URL = "https://api.coindcx.com"
CANDLE_PATH = "/market_data/candles"
MAX_CANDLES_PER_REQUEST = 1000

_INTERVAL_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


def _epoch_ms(value: str | datetime | int | float) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def fetch_historical_candles(
    pair: str,
    start: str | datetime | int | float,
    end: str | datetime | int | float,
    interval: str = "5m",
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Fetch CoinDCX spot candles and return AMTE canonical OHLCV columns."""
    if interval not in _INTERVAL_MS:
        raise ValueError(f"Unsupported interval: {interval}")
    start_ms, end_ms = _epoch_ms(start), _epoch_ms(end)
    if start_ms >= end_ms:
        raise ValueError("start must be earlier than end")

    client = session or requests.Session()
    rows: list[dict] = []
    cursor = start_ms
    window = _INTERVAL_MS[interval] * (MAX_CANDLES_PER_REQUEST - 1)
    while cursor < end_ms:
        chunk_end = min(cursor + window, end_ms)
        response = client.get(
            f"{BASE_URL}{CANDLE_PATH}",
            params={"pair": pair, "interval": interval, "startTime": cursor, "endTime": chunk_end, "limit": MAX_CANDLES_PER_REQUEST},
            headers={"Accept": "application/json"},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError(f"CoinDCX candle request failed: {payload}")
        rows.extend(payload)
        if chunk_end >= end_ms:
            break
        cursor = chunk_end

    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"], index=pd.DatetimeIndex([], name="timestamp", tz="UTC"))

    frame = pd.DataFrame(rows)
    required = {"time", "open", "high", "low", "close", "volume"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"CoinDCX response missing fields: {sorted(missing)}")
    frame = frame.drop_duplicates(subset=["time"]).sort_values("time")
    frame["timestamp"] = pd.to_datetime(frame["time"], unit="ms", utc=True)
    return frame.set_index("timestamp")[["open", "high", "low", "close", "volume"]].astype(float)
