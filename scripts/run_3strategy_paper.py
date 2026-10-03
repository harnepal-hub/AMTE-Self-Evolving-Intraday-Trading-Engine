"""AMTE independent 3-strategy server-side paper engine.
# AF diagnostics added; entry logic remains unchanged until validation.
Turtle Soup + AF StochZ + Deviation Trend Profile.
Public CoinDCX futures data only. No real exchange orders.
"""
from __future__ import annotations
import argparse, csv, json, math, time, uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

ROOT=Path(__file__).resolve().parents[1]
ACTIVE="https://api.coindcx.com/exchange/v1/derivatives/futures/data/active_instruments?margin_currency_short_name[]=USDT"
PRICES="https://public.coindcx.com/market_data/v3/current_prices/futures/rt"
CANDLES="https://public.coindcx.com/market_data/candlesticks"
BOOK="https://public.coindcx.com/market_data/v3/orderbook/{pair}-futures/50"
IST=ZoneInfo("Asia/Kolkata")
DATA=ROOT/"data"/"paper_3strategy"
STATE=DATA/"state.json"; MARKET=DATA/"market.json"
TRADES=DATA/"trade_journal.csv"; SIGNALS=DATA/"signal_journal.csv"
RISK=.001; SL=.005; TP=.01; FEE=5/10000; SLIP=2/10000; MAX_TRADES=10; DAILY_LOCK=2000.0
TURTLE=20; DTP_SMA=50; DTP_ATR=200; AF_MIN=8; AF_MAX=34; AF_ER=10; AF_Z=20

HTTP=requests.Session(); HTTP.headers.update({"User-Agent":"AMTE-3Strategy-Paper/1.0","Accept":"application/json"})
TRADE_FIELDS=["trade_id","signal_id","pair","side","entry_time","exit_time","entry_price","exit_price","stop_price","target_price","quantity","entry_fee","exit_fee","fees","gross_pnl","net_pnl","reason","hold_seconds","mfe_price","mae_price","mfe_pct","mae_pct","mfe_r","mae_r","signal_source","turtle_signal","af_signal","dtp_signal"]
SIGNAL_FIELDS=["signal_id","time","bar_time","pair","side","turtle_signal","af_signal","dtp_signal","decision","reject_reason","entry_price","bid","ask","spread_bps","signal_source"]

def getj(url,params=None,timeout=12,retries=2):
    for a in range(retries+1):
        try:
            r=HTTP.get(url,params=params,timeout=(5,timeout)); r.raise_for_status(); return r.json()
        except Exception:
            if a>=retries: raise
            time.sleep(2**a)

def active_pairs(n=30):
    active={x for x in getj(ACTIVE) if isinstance(x,str) and x.endswith("_USDT")}
    prices=getj(PRICES).get("prices",{})
    ranked=sorted(((p,float(v.get("v",0))) for p,v in prices.items() if p in active and isinstance(v,dict)),key=lambda x:x[1],reverse=True)
    return [p for p,_ in ranked[:n]] if ranked else sorted(active)[:n]

def bars(pair,bars=280):
    now=int(time.time())
    j=getj(CANDLES,{"pair":pair,"from":now-bars*300-900,"to":now,"resolution":5,"pcode":"f"})
    out=[]
    for x in j.get("data",[]):
        try: out.append({"time":int(x["time"]),"open":float(x["open"]),"high":float(x["high"]),"low":float(x["low"]),"close":float(x["close"]),"volume":float(x["volume"])})
        except Exception: pass
    return sorted(out,key=lambda x:x["time"])[-bars:]

def book(pair):
    try:
        d=getj(BOOK.format(pair=pair),timeout=5,retries=1); b=d.get("bids",{}); a=d.get("asks",{})
        if not b or not a:return None
        bid=max(float(x) for x in b); ask=min(float(x) for x in a)
        return (bid,ask) if 0<bid<ask else None
    except Exception:return None

def sma(a,n):
    o=[float("nan")]*len(a); s=0
    for i,v in enumerate(a):
        s+=v
        if i>=n:s-=a[i-n]
        if i>=n-1:o[i]=s/n
    return o

def stdev(a,n):
    o=[float("nan")]*len(a)
    for i in range(n-1,len(a)):
        w=a[i-n+1:i+1]
        if all(math.isfinite(x) for x in w):
            m=sum(w)/n; o[i]=math.sqrt(sum((x-m)**2 for x in w)/n)
    return o

def atr(r,n):
    tr=[]
    for i,x in enumerate(r):
        tr.append(x["high"]-x["low"] if i==0 else max(x["high"]-x["low"],abs(x["high"]-r[i-1]["close"]),abs(x["low"]-r[i-1]["close"])))
    return sma(tr,n)

def turtle(r,i):
    if i<TURTLE+1:return 0
    hi=max(x["high"] for x in r[i-TURTLE:i]); lo=min(x["low"] for x in r[i-TURTLE:i]); x=r[i]
    if x["low"]<lo and x["close"]>lo:return 1
    if x["high"]>hi and x["close"]<hi:return -1
    return 0

def af(r):
    """AF StochZ signal plus full diagnostic values for the latest closed bar."""
    c=[x["close"] for x in r]; kraw=[float("nan")]*len(c); lengths=[None]*len(c)
    for i in range(len(c)):
        if i<AF_ER:continue
        ch=abs(c[i]-c[i-AF_ER]); vol=sum(abs(c[j]-c[j-1]) for j in range(i-AF_ER+1,i+1)); er=ch/vol if vol else 0
        ln=max(AF_MIN,min(AF_MAX,round(AF_MAX-er*(AF_MAX-AF_MIN))))
        lengths[i]=ln
        w=r[max(0,i-ln+1):i+1]; hi=max(x["high"] for x in w); lo=min(x["low"] for x in w)
        kraw[i]=((c[i]-lo)/(hi-lo)*100) if hi>lo else 50
    k=sma([50 if not math.isfinite(x) else x for x in kraw],3); d=sma([50 if not math.isfinite(x) else x for x in k],3)
    fisher=[float("nan")]*len(c)
    for i,x in enumerate(k):
        if math.isfinite(x):
            z0=max(-.998,min(.998,(x-50)/50)); fisher[i]=.5*math.log((1+z0)/(1-z0))
    fm=sma([0 if not math.isfinite(x) else x for x in fisher],AF_Z)
    fs=stdev([0 if not math.isfinite(x) else x for x in fisher],AF_Z)
    z=[float("nan")]*len(c)
    for i in range(len(c)):
        if math.isfinite(fisher[i]) and math.isfinite(fs[i]) and fs[i]>1e-12:
            z[i]=(fisher[i]-fm[i])/fs[i]
    i=len(c)-1
    diag={
        "ready":False,"adaptive_length":lengths[i] if i<len(lengths) else None,
        "k":None,"d":None,"fisher":None,"z":None,"prev_z":None,
        "z_reversal_long":False,"z_reversal_short":False,
        "stoch_cross_long":False,"stoch_cross_short":False,
        "bull":False,"bear":False,"reason":"INSUFFICIENT_DATA"
    }
    if i<3:return 0,diag
    vals={"k":k[i],"d":d[i],"fisher":fisher[i],"z":z[i],
          "prev_z":z[i-1] if i>0 else None}
    diag.update({key:(float(val) if isinstance(val,(int,float)) and math.isfinite(val) else None) for key,val in vals.items()})
    diag["ready"]=all(diag[x] is not None for x in ("k","d","z","prev_z"))
    if not diag["ready"]:
        diag["reason"]="INDICATOR_NOT_READY"
        return 0,diag
    diag["z_reversal_long"]=diag["z"] < -1 and diag["z"] > diag["prev_z"]
    diag["z_reversal_short"]=diag["z"] > 1 and diag["z"] < diag["prev_z"]
    diag["stoch_cross_long"]=diag["k"] > diag["d"]
    diag["stoch_cross_short"]=diag["k"] < diag["d"]
    bull=diag["z_reversal_long"] and diag["stoch_cross_long"]
    bear=diag["z_reversal_short"] and diag["stoch_cross_short"]
    diag["bull"]=bull; diag["bear"]=bear
    diag["reason"]="LONG_TRIGGER" if bull else "SHORT_TRIGGER" if bear else "NO_TRIGGER"
    return (1 if bull else -1 if bear else 0),diag

def dtp(r):
    c=[x["close"] for x in r]; s=sma(c,DTP_SMA); a=atr(r,DTP_ATR); i=len(c)-1
    if i<1 or not math.isfinite(s[i]) or not math.isfinite(s[i-1]) or not math.isfinite(a[i]) or a[i]<=0:return 0
    slope=(s[i]-s[i-1])/a[i]
    return 1 if slope>0 and c[i]>=s[i] else -1 if slope<0 and c[i]<=s[i] else 0

def signal(r):
    if len(r)<DTP_ATR+5:return (0,0,0,0,{})
    t=turtle(r,len(r)-1); a,ad=af(r); d=dtp(r)
    combined=1 if t==1 and a==1 and d==1 else -1 if t==-1 and a==-1 and d==-1 else 0
    return combined,t,a,d,ad

def default():
    return {"version":1,"day_ist":"","cash":100000.0,"realized_pnl":0.0,"daily_start_equity":100000.0,"daily_drawdown_rs":0.0,"peak_equity":100000.0,"max_drawdown_rs":0.0,"trades_today":0,"locked":False,"lock_reason":"","position":None,"last_signal":{},"last_processed_bar":{},"pairs":[],"events":0,"signals":0,"accepted_signals":0,"rejected_signals":0,"errors":0,"updated_at":None,"heartbeat_at":None,"run_started_at":None,"run_finished_at":None,"status":"STARTING","signal_map":{},"af_diagnostics":{},"af_long_candidates":0,"af_short_candidates":0}

def load():
    if not STATE.exists():return default()
    try:
        s=default();s.update(json.loads(STATE.read_text()));return s
    except Exception:return default()

def save(s): DATA.mkdir(parents=True,exist_ok=True);STATE.write_text(json.dumps(s,indent=2))

def csvadd(path,fields,row):
    new=not path.exists() or path.stat().st_size==0; DATA.mkdir(parents=True,exist_ok=True)
    with path.open("a",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore")
        if new:w.writeheader()
        w.writerow({k:row.get(k,"") for k in fields})

def event(s,e):
    s.setdefault("journal",[]).insert(0,{**e,"time":datetime.now(timezone.utc).isoformat()});s["journal"]=s["journal"][:1000]

def roll(s):
    d=datetime.now(IST).date().isoformat()
    if s["day_ist"]!=d:
        s["day_ist"]=d;s["trades_today"]=0;s["locked"]=False;s["lock_reason"]="";s["realized_pnl"]=0;s["daily_start_equity"]=s["cash"];s["daily_drawdown_rs"]=0

def enter(s,pair,side,bid,ask,sid,t,a,d):
    if s["position"] or s["locked"] or s["trades_today"]>=MAX_TRADES:return False
    p0=ask if side==1 else bid; px=p0*(1+SLIP if side==1 else 1-SLIP); risk=s["cash"]*RISK; qty=risk/(px*SL); fee=px*qty*FEE
    if qty<=0 or s["cash"]<=fee:return False
    name="LONG" if side==1 else "SHORT";tid=uuid.uuid4().hex
    s["cash"]-=fee;s["trades_today"]+=1;s["accepted_signals"]+=1
    s["position"]={"trade_id":tid,"signal_id":sid,"pair":pair,"side":name,"entry_price":px,"quantity":qty,"entry_fee":fee,"stop_price":px*(1-SL if side==1 else 1+SL),"target_price":px*(1+TP if side==1 else 1-TP),"opened_at":datetime.now(timezone.utc).isoformat(),"kind":"TURTLE+AF_STOCHZ+DTP","mfe_price":px,"mae_price":px,"turtle_signal":t,"af_signal":a,"dtp_signal":d}
    event(s,{"event":"ENTRY","trade_id":tid,"signal_id":sid,"pair":pair,"side":name,"entry_price":px,"quantity":qty,"stop_price":s["position"]["stop_price"],"target_price":s["position"]["target_price"]})
    return True

def exitpos(s,bid,ask,reason):
    p=s["position"]
    if not p:return
    side=1 if p["side"]=="LONG" else -1; p0=bid if side==1 else ask; px=p0*(1-SLIP if side==1 else 1+SLIP)
    gross=(px-p["entry_price"])*p["quantity"]*side; fee=px*p["quantity"]*FEE; net=gross-p["entry_fee"]-fee
    s["cash"]+=gross-fee;s["realized_pnl"]+=net
    hold=(datetime.now(timezone.utc)-datetime.fromisoformat(p["opened_at"])).total_seconds()
    mfe=abs(p["mfe_price"]-p["entry_price"])/p["entry_price"];mae=abs(p["mae_price"]-p["entry_price"])/p["entry_price"]
    row={"trade_id":p["trade_id"],"signal_id":p["signal_id"],"pair":p["pair"],"side":p["side"],"entry_time":p["opened_at"],"exit_time":datetime.now(timezone.utc).isoformat(),"entry_price":p["entry_price"],"exit_price":px,"stop_price":p["stop_price"],"target_price":p["target_price"],"quantity":p["quantity"],"entry_fee":p["entry_fee"],"exit_fee":fee,"fees":p["entry_fee"]+fee,"gross_pnl":gross,"net_pnl":net,"reason":reason,"hold_seconds":hold,"mfe_price":p["mfe_price"],"mae_price":p["mae_price"],"mfe_pct":mfe,"mae_pct":mae,"mfe_r":mfe/SL,"mae_r":mae/SL,"signal_source":p["kind"],"turtle_signal":p["turtle_signal"],"af_signal":p["af_signal"],"dtp_signal":p["dtp_signal"]}
    csvadd(TRADES,TRADE_FIELDS,row);event(s,{"event":"EXIT",**row});s["position"]=None
    if s["realized_pnl"]<=-DAILY_LOCK:s["locked"]=True;s["lock_reason"]="DAILY_LOSS_LOCK"

def excursion(s,rows):
    p=s.get("position")
    if not p:return
    side=1 if p["side"]=="LONG" else -1
    for x in rows[-3:]:
        if side==1:
            p["mfe_price"]=max(p["mfe_price"],x["high"]);p["mae_price"]=min(p["mae_price"],x["low"])
        else:
            p["mfe_price"]=max(p["mfe_price"],2*p["entry_price"]-x["low"]);p["mae_price"]=min(p["mae_price"],2*p["entry_price"]-x["high"])

def update_dd(s):
    p=s.get("position")
    if not p:return
    q=book(p["pair"])
    if not q:return
    bid,ask=q; mark=(bid+ask)/2; side=1 if p["side"]=="LONG" else -1
    eq=s["cash"]+(mark-p["entry_price"])*p["quantity"]*side
    dd=max(0.0,s["daily_start_equity"]-eq);s["daily_drawdown_rs"]=max(s.get("daily_drawdown_rs",0),dd)
    s["peak_equity"]=max(s.get("peak_equity",100000),eq);s["max_drawdown_rs"]=max(s.get("max_drawdown_rs",0),s["peak_equity"]-eq)
    if dd>=DAILY_LOCK:s["locked"]=True;s["lock_reason"]="EQUITY_DRAWDOWN_LOCK"

def risk(s):
    p=s.get("position")
    if not p:return
    q=book(p["pair"])
    if not q:return
    bid,ask=q
    if p["side"]=="LONG":
        if bid<=p["stop_price"]:exitpos(s,bid,ask,"STOP_LOSS")
        elif bid>=p["target_price"]:exitpos(s,bid,ask,"TAKE_PROFIT")
    else:
        if ask>=p["stop_price"]:exitpos(s,bid,ask,"STOP_LOSS")
        elif ask<=p["target_price"]:exitpos(s,bid,ask,"TAKE_PROFIT")

def process(s,pair,rows,cache):
    if len(rows)<DTP_ATR+5:return
    cache[pair]=rows[-120:]
    bucket=(int(time.time())//300)*300000; closed=[x for x in rows if x["time"]<bucket]
    if len(closed)<DTP_ATR+5:return
    bt=closed[-1]["time"]
    if s["last_processed_bar"].get(pair)==bt:return
    s["last_processed_bar"][pair]=bt;s["events"]+=1
    combined,t,a,d,ad=signal(closed)
    s["signal_map"][pair]={"combined":combined,"turtle":t,"af":a,"dtp":d,"bar_time":bt}
    s.setdefault("af_diagnostics",{})[pair]={**ad,"bar_time":bt}
    if ad.get("bull"):s["af_long_candidates"]=s.get("af_long_candidates",0)+1
    if ad.get("bear"):s["af_short_candidates"]=s.get("af_short_candidates",0)+1
    if not combined:return
    sid=f"{pair}-{bt}-{combined}"
    if s["last_signal"].get(pair)==sid:return
    s["last_signal"][pair]=sid
    q=book(pair);bid,ask=q if q else (None,None);spread=((ask-bid)/((ask+bid)/2)*10000) if bid and ask else None
    wanted="LONG" if combined==1 else "SHORT"; decision="ACCEPTED";reject=""
    if not q:decision,reject="REJECTED","NO_LIVE_ORDERBOOK"
    elif s["position"]:decision,reject="REJECTED","POSITION_ALREADY_OPEN"
    elif s["locked"]:decision,reject="REJECTED",s.get("lock_reason") or "RISK_LOCK"
    elif s["trades_today"]>=MAX_TRADES:decision,reject="REJECTED","DAILY_TRADE_CAP"
    row={"signal_id":sid,"time":datetime.now(timezone.utc).isoformat(),"bar_time":datetime.fromtimestamp(bt/1000,timezone.utc).isoformat(),"pair":pair,"side":wanted,"turtle_signal":t,"af_signal":a,"dtp_signal":d,"decision":decision,"reject_reason":reject,"entry_price":ask if combined==1 else bid,"bid":bid,"ask":ask,"spread_bps":spread,"signal_source":"TURTLE+AF_STOCHZ+DTP"}
    csvadd(SIGNALS,SIGNAL_FIELDS,row);s["signals"]+=1
    if decision!="ACCEPTED":s["rejected_signals"]+=1;event(s,{"event":"SIGNAL_REJECTED",**row});return
    if not enter(s,pair,combined,bid,ask,sid,t,a,d):s["rejected_signals"]+=1;event(s,{"event":"SIGNAL_REJECTED","pair":pair,"signal_id":sid,"reason":"ENTRY_GUARD"})

def snapshot(s,pairs,cache):
    try:
        prices=getj(PRICES).get("prices",{})
        MARKET.write_text(json.dumps({"updated_at":datetime.now(timezone.utc).isoformat(),"prices":{p:prices[p] for p in pairs if p in prices},"signals":s["signal_map"],"candles":{p:cache.get(p,[]) for p in pairs if cache.get(p)},"source":"CoinDCX public futures REST","candle_resolution":5},separators=(",",":")))
    except Exception as e:
        s["errors"]+=1
        event(s,{"event":"SNAPSHOT_ERROR","error":str(e)})
        try: save(s)
        except Exception: pass

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--minutes",type=float,default=4.5);ap.add_argument("--max-pairs",type=int,default=30);a=ap.parse_args()
    DATA.mkdir(parents=True,exist_ok=True);s=load();roll(s)
    s["run_started_at"]=datetime.now(timezone.utc).isoformat();s["run_finished_at"]=None;s["status"]="STARTING";s["updated_at"]=s["run_started_at"];s["heartbeat_at"]=s["run_started_at"];save(s)
    try:
        try:pairs=active_pairs(a.max_pairs)
        except Exception as e:pairs=s.get("pairs",[])[:a.max_pairs];s["errors"]+=1;event(s,{"event":"ACTIVE_PAIRS_ERROR","error":str(e)})
        s["pairs"]=pairs;save(s)
        end=time.monotonic()+a.minutes*60;cache={};workers=min(8,max(1,len(pairs)))
        while time.monotonic()<end:
            fetched={}
            with ThreadPoolExecutor(max_workers=workers) as pool:
                fs={pool.submit(bars,p):p for p in pairs}
                for f in as_completed(fs):
                    p=fs[f]
                    try:fetched[p]=f.result()
                    except Exception as e:s["errors"]+=1;event(s,{"event":"FETCH_ERROR","pair":p,"error":str(e)})
            for p in pairs:
                try:
                    rr=fetched.get(p) or cache.get(p,[])
                    if s.get("position") and s["position"].get("pair")==p: excursion(s,rr)
                    process(s,p,rr,cache);risk(s);update_dd(s)
                except Exception as e:s["errors"]+=1;event(s,{"event":"PAIR_ERROR","pair":p,"error":str(e)})
            s["updated_at"]=datetime.now(timezone.utc).isoformat();s["heartbeat_at"]=s["updated_at"];s["status"]="LIVE_PAPER";save(s);time.sleep(15)
        snapshot(s,pairs,cache)
        s["run_finished_at"]=datetime.now(timezone.utc).isoformat();s["updated_at"]=s["run_finished_at"];s["heartbeat_at"]=s["run_finished_at"];s["status"]="LIVE_PAPER";save(s)
    except Exception as e:
        s["errors"]+=1;s["status"]="ERROR";s["updated_at"]=datetime.now(timezone.utc).isoformat();s["heartbeat_at"]=s["updated_at"]
        event(s,{"event":"ENGINE_FATAL_ERROR","error":str(e)})
        save(s)
        raise

if __name__=="__main__":main()
