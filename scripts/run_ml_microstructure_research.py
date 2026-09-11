"""Research-only walk-forward ML experiment with a hard five-trade daily cap.

This is not live trading. It uses candle/volume microstructure proxies until
historical order-book/trade-tick data is available.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np
import pandas as pd
from app.backtest.engine import BacktestConfig, run_backtest
from app.research.microstructure import walk_forward_probabilities


def cap_daily(signals: pd.Series, max_trades: int = 5) -> pd.Series:
    out = signals.copy().astype(int)
    counts = {}
    for i, (ts, value) in enumerate(out.items()):
        if value == 0: continue
        day = ts.date(); counts.setdefault(day, 0)
        if counts[day] >= max_trades: out.iloc[i] = 0
        else: counts[day] += 1
    return out


def metrics(trades: pd.DataFrame, equity: pd.DataFrame, initial: float = 100_000.0) -> dict:
    if trades.empty: return {"trades":0,"net_pnl":0.0,"pf":0.0,"win_rate":0.0,"max_dd_pct":0.0,"profitable_days_pct":0.0,"avg_daily_pnl":0.0,"median_daily_pnl":0.0,"max_trades_day":0}
    wins=trades.loc[trades.net_pnl>0,"net_pnl"].sum(); losses=-trades.loc[trades.net_pnl<0,"net_pnl"].sum(); pf=float(wins/losses) if losses>0 else float("inf")
    daily=trades.assign(day=pd.to_datetime(trades.entry_time).dt.date).groupby("day").net_pnl.sum()
    dd=(equity.equity/equity.equity.cummax()-1).min()*100
    tpd=trades.assign(day=pd.to_datetime(trades.entry_time).dt.date).groupby("day").size()
    return {"trades":len(trades),"net_pnl":float(trades.net_pnl.sum()),"pf":pf,"win_rate":float((trades.net_pnl>0).mean()*100),"max_dd_pct":float(dd),"profitable_days_pct":float((daily>0).mean()*100),"avg_daily_pnl":float(daily.mean()),"median_daily_pnl":float(daily.median()),"max_trades_day":int(tpd.max())}


def main() -> None:
    ap=argparse.ArgumentParser(); ap.add_argument("--data",required=True); ap.add_argument("--out",default="artifacts/ml_microstructure"); args=ap.parse_args()
    bars=pd.read_csv(args.data,index_col=0,parse_dates=True).sort_index()
    split=int(len(bars)*.8); dev=bars.iloc[:split]; hold=bars.iloc[split:]
    rows=[]
    for horizon in (1,3,6):
        for move in (3.0,4.0,6.0):
            for lo,hi in ((.60,.40),(.62,.38),(.65,.35)):
                for frame,name in ((dev,"development"),(hold,"holdout_reference")):
                    probs=walk_forward_probabilities(frame,horizon_bars=horizon,min_move_bps=move,long_threshold=hi,short_threshold=lo)
                    sig=cap_daily(probs.ml_signal,5)
                    cfg=BacktestConfig(initial_capital=100_000,risk_per_trade=.0025,stop_loss_pct=.005,take_profit_pct=.01,fee_bps_per_side=5,slippage_bps=2,square_off_at_session_end=False)
                    trades,equity=run_backtest(frame,sig,cfg); m=metrics(trades,equity); m.update({"period":name,"horizon":horizon,"min_move_bps":move,"long_threshold":hi,"short_threshold":lo}); rows.append(m)
    out=Path(args.out); out.mkdir(parents=True,exist_ok=True); pd.DataFrame(rows).to_csv(out/"leaderboard.csv",index=False)
    print(pd.DataFrame(rows).sort_values(["period","pf","net_pnl"],ascending=[True,False,False]).head(20).to_string(index=False))

if __name__=="__main__": main()
