import pandas as pd
import pytest

from app.data.coindcx import fetch_historical_candles


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.payload)


def test_coindcx_candles_normalize_descending_payload():
    session = FakeSession([
        {"time": 1760000300000, "open": "101", "high": "102", "low": "100", "close": "101.5", "volume": "12"},
        {"time": 1760000000000, "open": "100", "high": "101", "low": "99", "close": "100.5", "volume": "10"},
    ])
    frame = fetch_historical_candles("B-BTC_USDT", "5m", 1760000000000, 1760000600000, session)
    assert list(frame.columns) == ["open", "high", "low", "close", "volume"]
    assert frame.index.is_monotonic_increasing
    assert frame.iloc[0]["close"] == 100.5
    assert session.calls[0][1]["params"]["pair"] == "B-BTC_USDT"


def test_coindcx_rejects_invalid_interval():
    with pytest.raises(ValueError, match="Unsupported interval"):
        fetch_historical_candles("B-BTC_USDT", "2m", 1, 2, FakeSession([]))


def test_coindcx_rejects_invalid_range():
    with pytest.raises(ValueError, match="start must be earlier"):
        fetch_historical_candles("B-BTC_USDT", "5m", 2, 1, FakeSession([]))
