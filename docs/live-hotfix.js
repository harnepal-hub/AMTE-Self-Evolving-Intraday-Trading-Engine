/* AMTE live-dashboard watchdog: keeps the UI synchronized with the authoritative paper state. */
(function(){
  const STATE='https://raw.githubusercontent.com/harnepal-hub/AMTE-Self-Evolving-Intraday-Trading-Engine/main/data/paper_live/state.json';
  const H=id=>document.getElementById(id);
  const jf=async u=>{const r=await fetch(u+'?t='+Date.now(),{cache:'no-store'});if(!r.ok)throw Error(r.status);return r.json()};
  const fmt=x=>Number(x||0).toLocaleString('en-IN',{maximumFractionDigits:8});
  async function sync(){
    try{
      const s=await jf(STATE);
      window.server=s;
      const stamp=s.heartbeat_at||s.updated_at, age=stamp?Date.now()-Date.parse(stamp):Infinity;
      const fresh=Number.isFinite(age)&&age<10*60*1000;
      if(H('engine')){H('engine').textContent=fresh?'PAPER ENGINE LIVE':'PAPER ENGINE STALE';H('engine').className='value '+(fresh?'green':'yellow')}
      if(H('engineSub'))H('engineSub').textContent=fresh?'Authoritative server state':'Waiting for a fresh server heartbeat';
      if(H('cash'))H('cash').textContent='₹'+fmt(s.cash??100000);
      if(H('pnl')){H('pnl').textContent='₹'+fmt(s.realized_pnl??0);H('pnl').className='value '+((s.realized_pnl||0)>=0?'green':'red')}
      if(H('trades'))H('trades').textContent=(s.trades_today||0)+' / 5';
      if(H('openPositions'))H('openPositions').textContent=s.position?1:0;
      if(H('dot'))H('dot').className='dot '+(fresh?'ok':'');
      if(H('statusText'))H('statusText').textContent=fresh?'LIVE PAPER · SERVER':'PAPER SERVER STALE';
      if(typeof window.amteRefresh==='function') await window.amteRefresh();
    }catch(e){
      if(H('dot'))H('dot').className='dot';
      if(H('statusText'))H('statusText').textContent='PAPER STATE ERROR';
    }
  }
  window.addEventListener('load',()=>{setTimeout(sync,500);setInterval(sync,30000)});
})();