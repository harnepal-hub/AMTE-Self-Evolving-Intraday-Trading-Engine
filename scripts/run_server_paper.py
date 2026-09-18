"""Server-side 5-minute multi-coin paper engine.

Runs on GitHub Actions, so the paper engine continues when the dashboard/browser
is closed. Uses public CoinDCX futures REST data only. No real exchange orders.
"""
from __future__ import annotations
import argparse, json, time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app.signals.tw_all_in_one_filtered import filtered_signals

ACTIVE = "https://api.coindcx.com/exchange/v1/derivatives/futures/data/active_instruments?margin_currency_short_name[]=USDT"
PRICES = "https://public.coindcx.com/market_data/v3/current_prices/futures/rt"
CANDLES = "https://public.coindcx.com/market_data/candlesticks"
BOOK = "https://public.coindcx.com/market_data/v3/orderbook/{pair}-futures/50"
IST = ZoneInfo("Asia/Kolkata")
CFG = {"hull_length":8,"ema_length":200,"ema_filter":True,"slope_filter":True,
       "volume_ratio_min":1.2,"atr_expansion_min":1.1,"ema_distance_min":0.003,
       "rsi_filter":False,"cooldown_bars":3}

def get_json(url, params=None, timeout=12):
    r=requests.get(url,params=params,timeout=timeout); r.raise_for_status(); return r.json()

def active_pairs(n):
    active={x for x in get_json(ACTIVE) if isinstance(x,str) and x.endswith("_USDT")}
    prices=get_json(PRICES)
    ranked=sorted(((p,float(v.get("v",0))) for p,v in prices.items()
                   if p in active and isinstance(v,dict)),key=lambda x:x[1],reverse=True)
    return [p for p,_ in ranked[:n]] if ranked else sorted(active)[:n]

def fetch_bars(pair,bars=260):
    now=int(time.time())
    j=get_json(CANDLES,{"pair":pair,"from":now-bars*300-900,"to":now,"resolution":5,"pcode":"f"})
    out=[]
    for x in j.get("data",[]):
        try: out.append({"time":int(x["time"]),"open":float(x["open"]),"high":float(x["high"]),
                         "low":float(x["low"]),"close":float(x["close"]),"volume":float(x["volume"])})
        except (KeyError,TypeError,ValueError): pass
    return sorted(out,key=lambda x:x["time"])[-bars:]

def frame(rows):
    return pd.DataFrame({"open":[x["open"] for x in rows],"high":[x["high"] for x in rows],
                         "low":[x["low"] for x in rows],"close":[x["close"] for x in rows],
                         "volume":[x["volume"] for x in rows]},
                        index=pd.to_datetime([x["time"] for x in rows],unit="ms",utc=True))

def book(pair):
    try:
        d=get_json(BOOK.format(pair=pair),timeout=5); bids=d.get("bids",{}); asks=d.get("asks",{})
        if not bids or not asks:return None
        bid=max(float(p) for p in bids); ask=min(float(p) for p in asks)
        return (bid,ask) if 0<bid<ask else None
    except Exception:return None

def ema(v,n):
    return float(pd.Series(v,dtype=float).ewm(span=n,adjust=False).mean().iloc[-1])

def rsi(v,n=14):
    s=pd.Series(v,dtype=float); d=s.diff(); up=d.clip(lower=0); dn=-d.clip(upper=0)
    au=up.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    ad=dn.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    z=100-100/(1+au/ad.replace(0,pd.NA))
    return float(z.iloc[-1]) if pd.notna(z.iloc[-1]) else 50.0

def ai_proxy(rows):
    if len(rows)<60:return "NO TRADE",50.0
    c=[x["close"] for x in rows]; v=[x["volume"] for x in rows]
    last,e20,e50=c[-1],ema(c,20),ema(c,50); rr=rsi(c); vr=v[-1]/(sum(v[-20:])/20 or 1)
    score=50+(12 if last>e20 else -12)+(12 if e20>e50 else -12)
    if 50<rr<70:score+=10
    if 30<rr<50:score-=10
    if vr>1.2:score+=8
    return ("LONG" if score>=62 else "SHORT" if score<=38 else "NO TRADE"),float(max(0,min(100,score)))

def default_state():
    return {"version":2,"day_ist":"","cash":100000.0,"realized_pnl":0.0,"trades_today":0,
            "locked":False,"position":None,"last_signal":{},"journal":[],"pairs":[],"events":0,
            "signals":0,"errors":0,"updated_at":None,"status":"STARTING"}

def load_state(path):
    if not path.exists():return default_state()
    try:
        s=default_state();s.update(json.loads(path.read_text()));return s
    except Exception:return default_state()

def save_state(path,s):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(s,indent=2,default=str))

def log(s,row):
    row["time"]=datetime.now(timezone.utc).isoformat();s["journal"].insert(0,row);s["journal"]=s["journal"][:500]

def roll_day(s):
    d=datetime.now(IST).date().isoformat()
    if s["day_ist"]!=d:
        s["day_ist"]=d;s["trades_today"]=0;s["locked"]=False;s["realized_pnl"]=0.0

def enter(s,pair,side,bid,ask,kind,score):
    if s["position"] or s["trades_today"]>=5 or s["locked"]:return False
    px0=ask if side==1 else bid; px=px0*(1+0.0002 if side==1 else 1-0.0002)
    risk=s["cash"]*0.0025; qty=risk/(px*0.005); fee=px*qty*0.0005
    if qty<=0 or s["cash"]<=fee:return False
    s["cash"]-=fee;s["trades_today"]+=1
    s["position"]={"pair":pair,"side":"LONG" if side==1 else "SHORT","entry_price":px,
                   "quantity":qty,"entry_fee":fee,"stop_price":px*(0.995 if side==1 else 1.005),
                   "target_price":px*(1.01 if side==1 else .99),
                   "opened_at":datetime.now(timezone.utc).isoformat(),"kind":kind,"ai_score":score}
    log(s,{"event":"ENTRY","pair":pair,"side":s["position"]["side"],"entry_price":px,
           "quantity":qty,"stop_price":s["position"]["stop_price"],"target_price":s["position"]["target_price"],
           "kind":kind,"ai_score":score})
    return True

def exit_position(s,bid,ask,reason):
    p=s["position"]
    if not p:return
    side=1 if p["side"]=="LONG" else -1; px0=bid if side==1 else ask
    px=px0*(1-0.0002 if side==1 else 1+0.0002)
    gross=(px-p["entry_price"])*p["quantity"]*side; fee=px*p["quantity"]*0.0005
    net=gross-p["entry_fee"]-fee;s["cash"]+=gross-fee;s["realized_pnl"]+=net
    log(s,{"event":"EXIT","pair":p["pair"],"side":p["side"],"entry_price":p["entry_price"],
           "exit_price":px,"quantity":p["quantity"],"gross_pnl":gross,"fees":p["entry_fee"]+fee,
           "net_pnl":net,"reason":reason});s["position"]=None
    if s["realized_pnl"]<=-2000:s["locked"]=True

def risk_check(s):
    p=s["position"]
    if not p:return
    q=book(p["pair"])
    if not q:return
    bid,ask=q
    if p["side"]=="LONG":
        if bid<=p["stop_price"]:exit_position(s,bid,ask,"STOP_LOSS")
        elif bid>=p["target_price"]:exit_position(s,bid,ask,"TAKE_PROFIT")
    else:
        if ask>=p["stop_price"]:exit_position(s,bid,ask,"STOP_LOSS")
        elif ask<=p["target_price"]:exit_position(s,bid,ask,"TAKE_PROFIT")

def process(s,pair):
    try:
        rows=fetch_bars(pair)
        if len(rows)<210:return
        bucket=(int(time.time())//300)*300000
        closed=[r for r in rows if r["time"]<bucket]
        if len(closed)<210:return
        s["events"]+=1
        for label,data in (("CLOSED",closed),("INTRABAR",rows[-250:])):
            sig=int(filtered_signals(frame(data),**CFG).iloc[-1])
            if not sig:continue
            key=f'{data[-1]["time"]}:{sig}:{label}'
            if s["last_signal"].get(pair)==key:continue
            s["last_signal"][pair]=key
            ai_side,score=ai_proxy(data);wanted="LONG" if sig==1 else "SHORT"
            if ai_side!=wanted or score<62:
                log(s,{"event":"SIGNAL_REJECTED","pair":pair,"side":wanted,"ai_side":ai_side,
                       "ai_score":score,"kind":label});continue
            s["signals"]+=1;q=book(pair)
            if not q:
                log(s,{"event":"SIGNAL_NO_BOOK","pair":pair,"side":wanted,"ai_score":score,"kind":label});continue
            bid,ask=q
            if s["position"]:
                if s["position"]["pair"]==pair and s["position"]["side"]!=wanted:exit_position(s,bid,ask,"TW_OPPOSITE")
                continue
            enter(s,pair,sig,bid,ask,label,score)
    except Exception as exc:
        s["errors"]+=1;log(s,{"event":"PAIR_ERROR","pair":pair,"error":str(exc)})

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--minutes",type=float,default=4.0)
    ap.add_argument("--max-pairs",type=int,default=30);ap.add_argument("--state",default="data/paper_live/state.json")
    ap.add_argument("--summary",default="data/paper_live/summary.json");a=ap.parse_args()
    sp,mp=Path(a.state),Path(a.summary);s=load_state(sp);roll_day(s)
    try:pairs=active_pairs(a.max_pairs)
    except Exception as exc:
        pairs=s.get("pairs",[])[:a.max_pairs];s["errors"]+=1;log(s,{"event":"ACTIVE_PAIRS_ERROR","error":str(exc)})
    s["pairs"]=pairs;end=time.monotonic()+a.minutes*60
    while time.monotonic()<end:
        for p in pairs:process(s,p)
        risk_check(s);s["updated_at"]=datetime.now(timezone.utc).isoformat();s["status"]="LIVE_PAPER"
        save_state(sp,s);time.sleep(20)
    summary={"mode":"SERVER_SIDE_MULTI_COIN_TW_AI_PAPER","status":s["status"],"updated_at":s["updated_at"],
             "day_ist":s["day_ist"],"capital":100000.0,"cash":s["cash"],"realized_pnl":s["realized_pnl"],
             "trades_today":s["trades_today"],"max_trades_per_day":5,"pairs":len(pairs),"pair_list":pairs,
             "events":s["events"],"signals":s["signals"],"errors":s["errors"],"position":s["position"],
             "browser_required":False,"real_orders":False,"config":CFG}
    save_state(sp,s);save_state(mp,summary);print(json.dumps(summary,indent=2),flush=True)

if __name__=="__main__":main()
