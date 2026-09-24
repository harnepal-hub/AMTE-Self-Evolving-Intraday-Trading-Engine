/* AMTE live-dashboard hotfix: authoritative server signals + live REST refresh. */
(function(){
  const SITE=new URL('.',location.href);
  const STATE=new URL('live_data/state.json',SITE).href;
  const MARKET=new URL('live_data/market.json',SITE).href;
  const RAW_STATE='https://raw.githubusercontent.com/harnepal-hub/AMTE-Self-Evolving-Intraday-Trading-Engine/main/data/paper_live/state.json';
  const RAW_MARKET='https://raw.githubusercontent.com/harnepal-hub/AMTE-Self-Evolving-Intraday-Trading-Engine/main/data/paper_live/market.json';
  const PUB='https://public.coindcx.com';
  const H=(id)=>document.getElementById(id);
  const jf=async(u)=>{const r=await fetch(u+(u.includes('?')?'&':'?')+'t='+Date.now(),{cache:'no-store'});if(!r.ok)throw Error(r.status);return r.json()};
  const n=(p)=>p.replace(/^B-/,'').replace(/_USDT$/,'');
  const f=(x)=>Number(x||0).toLocaleString('en-IN',{maximumFractionDigits:8});
  function mark(t,ok){if(H('dot'))H('dot').className='dot '+(ok?'ok':'');if(H('statusText'))H('statusText').textContent=t}
  function set(id,v){if(H(id))H(id).textContent=v}
  function renderAuthoritative(){
    const s=window.server||{}, sm=s.signal_map||{}, list=Array.isArray(s.pairs)?s.pairs.slice(0,30):Object.keys(window.prices||{}).filter(x=>x.endsWith('_USDT')).slice(0,30);
    if(H('pairs'))H('pairs').textContent=list.length;
    if(H('signals'))H('signals').textContent=s.signals||0;
    if(H('rejected'))H('rejected').textContent=s.rejected_signals||0;
    if(H('events'))H('events').textContent=s.events||0;
    if(H('psignals'))H('psignals').textContent=s.signals||0;
    if(H('entered'))H('entered').textContent=(s.journal||[]).filter(x=>x.event==='ENTRY').length;
    if(H('closed'))H('closed').textContent=(s.journal||[]).filter(x=>x.event==='EXIT').length;
    if(H('cash'))H('cash').textContent='₹'+f(s.cash??100000);
    if(H('pnl')){H('pnl').textContent='₹'+f(s.realized_pnl??0);H('pnl').className='value '+((s.realized_pnl||0)>=0?'green':'red')}
    if(H('trades'))H('trades').textContent=(s.trades_today||0)+' / 5';
    const age=s.updated_at?Date.now()-Date.parse(s.updated_at):Infinity;
    const fresh=age>=0&&age<10*60*1000;
    if(H('lastsession'))H('lastsession').textContent=s.updated_at?new Date(s.updated_at).toLocaleString('en-IN',{hour12:false}):'—';
    if(H('engine')){H('engine').textContent=fresh?'PAPER ENGINE LIVE':'PAPER ENGINE STALE';H('engine').className='value '+(fresh?'green':'yellow')}
    mark(fresh?'LIVE PAPER · SERVER':'PAPER SERVER STALE',fresh);
    const q=($('search')?.value||'').toUpperCase().replace('/USDT','');
    const visible=list.filter(p=>p.includes(q));
    if(H('coins'))H('coins').innerHTML=visible.map(p=>{
      const z=(window.prices||{})[p]||{}, m=sm[p]||{}, tw=+m.tw||0, ai=m.ai||'NO TRADE';
      return '<tr class="coin" onclick="selectCoin(\''+p+'\')"><td><b>'+n(p)+'</b></td><td>'+f(z.ls||z.mp)+'</td><td class="'+(+(z.pc||0)>=0?'green':'red')+'">'+(+(z.pc||0)).toFixed(2)+'%</td><td><span class="pill '+(tw===1?'buy':tw===-1?'sell':'neutral')+'">'+(tw===1?'BUY':tw===-1?'SELL':'—')+'</span></td><td><span class="pill '+(ai==='LONG'?'buy':ai==='SHORT'?'sell':'neutral')+'">'+(ai==='NO TRADE'?'—':ai)+'</span></td></tr>';
    }).join('');
    const p=s.position;
    if(H('position')){
      if(p){
        const z=(window.prices||{})[p.pair]||{}, px=+(z.ls||z.mp||0), side=p.side==='LONG', up=px?((px-p.entry_price)*p.quantity*(side?1:-1)):0;
        H('position').innerHTML='<div class="bigpos '+(side?'green':'red')+'">'+n(p.pair)+'/USDT · '+p.side+'</div><div class="metricrow"><span>Entry</span><b>'+f(p.entry_price)+'</b></div><div class="metricrow"><span>Current</span><b>'+f(px)+'</b></div><div class="metricrow"><span>TP</span><b>'+f(p.target_price)+'</b></div><div class="metricrow"><span>SL</span><b>'+f(p.stop_price)+'</b></div><div class="metricrow"><span>Open P&amp;L</span><b class="'+(up>=0?'green':'red')+'">₹'+f(up)+'</b></div>';
      }else H('position').innerHTML='<div class="bigpos">No open paper position</div><p class="label">Server-side paper engine is monitoring filtered TW + AI.</p>';
    }
  }
  async function refresh(){
    try{
      let s; try{s=await jf(STATE)}catch(e){s=await jf(RAW_STATE)}
      window.server=s;
      if(Array.isArray(s.pairs))window.pairs=s.pairs.filter(p=>typeof p==='string'&&p.endsWith('_USDT')).slice(0,30);
      renderAuthoritative();
      if(H('chartState'))H('chartState').textContent='Server signal + live CoinDCX REST';
    }catch(e){mark('PAPER STATE ERROR',false)}
    try{
      let j; try{j=await Promise.race([jf(PUB+'/market_data/v3/current_prices/futures/rt'),new Promise((_,r)=>setTimeout(()=>r(Error('timeout')),5000))])}catch(e){j=await jf(RAW_MARKET)}
      window.prices=j.prices||j||{};
      if(H('engine') && window.server?.updated_at){
        const age=Date.now()-Date.parse(window.server.updated_at);
        H('engine').textContent=age<10*60*1000?'PAPER ENGINE LIVE':'PAPER ENGINE STALE';
      }
      renderAuthoritative();
      const p=window.selected||'B-BTC_USDT', now=Math.floor(Date.now()/1000);
      let rows=[];
      try{
        const c=await jf(PUB+'/market_data/candlesticks?pair='+encodeURIComponent(p)+'&from='+(now-260*300-900)+'&to='+now+'&resolution=5&pcode=f');
        rows=(c.data||[]).map(window.norm).filter(Boolean).sort((a,b)=>a.time-b.time).slice(-260);
      }catch(e){}
      if(!rows.length){
        try{const m=await jf(MARKET);rows=(m.candles?.[p]||[]).map(window.norm).filter(Boolean).sort((a,b)=>a.time-b.time).slice(-260)}catch(e){}
      }
      if(rows.length)window.histories[p]=rows;
      if(typeof window.selectedUI==='function')window.selectedUI();
      if(H('chartState'))H('chartState').textContent='Live CoinDCX REST · server signal authoritative';
    }catch(e){
      if(H('chartState'))H('chartState').textContent='Live REST unavailable · showing last server snapshot';
    }
  }
  window.addEventListener('load',()=>{setTimeout(refresh,300);setInterval(refresh,10000)});
})();