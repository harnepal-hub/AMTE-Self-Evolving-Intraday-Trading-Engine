#!/usr/bin/env python3
"""Isolated 3-Strategy Lab portfolio research. Public CoinDCX data only; never places orders.

Runs a chronological multi-pair portfolio simulation for entry consensus, direction,
position concurrency and exit variants. Existing paper/live engine is not imported
or modified. Intrabar stop/target ambiguity is resolved conservatively (stop first).
"""
from __future__ import annotations
import argparse, csv, json, math, time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import requests

BASE = Path(__file__).resolve().parents[1]
OUT = BASE / "research_artifacts" / "three_strategy_optimisation"
ACTIVE = "https://api.coindcx.com/exchange/v1/derivatives/futures/data/active_instruments?margin_currency_short_name[]=USDT"
PRICES = "https://public.coindcx.com/market_data/v3/current_prices/futures/rt"
CANDLES = "https://public.coindcx.com/market_data/candlesticks"
FEE, SLIP = 0.0005, 0.0002
CAPITAL, RISK, DAILY_STOP, DAILY_CAP = 100000.0, 0.001, 0.02, 10
TURTLE, DTP_SMA, DTP_ATR = 20, 50, 200
AF_MIN, AF_MAX, AF_ER, AF_Z = 8, 34, 10, 20
S = requests.Session()
S.headers.update({"User-Agent":"AMTE-3Strategy-Research/1.0","Accept":"application/json"})

def getj(url, params=None):
    r=S.get(url,params=params,timeout=(8,25)); r.raise_for_status(); return r.json()

def active_pairs(n):
    active={x for x in getj(ACTIVE) if isinstance(x,str) and x.endswith("_USDT")}
    prices=getj(PRICES).get("prices",{})
    ranked=sorted(((p,float(v.get("v",0))) for p,v in prices.items()
                   if p in active and isinstance(v,dict)),key=lambda z:z[1],reverse=True)
    return [p for p,_ in ranked[:n]]

def fetch_pair(pair, days):
    end=int(time.time()); start=end-days*86400; out={}
    # Small windows avoid exchange candle endpoint limits and missing large responses.
    cursor=start
    while cursor<end:
        stop=min(end,cursor+5*86400)
        data=getj(CANDLES,{"pair":pair,"from":cursor,"to":stop,"resolution":5,"pcode":"f"}).get("data",[])
        for x in data:
            try:
                t=int(x["time"]); out[t]={"t":t,"o":float(x["open"]),"h":float(x["high"]),
                    "l":float(x["low"]),"c":float(x["close"]),"v":float(x["volume"])}
            except (KeyError,TypeError,ValueError): pass
        cursor=stop
        time.sleep(.08)
    return pair, sorted(out.values(),key=lambda x:x["t"])

def sma(a,n):
    out=[math.nan]*len(a); total=0.0
    for i,x in enumerate(a):
        total+=x
        if i>=n: total-=a[i-n]
        if i>=n-1: out[i]=total/n
    return out

def atr(rows,n):
    tr=[]
    for i,x in enumerate(rows):
        tr.append(x["h"]-x["l"] if i==0 else max(x["h"]-x["l"],abs(x["h"]-rows[i-1]["c"]),abs(x["l"]-rows[i-1]["c"])))
    return sma(tr,n)

def stdev(a,n):
    out=[math.nan]*len(a)
    for i in range(n-1,len(a)):
        w=a[i-n+1:i+1]; m=sum(w)/n
        out[i]=math.sqrt(sum((x-m)**2 for x in w)/n)
    return out

def indicators(rows):
    c=[x["c"] for x in rows]; kraw=[math.nan]*len(c)
    for i in range(AF_ER,len(c)):
        ch=abs(c[i]-c[i-AF_ER]); vol=sum(abs(c[j]-c[j-1]) for j in range(i-AF_ER+1,i+1))
        er=ch/vol if vol else 0.0; ln=max(AF_MIN,min(AF_MAX,round(AF_MAX-er*(AF_MAX-AF_MIN))))
        w=rows[max(0,i-ln+1):i+1]; hi=max(x["h"] for x in w); lo=min(x["l"] for x in w)
        kraw[i]=(c[i]-lo)/(hi-lo)*100 if hi>lo else 50.0
    k=sma([50.0 if not math.isfinite(x) else x for x in kraw],3)
    d=sma(k,3); fisher=[]
    for x in k:
        z=max(-.998,min(.998,(x-50)/50)); fisher.append(.5*math.log((1+z)/(1-z)))
    fm=sma(fisher,AF_Z); fs=stdev(fisher,AF_Z); z=[math.nan]*len(c)
    for i in range(len(c)):
        if math.isfinite(fs[i]) and fs[i]>1e-12: z[i]=(fisher[i]-fm[i])/fs[i]
    dtp_s=sma(c,DTP_SMA); dtp_a=atr(rows,DTP_ATR)
    result=[]
    for i,x in enumerate(rows):
        t=0; a=0; d0=0
        if i>=TURTLE+1:
            hi=max(q["h"] for q in rows[i-TURTLE:i]); lo=min(q["l"] for q in rows[i-TURTLE:i])
            if x["l"]<lo and x["c"]>lo: t=1
            elif x["h"]>hi and x["c"]<hi: t=-1
        if i>0 and math.isfinite(z[i]) and math.isfinite(z[i-1]) and math.isfinite(k[i]) and math.isfinite(d[i]):
            if z[i]<-1 and z[i]>z[i-1] and k[i]>d[i]: a=1
            elif z[i]>1 and z[i]<z[i-1] and k[i]<d[i]: a=-1
        if i>0 and math.isfinite(dtp_s[i]) and math.isfinite(dtp_s[i-1]) and math.isfinite(dtp_a[i]) and dtp_a[i]>0:
            slope=(dtp_s[i]-dtp_s[i-1])/dtp_a[i]
            if slope>0 and c[i]>=dtp_s[i]: d0=1
            elif slope<0 and c[i]<=dtp_s[i]: d0=-1
        result.append((t,a,d0,dtp_a[i]))
    return result

def consensus(t,a,d,mode):
    votes=[v for v in (t,a,d) if v]
    if mode=="ALL3": return t if t==a==d and t else 0
    if mode=="TURTLE_AF": return t if t and t==a else 0
    if mode=="TURTLE_DTP": return t if t and t==d else 0
    if mode=="AF_DTP": return a if a and a==d else 0
    if mode=="2_OF_3":
        longs=sum(v==1 for v in (t,a,d)); shorts=sum(v==-1 for v in (t,a,d))
        return 1 if longs>=2 else -1 if shorts>=2 else 0
    return 0

def build_events(series, mode, direction):
    events=[]
    for pair,rows in series.items():
        ind=indicators(rows)
        for i in range(1,len(rows)-1):
            t,a,d,aa=ind[i]
            side=consensus(t,a,d,mode)
            if direction=="SHORT_ONLY" and side!=-1: continue
            if direction=="LONG_ONLY" and side!=1: continue
            if side:
                # Signal confirmed at candle close; enter at next candle open.
                events.append((rows[i+1]["t"],pair,side,rows[i+1],aa))
    return sorted(events,key=lambda x:(x[0],x[1]))

def simulate(series, mode, direction, exit_mode, max_open, split_start, split_end):
    events=build_events(series,mode,direction)
    by_time=defaultdict(list)
    for e in events:
        if split_start<=e[0]<split_end: by_time[e[0]].append(e)
    # Replay every candle in chronological order so open positions are marked/exited.
    timeline=defaultdict(list)
    for pair,rows in series.items():
        for r in rows:
            if split_start<=r["t"]<split_end: timeline[r["t"]].append((pair,r))
    equity=CAPITAL; peak=CAPITAL; maxdd=0.0; pos={}; trades=[]; daily_count=defaultdict(int)
    daily_equity={}; locked=set(); event_map=by_time
    for ts in sorted(timeline):
        day=ts//86400
        daily_equity.setdefault(day,equity)
        # Manage exits first. If both barriers touched, assume stop was hit first.
        for pair,p in list(pos.items()):
            bar=next((b for pp,b in timeline[ts] if pp==pair),None)
            if bar is None: continue
            side=p["side"]; stop=p["stop"]; target=p["target"]; reason=None; px=None
            if side==1:
                if bar["l"]<=stop: reason,px="STOP",stop*(1-SLIP)
                elif bar["h"]>=target: reason,px="TARGET",target*(1-SLIP)
            else:
                if bar["h"]>=stop: reason,px="STOP",stop*(1+SLIP)
                elif bar["l"]<=target: reason,px="TARGET",target*(1+SLIP)
            if reason:
                gross=(px-p["entry"])*p["qty"]*side
                fee=px*p["qty"]*FEE
                net=gross-p["entry_fee"]-fee; equity+=gross-fee
                trades.append({"pair":pair,"side":"LONG" if side==1 else "SHORT","net":net,
                    "gross":gross,"fees":p["entry_fee"]+fee,"reason":reason,"entry":p["time"],"exit":ts})
                del pos[pair]
        peak=max(peak,equity); maxdd=max(maxdd,(peak-equity)/peak if peak else 0)
        if daily_equity[day]-equity>=CAPITAL*DAILY_STOP: locked.add(day)
        # Process signals only when capital/risk controls permit.
        for _,pair,side,bar,atrv in event_map.get(ts,[]):
            if pair in pos or len(pos)>=max_open or daily_count[day]>=DAILY_CAP or day in locked: continue
            if exit_mode=="ATR":
                if not math.isfinite(atrv) or atrv<=0: continue
                sl_dist=max(.003,1.2*atrv/bar["o"]); tp_dist=max(.006,2.4*atrv/bar["o"])
            else:
                sl_dist,tp_dist={"FIXED_05_10":(.005,.01),"FIXED_06_12":(.006,.012),"FIXED_05_15":(.005,.015)}[exit_mode]
            entry=bar["o"]*(1+SLIP if side==1 else 1-SLIP)
            risk_cash=equity*RISK
            qty=risk_cash/(entry*sl_dist)
            fee=entry*qty*FEE
            if qty<=0 or not math.isfinite(qty): continue
            equity-=fee
            pos[pair]={"side":side,"entry":entry,"qty":qty,"entry_fee":fee,
                "stop":entry*(1-sl_dist if side==1 else 1+sl_dist),
                "target":entry*(1+tp_dist if side==1 else 1-tp_dist),"time":ts}
            daily_count[day]+=1
    # Force-mark any open positions at final close with taker fee and slippage.
    for pair,p in list(pos.items()):
        rows=series[pair]; bar=next((x for x in reversed(rows) if split_start<=x["t"]<split_end),None)
        if bar:
            side=p["side"]; px=bar["c"]*(1-SLIP if side==1 else 1+SLIP)
            gross=(px-p["entry"])*p["qty"]*side; fee=px*p["qty"]*FEE
            net=gross-p["entry_fee"]-fee; equity+=gross-fee
            trades.append({"pair":pair,"side":"LONG" if side==1 else "SHORT","net":net,
                "gross":gross,"fees":p["entry_fee"]+fee,"reason":"END_MARK","entry":p["time"],"exit":bar["t"]})
    wins=[x for x in trades if x["net"]>0]; losses=[x for x in trades if x["net"]<0]
    gp=sum(x["net"] for x in wins); gl=abs(sum(x["net"] for x in losses))
    days=max(1,(split_end-split_start)/86400)
    return {"trades":len(trades),"net_pnl":round(sum(x["net"] for x in trades),2),
        "return_pct":round((equity/CAPITAL-1)*100,3),"profit_factor":round(gp/gl,3) if gl else 99.0,
        "win_rate_pct":round(100*len(wins)/len(trades),2) if trades else 0,
        "fees":round(sum(x["fees"] for x in trades),2),"max_drawdown_pct":round(maxdd*100,3),
        "avg_daily_pnl":round(sum(x["net"] for x in trades)/days,2),
        "long_net":round(sum(x["net"] for x in trades if x["side"]=="LONG"),2),
        "short_net":round(sum(x["net"] for x in trades if x["side"]=="SHORT"),2),
        "trades_per_day":round(len(trades)/days,2)}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--days",type=int,default=90); ap.add_argument("--max-pairs",type=int,default=30)
    ap.add_argument("--workers",type=int,default=6); args=ap.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    pairs=active_pairs(args.max_pairs); fetched={}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        fs={pool.submit(fetch_pair,p,args.days):p for p in pairs}
        for f in as_completed(fs):
            pair=fs[f]
            try:
                p,rows=f.result()
                if len(rows)>=DTP_ATR+20: fetched[p]=rows
                print(f"{p}: {len(rows)} candles",flush=True)
            except Exception as e: print(f"ERROR {pair}: {e}",flush=True)
    if len(fetched)<5: raise RuntimeError(f"Insufficient pair history: {len(fetched)}")
    all_times=sorted({r["t"] for rows in fetched.values() for r in rows})
    lo,hi=all_times[0],all_times[-1]+300
    # 60/20/20 chronological development/validation/locked test.
    cut1=lo+(hi-lo)*.60; cut2=lo+(hi-lo)*.80
    modes=["ALL3","TURTLE_AF","TURTLE_DTP","AF_DTP","2_OF_3"]
    directions=["BOTH","SHORT_ONLY","LONG_ONLY"]
    exits=["FIXED_05_10","FIXED_06_12","FIXED_05_15","ATR"]
    rows_out=[]
    for mode in modes:
      for direction in directions:
       for ex in exits:
        for cap in (1,2,3):
         cfg={"entry":mode,"direction":direction,"exit":ex,"max_open":cap}
         dev=simulate(fetched,mode,direction,ex,cap,lo,cut1)
         val=simulate(fetched,mode,direction,ex,cap,cut1,cut2)
         test=simulate(fetched,mode,direction,ex,cap,cut2,hi)
         rows_out.append({**cfg,"development":dev,"validation":val,"locked_test":test})
    # Rank only on validation; test remains a report-only locked holdout.
    rows_out.sort(key=lambda x:(x["validation"]["profit_factor"]>1,
        x["validation"]["net_pnl"],x["validation"]["max_drawdown_pct"]),reverse=True)
    (OUT/"results.json").write_text(json.dumps({"generated_at":datetime.now(timezone.utc).isoformat(),
        "pairs":list(fetched),"days_requested":args.days,"candles_by_pair":{p:len(v) for p,v in fetched.items()},
        "split":{"start":lo,"dev_end":cut1,"validation_end":cut2,"end":hi},
        "target_daily_pnl":1000,"capital":CAPITAL,"risk_per_trade_pct":RISK*100,
        "note":"Research only. No execution. Locked test must not be used for tuning.",
        "results":rows_out},indent=2))
    with (OUT/"leaderboard.csv").open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=["entry","direction","exit","max_open","dev_net","val_net","val_pf","val_dd","val_avg_daily","test_net","test_pf","test_dd","test_avg_daily","test_trades"])
        w.writeheader()
        for x in rows_out:
            d,v,t=x["development"],x["validation"],x["locked_test"]
            w.writerow({"entry":x["entry"],"direction":x["direction"],"exit":x["exit"],"max_open":x["max_open"],
                "dev_net":d["net_pnl"],"val_net":v["net_pnl"],"val_pf":v["profit_factor"],"val_dd":v["max_drawdown_pct"],
                "val_avg_daily":v["avg_daily_pnl"],"test_net":t["net_pnl"],"test_pf":t["profit_factor"],
                "test_dd":t["max_drawdown_pct"],"test_avg_daily":t["avg_daily_pnl"],"test_trades":t["trades"]})
    print(f"Saved {len(rows_out)} configurations across {len(fetched)} pairs to {OUT}",flush=True)
    print("Top validation configurations:")
    for x in rows_out[:10]:
        print(x["entry"],x["direction"],x["exit"],"open",x["max_open"],
            "val",x["validation"]["net_pnl"],"PF",x["validation"]["profit_factor"],
            "test",x["locked_test"]["net_pnl"],"test PF",x["locked_test"]["profit_factor"])

if __name__=="__main__": main()
