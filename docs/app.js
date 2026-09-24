/* AMTE dashboard support layer. Live data rendering is handled by live-hotfix.js. */
(function(){
  function refresh(){ if(typeof window.amteRefresh==='function') window.amteRefresh(); }
  function theme(mode){
    document.body.classList.toggle('dark',mode==='dark');
    try{localStorage.setItem('theme',mode)}catch(e){}
    var light=document.getElementById('lightBtn'), dark=document.getElementById('darkBtn');
    if(light) light.classList.toggle('on',mode!=='dark');
    if(dark) dark.classList.toggle('on',mode==='dark');
  }
  function clock(){
    var d=new Date(), o={timeZone:'Asia/Kolkata'};
    var date=d.toLocaleDateString('en-IN',{weekday:'short',day:'2-digit',month:'short',year:'numeric',...o});
    var time=d.toLocaleTimeString('en-IN',{hour12:false,...o});
    var md=document.getElementById('marketDate'), mc=document.getElementById('marketClock');
    if(md) md.textContent=date;
    if(mc) mc.textContent=time;
  }
  function init(){
    var saved='light';
    try{saved=localStorage.getItem('theme')==='dark'?'dark':'light'}catch(e){}
    theme(saved); clock(); setInterval(clock,1000);
    var light=document.getElementById('lightBtn'), dark=document.getElementById('darkBtn');
    if(light) light.onclick=function(){theme('light')};
    if(dark) dark.onclick=function(){theme('dark')};
    var search=document.getElementById('search');
    if(search) search.addEventListener('input',refresh);
    setTimeout(refresh,600);
  }
  window.addEventListener('load',init);
})();