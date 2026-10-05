"""Run AMTE research-only self-evolution."""
from __future__ import annotations
import argparse, json
from pathlib import Path
import pandas as pd
from app.research.evolution_engine import run_evolution, live_trade_feedback
from app.research.stage3 import realistic_config
from scripts.run_stage3_research import fetch_history

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--days",type=int,default=180)
    p.add_argument("--output-dir",default="research_artifacts/self_evolution")
    p.add_argument("--min-trades",type=int,default=20)
    a=p.parse_args()
    out=Path(a.output_dir); out.mkdir(parents=True,exist_ok=True)
    bars=fetch_history(a.days)
    cfg=realistic_config(initial_capital=100000,risk_per_trade=.001,stop_loss_pct=.005,
                        take_profit_pct=.01,fee_bps_per_side=5.0,slippage_bps=2.0)
    leaderboard,holdout,summary=run_evolution(bars,cfg,a.min_trades)
    lp=Path("data/paper_live/trade_journal.csv")
    live_feedback=live_trade_feedback(pd.read_csv(lp)) if lp.exists() else {"trades":0,"note":"journal unavailable"}
    report={"engine":"AMTE Self-Evolution v1","status":"research_only","real_orders":False,
            "dataset":{"pair":"B-BTC_USDT","interval":"5m","days":a.days,"rows":len(bars),
                       "start":bars.index.min().isoformat(),"end":bars.index.max().isoformat()},
            "live_paper_feedback":live_feedback,"summary":summary,
            "promotion_policy":{"auto_promotion":False,"requires":[
                "walk-forward profitable-window fraction >= 55%","profit factor >= 1.20",
                "max drawdown <= 15%","locked holdout positive","holdout profit factor >= 1.15",
                "challenger holdout >= champion holdout"]}}
    leaderboard.to_csv(out/"challenger_leaderboard.csv",index=False)
    holdout.to_csv(out/"locked_holdout.csv",index=False)
    (out/"EVOLUTION_REPORT.json").write_text(json.dumps(report,indent=2,default=str)+"\n",encoding="utf-8")
    s=summary
    lines=["# AMTE Self-Evolution v1","",
           "**Research-only. Live paper configuration was not modified. Real orders remain disabled.**","",
           f"- Candidates searched: {s['candidate_count']}",
           f"- Challengers walk-forward tested: {s['top_challengers_tested']}",
           f"- Decision: **{s['decision']}**","",
           "## Champion",
           f"- Development net P&L: ₹{s['champion']['development'].get('net_pnl',0):,.2f}",
           f"- Holdout net P&L: ₹{s['champion']['holdout'].get('net_pnl',0):,.2f}",
           f"- Holdout profit factor: {s['champion']['holdout'].get('profit_factor',0):.3f}","",
           "## Challenger",
           f"- Name: {s.get('challenger',{}).get('name','—')}",
           f"- Development score: {s.get('challenger',{}).get('score',0):.3f}",
           f"- Walk-forward profitable fraction: {s.get('challenger',{}).get('wf_profitable_fraction',0):.1%}",
           f"- Locked holdout net P&L: ₹{s.get('challenger_holdout',{}).get('net_pnl',0):,.2f}",
           f"- Locked holdout profit factor: {s.get('challenger_holdout',{}).get('profit_factor',0):.3f}","",
           "## Live paper feedback",
           f"- Completed paper trades: {live_feedback.get('trades',0)}",
           f"- Net P&L: ₹{live_feedback.get('net_pnl',0):,.2f}",
           f"- Win rate: {live_feedback.get('win_rate_pct',0):.2f}%",
           f"- Profit factor: {live_feedback.get('profit_factor',0):.3f}"]
    (out/"EVOLUTION_REPORT.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(f"Self-evolution complete: decision={s['decision']}")

if __name__=="__main__": main()
