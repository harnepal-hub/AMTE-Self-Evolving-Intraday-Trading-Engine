const PUB='https://public.coindcx.com';
const WS='https://stream.coindcx.com';
const RAW='https://raw.githubusercontent.com/harnepal-hub/AMTE-Self-Evolving-Intraday-Trading-Engine/main/data/paper_live/state.json';
const $=id=>document.getElementById(id);
const fmt=x=>Number(x||0).toLocaleString('en-IN',{maximumFractionDigits:8});
const CFG={hull:8,ema:200,vol:1.2,atr:1.1,dist:.003};
const TF={1m:{sec:60,bars:160,res:'1'},5m:{sec:300,bars:160,res:'5'},15m:{sec:900,bars:160,res:'15'}};
let pairs=[],prices={},histories={},selected='B-BTC_USDT',server={},lastPriceAt=0,timeframe='5m',socket=null,subscribed='';
let restFailures=0;

async function get(url,params={}){
  const u=new URL(url);Object.entries(params).forEach(([k,v])=>u.searchParams.set(k,v));
  const r=await fetch(u,{cache:'no-store',mode:'cors'});if(!r.ok)throw Error(String(r.status));return r.json();
}
function ema(a,n){if(!a.length)return[];const k=2/(n+1),o=[];let x=a[0];for(const z of a){x=z*k+x*(1-k);o.push(x)}return o}
function ehma(a,n){const h=Math.max(1,Math.floor(n/2)),r=Math.max(1,Math.round(Math.sqrt(n))),a1=ema(a,h),a2=ema(a,n);return ema(a1.map((x,i)=>2*x-a2[i]),r)}
function rsi(a,n=14){if(a.length<=n)return 50;let g=0,l=0;for(let i=1;i<=n;i++){const d=a[i]-a[i-1];g+=Math.max(d,0);l+=Math.max(-d,0)}g/=n;l/=n;for(let i=n+1;i<a.length;i++){const d=a[i]-a[i-1];g=(g*(n-1)+Math.max(d,0))/n;l=(l*(n-1)+Math.max(-d,0))/n}return 100-100/(1+g/(l||1e-12))}
function tw(rows){if(rows.length<210)return 0;const c=rows.map(x=>+x.close),h=ehma(c,CFG.hull),sh=h.map((x,i)=>i>1?h[i-2]:NaN),i=c.length-1;let s=sh[i-1]>=h[i-1]&&sh[i]<h[i]?1:sh[i-1]<=h[i-1]&&sh[i]>h[i]?-1:0;if(!s)return 0;const e=ema(c,CFG.ema).at(-1),v=rows.map(x=>+x.volume),vr=v.at(-1)/(v.slice(-20).reduce((a,b)=>a+b,0)/20||1),tr=rows.map((x,j)=>j?Math.max(+x.high-+x.low,Math.abs(+x.high-+rows[j-1].close),Math.abs(+x.low-+rows[j-1].close)):+x.high-+x.low),atr=tr.slice(-14).reduce((a,b)=>a+b,0)/14,ap=atr/(c.at(-1)||1),prev=tr.slice(-64,-14).map((x,j)=>x/(rows[j+14]?.close||1)),am=prev.reduce((a,b)=>a+b,0)/(prev.length||1);if((s===1&&c.at(-1)<=e)||(s===-1&&c.at(-1)>=e))return 0;if((s===1&&h.at(-1)<=h.at(-2))||(s===-1&&h.at(-1)>=h.at(-2)))return 0;if(vr<CFG.vol||!am||ap/am<CFG.atr)return 0;if(Math.abs(c.at(-1)-e)/c.at(-1)<CFG.dist)return 0;return s}
function ai(rows){if(rows.length<60)return{side:'NO TRADE',score:50};const c=rows.map(x=>+x.close),v=rows.map(x=>+x.volume),last=c.at(-1),e20=ema(c,20).at(-1),e50=ema(c,50).at(-1),rr=rsi(c),vr=v.at(-1)/(v.slice(-20).reduce((a,b)=>a+b,0)/20||1);let s=50;s+=last>e20?12:-12;s+=e20>e50?12:-12;if(rr>50&&rr<70)s+=10;if(rr<50&&rr>30)s-=10;if(vr>1.2)s+=8;return{side:s>=62?'LONG':s<=38?'SHORT':'NO TRADE',score:Math.max(0,Math.min(100,s)),r:rr}}

function normalizeCandle(x){
  const d=x?.data||x||{};
  const t=+(d.t??d.time??0); if(!t)return null;
  return {time:t<1e12?t*1000:t,open:+(d.o??d.open),high:+(d.h??d.high),low:+(d.l??d.low),close:+(d.c??d.close),volume:+(d.v??d.volume)};
}
async function candles(p,tf=timeframe){
  const cfg=TF[tf],now=Math.floor(Date.now()/1000),from=now-cfg.bars*cfg.sec-2*cfg.sec;
  try{
    const j=await get(PUB+'/market_data/candlesticks',{pair:p,from,to:now,resolution:cfg.res,pcode:'f'});
    const rows=(j.data||[]).map(normalizeCandle).filter(x=>x&&x.close>0).sort((a,b)=>a.time-b.time).slice(-cfg.bars);
    if(rows.length>=2){restFailures=0;return rows}
    throw Error('empty candles');
  }catch(e){
    restFailures++;
    return histories[p]||[];
  }
}
function setStatus(label,ok){
  if($('dot'))$('dot').className='dot '+(ok?'ok':'');
  if($('statusText'))$('statusText').textContent=label;
  if($('engine') && label!=='PAPER ENGINE LIVE'){$('engine').textContent=label;$('engine').className='value '+(ok?'green':'yellow')}
  if($('chartState')&&!ok)$('chartState').textContent=label;
}
function renderServer(){
  const s=server||{},p=s.position;
  if($('cash'))$('cash').textContent='₹'+fmt(s.cash??100000);
  if($('pnl')){$('pnl').textContent='₹'+fmt(s.realized_pnl??0);$('pnl').className='value '+((s.realized_pnl||0)>=0?'green':'red')}
  if($('trades'))$('trades').textContent=(s.trades_today??0)+' / 5';
  if($('signals'))$('signals').textContent=s.signals??0;
  if($('pairs'))$('pairs').textContent=Array.isArray(s.pairs)?s.pairs.length:(s.pairs??pairs.length);
  if($('events'))$('events').textContent=s.events??'—';
  if($('psignals'))$('psignals').textContent=s.signals??'—';
  if($('entered'))$('entered').textContent=(s.journal||[]).filter(x=>x.event==='ENTRY').length;
  if($('closed'))$('closed').textContent=(s.journal||[]).filter(x=>x.event==='EXIT').length;
  if($('lastsession'))$('lastsession').textContent=s.updated_at?new Date(s.updated_at).toLocaleString('en-IN',{hour12:false}):'waiting';
  if($('position')){
    if(p){
      const side=p.side==='LONG',px=+(prices[p.pair]?.ls||prices[p.pair]?.mp||0),up=px?(px-p.entry_price)*p.quantity*(side?1:-1):0;
      $('position').innerHTML='<div class="bigpos '+(side?'green':'red')+'">'+p.pair.replace('B-','').replace('_USDT','/USDT')+' · '+p.side+'</div><div class="metricrow"><span>Entry</span><b>'+fmt(p.entry_price)+'</b></div><div class="metricrow"><span>Current</span><b>'+fmt(px)+'</b></div><div class="metricrow"><span>TP</span><b>'+fmt(p.target_price)+'</b></div><div class="metricrow"><span>SL</span><b>'+fmt(p.stop_price)+'</b></div><div class="metricrow"><span>Open P&amp;L</span><b class="'+(up>=0?'green':'red')+'">₹'+fmt(up)+'</b></div><p class="label">Server-side paper engine. Browser can be closed.</p>';
    }else $('position').innerHTML='<div class="bigpos">No open paper position</div><p class="label">Server-side paper engine runs independently of this browser.</p>';
  }
  if(s.status==='LIVE_PAPER'){if($('dot'))$('dot').className='dot ok';if($('statusText'))$('statusText').textContent='PAPER ENGINE LIVE';if($('engine')){$('engine').textContent='PAPER ENGINE LIVE';$('engine').className='value green'}}
}
async function loadServer(){try{server=await get(RAW+'?t='+Date.now());renderServer()}catch(e){if($('statusText'))$('statusText').textContent='PAPER STATE STALE'}}

function renderScanner(){
  const q=($('search')?.value||'').toUpperCase().replace('/USDT','');
  const list=pairs.filter(p=>p.includes(q)).sort((a,b)=>(+(prices[b]?.v||0))-(+(prices[a]?.v||0))).slice(0,30);
  if($('pairs') && !Array.isArray(server?.pairs))$('pairs').textContent=list.length;
  if($('coins'))$('coins').innerHTML=list.map(p=>{
    const r=histories[p]||[],s=tw(r),a=ai(r),z=prices[p]||{};
    return '<tr class="coin" onclick="selectCoin(\''+p+'\')"><td>'+p.replace('B-','').replace('_USDT','')+'</td><td>'+fmt(z.ls||z.mp||r.at(-1)?.close||0)+'</td><td class="'+(+(z.pc||0)>=0?'green':'red')+'">'+(+(z.pc||0)).toFixed(2)+'%</td><td><span class="pill '+(s===1?'buy':s===-1?'sell':'neutral')+'">'+(s===1?'BUY':s===-1?'SELL':'—')+'</span></td><td><span class="pill '+(a.side==='LONG'?'buy':a.side==='SHORT'?'sell':'neutral')+'">'+(a.side==='NO TRADE'?'—':a.side)+'</span></td></tr>';
  }).join('');
}

function draw(rows){
  const cv=$('chart');if(!cv)return;
  const ctx=cv.getContext('2d'),d=devicePixelRatio||1,w=cv.clientWidth,h=cv.clientHeight;
  if(w<10||h<10)return;
  cv.width=w*d;cv.height=h*d;ctx.setTransform(d,0,0,d,0,0);ctx.clearRect(0,0,w,h);
  const r=rows.slice(-100);if(r.length<2){ctx.fillStyle=getComputedStyle(document.body).getPropertyValue('--muted');ctx.font='14px system-ui';ctx.fillText('Waiting for live CoinDCX candle data…',24,40);return}
  const hi=Math.max(...r.map(x=>x.high)),lo=Math.min(...r.map(x=>x.low)),pad={l:36,r:12,t:18,b:24},pw=w-pad.l-pad.r,ph=h-pad.t-pad.b,step=pw/r.length,y=p=>pad.t+(hi-p)/(hi-lo||1)*ph;
  ctx.strokeStyle=getComputedStyle(document.body).getPropertyValue('--line');ctx.lineWidth=1;
  for(let i=0;i<5;i++){const yy=pad.t+i*ph/4;ctx.beginPath();ctx.moveTo(pad.l,yy);ctx.lineTo(w-pad.r,yy);ctx.stroke()}
  ctx.font='10px system-ui';ctx.fillStyle=getComputedStyle(document.body).getPropertyValue('--muted');for(let i=0;i<5;i++){const val=hi-(hi-lo)*i/4;ctx.fillText(fmt(val),2,pad.t+i*ph/4+3)}
  r.forEach((x,i)=>{const xx=pad.l+i*step+step/2,up=x.close>=x.open;ctx.strokeStyle=up?'#059669':'#dc2626';ctx.beginPath();ctx.moveTo(xx,y(x.high));ctx.lineTo(xx,y(x.low));ctx.stroke();ctx.fillStyle=ctx.strokeStyle;const top=y(Math.max(x.open,x.close)),bh=Math.max(1,Math.abs(y(x.open)-y(x.close)));ctx.fillRect(xx-Math.max(1,step*.28),top,Math.max(2,step*.56),bh)});
  const c=r.map(x=>x.close),h1=ehma(c,CFG.hull),e=ema(c,CFG.ema);
  const line=(vals,color,width)=>{ctx.lineWidth=width;ctx.strokeStyle=color;ctx.beginPath();vals.forEach((v,i)=>{const xx=pad.l+i*step+step/2;i?ctx.lineTo(xx,y(v)):ctx.moveTo(xx,y(v))});ctx.stroke()};
  line(h1,'#2563eb',2);line(e,'#b7791f',2);
  const last=r.at(-1);ctx.fillStyle=getComputedStyle(document.body).getPropertyValue('--text');ctx.font='11px system-ui';ctx.fillText('EHMA '+fmt(h1.at(-1)),pad.l,12);ctx.fillText('EMA '+CFG.ema+' '+fmt(e.at(-1)),pad.l+105,12);
}
function selectedUI(){
  const r=histories[selected]||[],z=prices[selected]||{},s=tw(r),a=ai(r),c=r.map(x=>x.close),h=ehma(c,CFG.hull),e=ema(c,CFG.ema);
  if($('selected'))$('selected').textContent=selected.replace('B-','').replace('_USDT','/USDT');
  if($('liveprice'))$('liveprice').textContent=fmt(z.ls||z.mp||c.at(-1)||0);
  if($('snapprice'))$('snapprice').textContent=fmt(z.ls||z.mp||c.at(-1)||0);
  if($('snapchange'))$('snapchange').textContent=(+(z.pc||0)).toFixed(2)+'%';
  if($('tw')){$('tw').textContent=s===1?'BUY':s===-1?'SELL':'NEUTRAL';$('tw').className='bigpos '+(s===1?'green':s===-1?'red':'')}
  if($('ehma'))$('ehma').textContent=h.length?fmt(h.at(-1)):'—';if($('ema'))$('ema').textContent=e.length?fmt(e.at(-1)):'—';if($('rsi'))$('rsi').textContent=r.length?rsi(c).toFixed(1):'—';
  if($('ai')){$('ai').textContent=a.side;$('ai').className='bigpos '+(a.side==='LONG'?'green':a.side==='SHORT'?'red':'')}
  if($('conf'))$('conf').textContent=a.score+'%';if($('score'))$('score').style.width=a.score+'%';
  if($('align'))$('align').textContent=(s===1&&a.side==='LONG')||(s===-1&&a.side==='SHORT')?'AGREE':s===0?'NEUTRAL':'CONFLICT';
  draw(r);
  if($('chartState'))$('chartState').textContent=r.length>=2?'LIVE · '+timeframe:'Waiting for candle history…';
}
function selectCoin(p){selected=p;subscribeCandle();loadHistory(p).then(selectedUI)}
async function loadHistory(p){try{histories[p]=await candles(p,timeframe);renderScanner();if(p===selected)selectedUI()}catch(e){}}

function mergeCandle(raw){
  const x=normalizeCandle(raw);if(!x||x.close<=0)return;
  const ch=x.data?.channel||raw?.channel||'';
  if(ch && !ch.includes(selected+'_'+timeframe))return;
  const arr=histories[selected]||[];const last=arr.at(-1);
  if(last&&last.time===x.time)arr[arr.length-1]=x;else arr.push(x);
  histories[selected]=arr.slice(-TF[timeframe].bars);prices[selected]=Object.assign({},prices[selected],{ls:x.close,mp:x.close});
  lastPriceAt=Date.now();selectedUI();renderScanner();
}
function join(channel){if(socket?.connected)socket.emit('join',{channelName:channel})}
function leave(channel){if(socket?.connected)socket.emit('leave',{channelName:channel})}
function subscribeCandle(){
  const ch=selected+'_'+timeframe+'-futures';
  if(subscribed&&subscribed!==ch)leave(subscribed);
  subscribed=ch;join(ch);
}
function connectWS(){
  try{
    if(typeof io!=='function'){if($('chartState'))$('chartState').textContent='WebSocket library unavailable';return}
    socket=io(WS,{transports:['websocket'],reconnection:true,reconnectionAttempts:Infinity,reconnectionDelay:1000});
    socket.on('connect',()=>{join('currentPrices@futures@rt');subscribeCandle();if($('chartState'))$('chartState').textContent='LIVE · '+timeframe});
    socket.on('disconnect',()=>{if($('chartState'))$('chartState').textContent='Reconnecting to CoinDCX…'});
    socket.on('connect_error',()=>{if($('chartState'))$('chartState').textContent='REST fallback · reconnecting…'});
    socket.on('currentPrices@futures#update',msg=>{
      const d=msg?.data||msg||{};if(d.pr!=='futures'&&d.pr!=='f')return;
      Object.assign(prices,d.prices||{});lastPriceAt=Date.now();renderScanner();selectedUI();
    });
    socket.on('candlestick',msg=>mergeCandle(msg));
  }catch(e){if($('chartState'))$('chartState').textContent='WebSocket error · REST fallback'}
}

function marketFromServer(){
  const pp=Array.isArray(server?.pairs)?server.pairs.filter(p=>typeof p==='string'&&p.endsWith('_USDT')):[];
  if(pp.length){pairs=pp;renderScanner();return true}
  return false;
}
async function market(){
  try{
    const cp=await get(PUB+'/market_data/v3/current_prices/futures/rt');
    prices=cp.prices||{};lastPriceAt=Date.now();
    if(!pairs.length)pairs=Object.keys(prices).filter(p=>p.endsWith('_USDT')).sort((a,b)=>(+(prices[b]?.v||0))-(+(prices[a]?.v||0))).slice(0,30);
    renderScanner();selectedUI();
  }catch(e){
    if(!marketFromServer() && $('chartState'))$('chartState').textContent='Waiting for CoinDCX market feed…';
  }
}

function setTimeframe(tf){
  if(!TF[tf])return;timeframe=tf;
  document.querySelectorAll('[data-tf]').forEach(b=>b.classList.toggle('on',b.dataset.tf===tf));
  histories[selected]=[];
  subscribeCandle();loadHistory(selected).then(selectedUI);
}
function updateClock(){
  const d=new Date(),o={timeZone:'Asia/Kolkata'};
  if($('dateNow'))$('dateNow').textContent=d.toLocaleDateString('en-IN',{day:'2-digit',month:'short',year:'numeric',...o})+' · '+d.toLocaleTimeString('en-IN',{hour12:false,...o})+' IST';
  if($('marketDate'))$('marketDate').textContent=d.toLocaleDateString('en-IN',{weekday:'short',day:'2-digit',month:'short',year:'numeric',...o});
  if($('marketClock'))$('marketClock').textContent=d.toLocaleTimeString('en-IN',{hour12:false,...o});
  if($('dataAge')){const age=lastPriceAt?Math.floor((Date.now()-lastPriceAt)/1000):-1;$('dataAge').textContent=age<0?'waiting':age<5?'live':age+'s'}
}
function theme(){document.body.classList.toggle('dark',localStorage.theme==='dark')}
function init(){
  theme();if($('theme'))$('theme').onclick=()=>{localStorage.theme=document.body.classList.contains('dark')?'light':'dark';theme();selectedUI()};
  document.querySelectorAll('[data-tf]').forEach(b=>b.onclick=()=>setTimeframe(b.dataset.tf));
  if($('search'))$('search').oninput=renderScanner;
  updateClock();setInterval(updateClock,1000);
  connectWS();loadServer().then(()=>{marketFromServer();market();loadHistory(selected).then(selectedUI)});
  setInterval(loadServer,10000);setInterval(market,15000);setInterval(()=>{if(selected)loadHistory(selected)},30000);
  window.addEventListener('resize',selectedUI);
}
init();
