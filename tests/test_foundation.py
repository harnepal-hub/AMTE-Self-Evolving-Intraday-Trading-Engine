import pandas as pd

from app.features.technical import add_basic_features
from app.risk.sizing import risk_position_size
from app.signals.baseline import generate_signal


def test_features_are_created_without_future_shift():
    df = pd.DataFrame(
        {
            "open": [100.0] * 55,
            "high": [101.0] * 55,
            "low": [99.0] * 55,
            "close": [100.0] * 55,
            "volume": [1000.0] * 55,
        }
    )
    out = add_basic_features(df)
    assert "sma_20" in out
    assert "sma_50" in out
    assert pd.isna(out.loc[0, "sma_20"])
    assert not pd.isna(out.loc[49, "sma_50"])


def test_baseline_signal_is_neutral_until_features_exist():
    row = pd.Series({"close": 100.0, "sma_20": float("nan"), "sma_50": 99.0})
    assert generate_signal(row) == 0


def test_risk_sizing():
    assert risk_position_size(100000, 0.5, 100, 95) == 100
