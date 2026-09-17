from __future__ import annotations
import json,time
from datetime import datetime,timezone
from pathlib import Path
import pandas as pd, requests
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.execution.paper import PaperBroker,PaperConfig
from app.signals.tw_all_in_one_filtered import filtered_signals
ACTIVE='https://api.coindcx.com/exchange/v1/derivatives/futures/data/active_instruments?margin_currency_short_name[]=USDT'
PRICES='https://public.coindcx.com/market_data/v3/current_prices/futures/rt'
CANDLES='https://public.coindcx.com/market_data/candlesticks'
BOOK='https://public.coindcx.com/market_data/v3/orderbook/{pair}-futures/50'
def get(url,params=None,timeout=10):
 r=requests.get(url,params=params,timeout=timeout);r.raise_for_status();return r.json()
def pairs(n=30):
 a=[x for x in get(ACTIVE) if isinstance(x,str) and x.endswith('_USDT')];p=get(PRICES);return [x for x,_ in sorted(((x,float(p.get(x,{}).get('v',0))) for x in a),key=lambda z:z[1],reverse=True)[:n]]
def bars(pair,n=220):
 now=int(time.time());d=get(CANDLES,{'pair':pair,'from':now-n*300-900,'to':now,'resolution':'5','pcode':'f'})
 return sorted([(int(x['time']),float(x['open']),float(x['high']),float(x['low']),float(x['close']),float(x['volume'])) for x in d.get('data',[])])[-n:]
def frame(r):
 return pd.DataFrame({'open':[x[1] for x in r],'high':[x[2] for x in r],'low':[x[3] for x in r],'close':[x[4] for x in r],'volume':[x[5] for x in r]},index=pd.to_datetime([x[0] for x in r],unit='ms',utc=True))
def ai(df):
 c=df.close.astype(float);v=df.volume.astype(float)
 if len(c)<60:return 'NO TRADE',50
 e20=c.ewm(span=20,adjust=False).mean().iloc[-1];e50=c.ewm(span=50,adjust=False).mean().iloc[-1];d=c.diff();g=d.clip(lower=0).ewm(alpha=1/14,adjust=False).mean().iloc[-1];l=(-d.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean().iloc[-1];r=100-100/(1+g/(l or 1e-12));vr=v.iloc[-1]/(v.tail(20).mean() or 1);s=50+(12 if c.iloc[-1]>e20 else -12)+(12 if e20>e50 else -12)+(10 if 50<r<70 else -10 if 30<r<50 else 0)+(8 if vr>1.2 else 0);return ('LONG' if s>=62 else 'SHORT' if s<=38 else 'NO TRADE'),max(0,min(100,int(s)))
def book(pair):
 try:
  d=get(BOOK.format(pair=pair),timeout=5);b=max(map(float,d.get('bids',{})));a=min(map(float,d.get('asks',{})));return (b,a) if a>b>0 else None
 except Exception:return None
def main():
 import argparse
 ap=argparse.ArgumentParser();ap.add_argument('--seconds',type=int,default=300);ap.add_argument('--max-pairs',type=int,default=30);ap.add_argument('--out',default='data/tw_realtime_paper');a=ap.parse_args()
 out=Path(a.out);out.mkdir(parents=True,exist_ok=True);journal=[];broker=PaperBroker(PaperConfig(initial_capital=100000,risk_per_trade=.0025,stop_pct=.005,target_pct=.01,fee_bps_per_side=5,slippage_bps=2,max_trades_per_day=5,max_daily_loss_pct=.02));ps=pairs(a.max_pairs);seen={};start=time.monotonic();events=signals=errors=0
 while time.monotonic()-start<a.seconds:
  for p in ps:
   try:
    r=bars(p);df=frame(r)
    if len(df)<210:continue
    events+=1;sig=int(filtered_signals(df,hull_length=8,ema_length=200,ema_filter=True,slope_filter=True,volume_ratio_min=1.2,atr_expansion_min=1.1,ema_distance_min=.003,rsi_filter=False,cooldown_bars=3).iloc[-1]);side,score=ai(df);bucket=r[-1][0];key=(bucket,sig,side)
    if sig and ((sig==1 and side=='LONG') or (sig==-1 and side=='SHORT')) and score>=62 and seen.get(p)!=key:
     seen[p]=key;signals+=1;q=book(p);ts=datetime.now(timezone.utc).isoformat();journal.append({'event':'SIGNAL','pair':p,'signal':sig,'side':side,'confidence':score,'time':ts})
     if q and broker.position is None and broker.can_enter(datetime.now(timezone.utc),q[0],q[1]) and broker.enter(datetime.now(timezone.utc),sig,q[0],q[1]):journal.append({'event':'ENTRY','pair':p,'side':side,'confidence':score,'bid':q[0],'ask':q[1],'time':ts})
    if broker.position is not None:
     ep=next((x['pair'] for x in journal[::-1] if x.get('event')=='ENTRY'),p);q=book(ep)
     if q:broker.check_risk_exits(datetime.now(timezone.utc),q[0],q[1])
   except Exception as e:errors+=1;journal.append({'event':'ERROR','pair':p,'error':str(e),'time':datetime.now(timezone.utc).isoformat()})
  time.sleep(10)
 summary={'mode':'MULTI_COIN_TW_AI_INTRABAR_PAPER','status':'LIVE_PAPER','updated_at':datetime.now(timezone.utc).isoformat(),'pairs':len(ps),'pair_list':ps,'events':events,'signals':signals,'errors':errors,'trades_entered':sum(x.get('event')=='ENTRY' for x in journal),'trades_closed':sum(x.get('event')=='EXIT' for x in journal),'realized_pnl':broker.realized_pnl,'ending_cash':broker.cash,'journal':journal[-100:]}
 (out/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
