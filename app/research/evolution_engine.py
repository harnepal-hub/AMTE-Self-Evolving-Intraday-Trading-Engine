"""Research-only self-evolution engine for AMTE.

Creates TW/filter challengers, evaluates them chronologically, and never
changes the live trading configuration.
"""
from __future__ import annotations
from dataclasses import dataclass
from itertools import product
import pandas as pd
from app.backtest.engine import BacktestConfig, run_backtest
from app.signals.tw_all_in_one_filtered import filtered_signals

@dataclass(frozen=True)
class Candidate:
    name: str
    hull_length: int
    ema_length: int
    volume_ratio_min: float
    atr_expansion_min: float
    ema_distance_min: float
    cooldown_bars: int
    def as_dict(self) -> dict:
        return {
            "name": self.name, "hull_length": self.hull_length,
            "ema_length": self.ema_length, "volume_ratio_min": self.volume_ratio_min,
            "atr_expansion_min": self.atr_expansion_min,
            "ema_distance_min": self.ema_distance_min, "cooldown_bars": self.cooldown_bars,
        }

def candidate_space() -> list[Candidate]:
    rows = []
    for h, ema, vol, atr, dist, cd in product(
        (6, 8, 10, 12), (150, 200), (1.0, 1.2), (1.0, 1.1), (0.0, 0.003), (0, 3)
    ):
        rows.append(Candidate(f"TW-h{h}-e{ema}-v{vol:.1f}-a{atr:.1f}-d{dist:.3f}-c{cd}",
                              h, ema, vol, atr, dist, cd))
    return rows

def signals_for(bars: pd.DataFrame, c: Candidate) -> pd.Series:
    return filtered_signals(
        bars, hull_length=c.hull_length, ema_length=c.ema_length,
        ema_filter=True, slope_filter=True, volume_ratio_min=c.volume_ratio_min,
        atr_expansion_min=c.atr_expansion_min, ema_distance_min=c.ema_distance_min,
        rsi_filter=False, cooldown_bars=c.cooldown_bars)

def evaluate_candidate(bars: pd.DataFrame, candidate: Candidate, config: BacktestConfig) -> dict:
    signals = signals_for(bars, candidate)
    trades, equity = run_backtest(bars, signals, config)
    pnl = float(trades["net_pnl"].sum()) if not trades.empty else 0.0
    wins = int((trades["net_pnl"] > 0).sum()) if not trades.empty else 0
    losses = int((trades["net_pnl"] < 0).sum()) if not trades.empty else 0
    gp = float(trades.loc[trades["net_pnl"] > 0, "net_pnl"].sum()) if not trades.empty else 0.0
    gl = float(-trades.loc[trades["net_pnl"] < 0, "net_pnl"].sum()) if not trades.empty else 0.0
    pf = gp / gl if gl else (999.0 if wins else 0.0)
    dd = float(((equity["equity"] / equity["equity"].cummax()) - 1.0).min() * 100) if not equity.empty else 0.0
    return {**candidate.as_dict(), "trades": int(len(trades)), "signals": int((signals != 0).sum()),
            "wins": wins, "losses": losses, "win_rate_pct": round(100*wins/len(trades),2) if len(trades) else 0.0,
            "net_pnl": round(pnl,2), "profit_factor": round(pf,3),
            "max_drawdown_pct": round(dd,2), "expectancy": round(pnl/len(trades),2) if len(trades) else 0.0}

def rank(rows: pd.DataFrame, min_trades: int = 20) -> pd.DataFrame:
    out = rows.copy()
    out["eligible"] = out["trades"] >= min_trades
    out["score"] = (out["profit_factor"].clip(0,5)*30 + out["win_rate_pct"].clip(0,100)*0.10
                    + out["expectancy"].clip(-500,500)*0.05 + out["net_pnl"].clip(-5000,5000)*0.002
                    + out["max_drawdown_pct"].clip(-50,0)*0.20)
    out.loc[~out["eligible"], "score"] = -1e9
    return out.sort_values(["eligible","score","profit_factor","net_pnl"],
                           ascending=[False,False,False,False]).reset_index(drop=True)

def live_trade_feedback(trades: pd.DataFrame) -> dict:
    if trades.empty: return {"trades":0,"net_pnl":0.0,"win_rate_pct":0.0,"profit_factor":0.0}
    pnl = pd.to_numeric(trades["net_pnl"], errors="coerce").dropna()
    wins = pnl[pnl > 0].sum(); losses = -pnl[pnl < 0].sum()
    return {"trades":int(len(pnl)), "net_pnl":round(float(pnl.sum()),2),
            "win_rate_pct":round(float((pnl>0).mean()*100),2),
            "profit_factor":round(float(wins/losses),3) if losses else (999.0 if wins else 0.0),
            "avg_trade":round(float(pnl.mean()),2) if len(pnl) else 0.0}

def champion_config() -> Candidate:
    return Candidate("CHAMPION_CURRENT", 8, 200, 1.2, 1.1, 0.003, 3)

def custom_walk_forward(bars: pd.DataFrame, candidates: list[Candidate], config: BacktestConfig) -> pd.DataFrame:
    bpd = 24*60//5
    train = 30*bpd; test = 7*bpd; step = test
    rows = []
    for c in candidates:
        for n, start in enumerate(range(0, max(0, len(bars)-train-test+1), step), 1):
            test_bars = bars.iloc[start+train:start+train+test]
            m = evaluate_candidate(test_bars, c, config)
            m["window"] = n
            rows.append(m)
    return pd.DataFrame(rows)

def run_evolution(bars: pd.DataFrame, config: BacktestConfig, min_trades: int = 20):
    split = int(len(bars)*0.70)
    development = bars.iloc[:split].copy()
    holdout = bars.iloc[split:].copy()
    candidates = candidate_space()
    leaderboard = rank(pd.DataFrame([evaluate_candidate(development,c,config) for c in candidates]), min_trades)
    top = [c for c in candidates if c.name in set(leaderboard.head(8)["name"])]
    wf = custom_walk_forward(development, top, config)
    if not wf.empty:
        ws = wf.groupby("name",as_index=False).agg(
            wf_windows=("window","count"), wf_profitable_windows=("net_pnl",lambda s:int((s>0).sum())),
            wf_net_pnl=("net_pnl","sum"), wf_median_pnl=("net_pnl","median"))
        ws["wf_profitable_fraction"] = ws["wf_profitable_windows"]/ws["wf_windows"]
        leaderboard = leaderboard.merge(ws,on="name",how="left")
    else:
        leaderboard["wf_windows"]=0; leaderboard["wf_profitable_windows"]=0
        leaderboard["wf_net_pnl"]=0.0; leaderboard["wf_median_pnl"]=0.0; leaderboard["wf_profitable_fraction"]=0.0
    selected = set(leaderboard.head(3)["name"])
    holdout_table = pd.DataFrame([evaluate_candidate(holdout,c,config) for c in candidates if c.name in selected])
    champion = champion_config()
    champ_dev = evaluate_candidate(development,champion,config)
    champ_hold = evaluate_candidate(holdout,champion,config)
    challenger = leaderboard.iloc[0].to_dict() if not leaderboard.empty else {}
    ch_name = challenger.get("name")
    ch_hold = holdout_table.loc[holdout_table["name"]==ch_name].iloc[0].to_dict() if ch_name in set(holdout_table.get("name",[])) else {}
    robust = bool(challenger and ch_name != champion.name and
                  float(challenger.get("wf_profitable_fraction",0)) >= .55 and
                  float(challenger.get("profit_factor",0)) >= 1.20 and
                  float(challenger.get("max_drawdown_pct",0)) >= -15 and
                  float(ch_hold.get("profit_factor",0)) >= 1.15 and
                  float(ch_hold.get("net_pnl",0)) > 0 and
                  float(ch_hold.get("net_pnl",0)) >= float(champ_hold.get("net_pnl",0)))
    summary = {"decision":"PAPER_CHALLENGER" if robust else "NO_GO",
               "candidate_count":len(candidates),"top_challengers_tested":len(top),
               "champion":{"development":champ_dev,"holdout":champ_hold,**champion.as_dict()},
               "challenger":challenger,"challenger_holdout":ch_hold}
    return leaderboard, holdout_table, summary
