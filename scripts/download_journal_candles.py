"""Download CoinDCX futures 5m candles covering every closed journal trade."""
from __future__ import annotations
import concurrent.futures as cf
import csv, hashlib, json, re, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
ROOT = Path(__file__).resolve().parents[1]
JOURNAL = ROOT / "data/paper_live/trade_journal.csv"
OUT = ROOT / "research_artifacts/journal_candles"
URL = "https://public.coindcx.com/market_data/candlesticks"
CHUNK_DAYS = 7
def get_json(session, params):
    last = None
    for attempt in range(5):
        try:
            r=session.get(URL,params=params,timeout=30); r.raise_for_status()
            payload=r.json()
            if isinstance(payload,dict):
                if payload.get("s") not in (None,"ok"): raise RuntimeError(f"CoinDCX status: {payload}")
                return payload.get("data",[])
            return payload
        except Exception as exc:
            last=exc; time.sleep(min(2**attempt,16))
    raise RuntimeError(f"CoinDCX request failed: {last}")
def fetch_pair(pair,start,end):
    session=requests.Session()
    session.headers.update({"User-Agent":"AMTE-Journal-Candle-Research/1.0","Accept":"application/json"})
    rows=[]; cursor=start
    while cursor<end:
        stop=min(cursor+timedelta(days=CHUNK_DAYS),end)
        rows.extend(get_json(session,{"pair":pair,"from":int(cursor.timestamp()),"to":int(stop.timestamp()),"resolution":"5","pcode":"f"}))
        cursor=stop; time.sleep(0.12)
    unique={}
    for x in rows:
        try:
            ms=int(x["time"]); unique[ms]={k:float(x[k]) for k in ("open","high","low","close","volume")}
        except (KeyError,TypeError,ValueError): pass
    ordered=sorted(unique.items()); name=re.sub(r"[^A-Za-z0-9_.-]+","_",pair); path=OUT/f"{name}_5m.csv"
    with path.open("w",newline="",encoding="utf-8") as f:
        w=csv.writer(f); w.writerow(["timestamp","open","high","low","close","volume"])
        for ms,v in ordered: w.writerow([datetime.fromtimestamp(ms/1000,timezone.utc).isoformat(),*[v[k] for k in ("open","high","low","close","volume")]])
    return {"pair":pair,"rows":len(ordered),"start":datetime.fromtimestamp(ordered[0][0]/1000,timezone.utc).isoformat() if ordered else None,"end":datetime.fromtimestamp(ordered[-1][0]/1000,timezone.utc).isoformat() if ordered else None,"file":path.name,"sha256":hashlib.sha256(path.read_bytes()).hexdigest() if ordered else None}
def main():
    if not JOURNAL.exists(): raise FileNotFoundError(JOURNAL)
    OUT.mkdir(parents=True,exist_ok=True)
    with JOURNAL.open(newline="",encoding="utf-8") as f: trades=list(csv.DictReader(f))
    groups={}
    for t in trades:
        pair=t.get("pair","").strip()
        if not pair: continue
        a=datetime.fromisoformat(t["entry_time"].replace("Z","+00:00")).astimezone(timezone.utc)-timedelta(minutes=10)
        b=datetime.fromisoformat(t["exit_time"].replace("Z","+00:00")).astimezone(timezone.utc)+timedelta(minutes=10)
        groups[pair]=[min(groups[pair][0],a),max(groups[pair][1],b)] if pair in groups else [a,b]
    m={"source":"CoinDCX public futures candlesticks","resolution":"5m","pcode":"f","journal_trades":len(trades),"pair_count":len(groups),"generated_at":datetime.now(timezone.utc).isoformat(),"coverage_rule":"per-pair min(entry)-10m to max(exit)+10m; UTC; OHLCV","pairs":[]}
    with cf.ThreadPoolExecutor(max_workers=3) as pool:
        fs={pool.submit(fetch_pair,p,*span):p for p,span in groups.items()}
        for f in cf.as_completed(fs):
            x=f.result(); m["pairs"].append(x); print(f"{x['pair']}: {x['rows']} candles")
    m["pairs"].sort(key=lambda x:x["pair"]); m["total_candles"]=sum(x["rows"] for x in m["pairs"]); m["empty_pairs"]=[x["pair"] for x in m["pairs"] if x["rows"]==0]
    (OUT/"manifest.json").write_text(json.dumps(m,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k:m[k] for k in ("journal_trades","pair_count","total_candles","empty_pairs")},indent=2))
    if m["empty_pairs"]: raise RuntimeError(f"No candle data for: {m['empty_pairs']}")
if __name__=="__main__": main()
