import pandas as pd

from app.backtest.engine import BacktestConfig, run_backtest
from app.analytics.performance import summarize_performance


def test_long_trade_hits_take_profit():
    idx = pd.date_range("2026-01-01 09:15", periods=4, freq="5min")
    bars = pd.DataFrame(
        {
            "open": [100, 100, 100, 100],
            "high": [100, 101, 102, 102],
            "low": [99, 99.5, 99.8, 99.8],
            "close": [100, 100.5, 101, 101],
        }, index=idx
    )
    signals = pd.Series([1, 0, 0, 0], index=idx)
    trades, equity = run_backtest(
        bars, signals,
        BacktestConfig(initial_capital=100_000, risk_per_trade=0.01,
                       stop_loss_pct=0.005, take_profit_pct=0.01)
    )
    assert len(trades) == 1
    assert trades.iloc[0]["side"] == "LONG"
    assert trades.iloc[0]["exit_reason"] == "TAKE_PROFIT"
    assert trades.iloc[0]["net_pnl"] > 0
    summary = summarize_performance(trades, equity, 100_000)
    assert summary["trades"] == 1


def test_signal_executes_next_bar_open():
    idx = pd.date_range("2026-01-01 09:15", periods=3, freq="5min")
    bars = pd.DataFrame(
        {
            "open": [100, 110, 111],
            "high": [101, 111, 112],
            "low": [99, 109, 110],
            "close": [100, 110, 111],
        }, index=idx
    )
    signals = pd.Series([1, 0, 0], index=idx)
    trades, _ = run_backtest(bars, signals, BacktestConfig())
    assert len(trades) == 1
    assert trades.iloc[0]["entry_price"] == 110
