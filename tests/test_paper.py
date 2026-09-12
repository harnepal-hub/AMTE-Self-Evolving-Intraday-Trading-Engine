import pandas as pd
import pytest

from app.execution.paper import PaperBroker, PaperConfig


def test_long_uses_ask_on_entry_and_bid_on_exit():
    b = PaperBroker(PaperConfig(fee_bps_per_side=0, slippage_bps=0))
    ts = pd.Timestamp("2026-09-12T10:00:00Z")
    assert b.enter(ts, 1, 100.0, 100.1)
    assert b.position.entry_price == 100.1
    row = b.exit(ts + pd.Timedelta(seconds=1), 101.0, 101.1, "TEST")
    assert row is not None
    assert row["exit_price"] == 101.0
    assert row["net_pnl"] > 0


def test_daily_trade_cap_is_hard():
    b = PaperBroker(PaperConfig(fee_bps_per_side=0, slippage_bps=0, max_trades_per_day=2))
    ts = pd.Timestamp("2026-09-12T10:00:00Z")
    for i in range(2):
        assert b.enter(ts + pd.Timedelta(minutes=i * 2), 1, 100.0, 100.1)
        b.exit(ts + pd.Timedelta(minutes=i * 2, seconds=1), 100.0, 100.1, "TEST")
    assert not b.enter(ts + pd.Timedelta(minutes=5), 1, 100.0, 100.1)
    assert b.trades_today == 2


def test_daily_loss_lock_blocks_new_entries():
    b = PaperBroker(PaperConfig(fee_bps_per_side=0, slippage_bps=0, max_daily_loss_pct=0.01))
    ts = pd.Timestamp("2026-09-12T10:00:00Z")
    assert b.enter(ts, 1, 100.0, 100.1)
    row = b.exit(ts + pd.Timedelta(seconds=1), 90.0, 90.1, "LOSS")
    assert row is not None and row["net_pnl"] < 0
    assert b.locked
    assert not b.enter(ts + pd.Timedelta(minutes=1), 1, 100.0, 100.1)


def test_invalid_quote_rejected():
    b = PaperBroker()
    with pytest.raises(ValueError):
        b.enter(pd.Timestamp("2026-09-12", tz="UTC"), 1, 101, 100)
