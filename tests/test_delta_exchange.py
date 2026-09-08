import pandas as pd

from app.data.delta_exchange import fetch_historical_candles


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params, headers, timeout):
        self.calls.append(params)
        return FakeResponse({
            "success": True,
            "result": [
                {"time": 1700000000, "open": 100, "high": 102, "low": 99, "close": 101, "volume": 10},
                {"time": 1700000300, "open": 101, "high": 103, "low": 100, "close": 102, "volume": 12},
            ],
        })


def test_delta_adapter_returns_canonical_ohlcv():
    session = FakeSession()
    frame = fetch_historical_candles(
        "BTCUSD", "5m", 1700000000, 1700000600, session=session
    )
    assert list(frame.columns) == ["open", "high", "low", "close", "volume"]
    assert isinstance(frame.index, pd.DatetimeIndex)
    assert str(frame.index.tz) == "UTC"
    assert len(frame) == 2


def test_delta_adapter_rejects_reversed_range():
    try:
        fetch_historical_candles("BTCUSD", "5m", 1700000600, 1700000000, session=FakeSession())
    except ValueError as exc:
        assert "earlier" in str(exc)
    else:
        raise AssertionError("Expected reversed range to fail")
