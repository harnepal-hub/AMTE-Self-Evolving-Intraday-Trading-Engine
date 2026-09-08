import pandas as pd
import pytest

from app.data.loader import load_ohlcv_csv
from app.data.session import assert_regular_frequency, filter_session


def _write_csv(tmp_path, rows):
    path = tmp_path / "bars.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_loader_normalizes_and_validates_csv(tmp_path):
    path = _write_csv(
        tmp_path,
        {
            "timestamp": ["2026-01-02 09:20", "2026-01-02 09:15"],
            "open": [101, 100], "high": [102, 101],
            "low": [100, 99], "close": [101.5, 100.5], "volume": [20, 10],
        },
    )
    out = load_ohlcv_csv(path, timezone="Asia/Kolkata")
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]
    assert out.index[0].strftime("%H:%M") == "09:15"
    assert str(out.index.tz) == "Asia/Kolkata"


def test_loader_rejects_duplicate_timestamps(tmp_path):
    path = _write_csv(
        tmp_path,
        {
            "timestamp": ["2026-01-02 09:15", "2026-01-02 09:15"],
            "open": [100, 100], "high": [101, 101],
            "low": [99, 99], "close": [100, 100], "volume": [10, 10],
        },
    )
    with pytest.raises(ValueError, match="Duplicate timestamps"):
        load_ohlcv_csv(path)


def test_nse_session_filter_excludes_pre_and_post_market():
    idx = pd.date_range("2026-01-02 09:00", periods=84, freq="5min")
    frame = pd.DataFrame({"close": range(len(idx))}, index=idx)
    out = filter_session(frame, "09:15", "15:30", "Asia/Kolkata")
    assert out.index[0].strftime("%H:%M") == "09:15"
    assert out.index[-1].strftime("%H:%M") == "15:30"


def test_regular_frequency_ignores_overnight_gap():
    idx = pd.DatetimeIndex([
        "2026-01-02 09:15", "2026-01-02 09:20", "2026-01-05 09:15"
    ]).tz_localize("Asia/Kolkata")
    frame = pd.DataFrame({"close": [1, 2, 3]}, index=idx)
    assert_regular_frequency(frame, "5min")


def test_regular_frequency_rejects_intraday_gap():
    idx = pd.DatetimeIndex([
        "2026-01-02 09:15", "2026-01-02 09:25"
    ]).tz_localize("Asia/Kolkata")
    frame = pd.DataFrame({"close": [1, 2]}, index=idx)
    with pytest.raises(ValueError, match="intraday gaps"):
        assert_regular_frequency(frame, "5min")
