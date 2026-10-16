from datetime import datetime, timezone
import csv

import pytest

from scripts.run_server_paper import (
    append_csv,
    excursion_percentages,
    signal_age_seconds,
    stop_overshoot_pct,
    update_excursion,
)


def test_excursion_percentages_are_directional():
    assert excursion_percentages("LONG", 100.0, 103.0, 98.0) == pytest.approx((0.03, 0.02))
    assert excursion_percentages("SHORT", 100.0, 95.0, 102.0) == pytest.approx((0.05, 0.02))


def test_stop_overshoot_uses_adverse_quote_and_only_for_stop_loss():
    assert stop_overshoot_pct("LONG", 99.0, 98.01, 98.02, "STOP_LOSS") == pytest.approx(0.01)
    assert stop_overshoot_pct("SHORT", 101.0, 102.0, 102.01, "STOP_LOSS") == pytest.approx(0.01)
    assert stop_overshoot_pct("LONG", 99.0, 98.0, 98.1, "TAKE_PROFIT") == 0.0


def test_signal_age_measures_from_candle_close():
    now = datetime.fromtimestamp(360.0, timezone.utc)
    assert signal_age_seconds(0, now) == pytest.approx(60.0)
    assert signal_age_seconds(0, datetime.fromtimestamp(240.0, timezone.utc)) == 0.0


def test_short_excursion_uses_actual_prices_and_excludes_pre_entry_candles():
    state = {
        "position": {
            "side": "SHORT",
            "entry_price": 100.0,
            "opened_at": "1970-01-01T00:10:00+00:00",
            "mfe_price": 100.0,
            "mae_price": 100.0,
        }
    }
    rows = [
        {"time": 300000, "high": 130.0, "low": 70.0},  # before entry; ignore
        {"time": 600000, "high": 102.0, "low": 95.0},  # after entry
        {"time": 900000, "high": 104.0, "low": 93.0},  # after entry
    ]
    update_excursion(state, rows)
    assert state["position"]["mfe_price"] == 93.0
    assert state["position"]["mae_price"] == 104.0


def test_csv_schema_upgrade_preserves_old_rows(tmp_path):
    path = tmp_path / "journal.csv"
    path.write_text("trade_id,net_pnl\nold-trade,-12.5\n", encoding="utf-8")
    append_csv(path, ["trade_id", "net_pnl", "trigger_bid"], {
        "trade_id": "new-trade", "net_pnl": 4.0, "trigger_bid": 99.5
    })
    with path.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows == [
        {"trade_id": "old-trade", "net_pnl": "-12.5", "trigger_bid": ""},
        {"trade_id": "new-trade", "net_pnl": "4.0", "trigger_bid": "99.5"},
    ]
