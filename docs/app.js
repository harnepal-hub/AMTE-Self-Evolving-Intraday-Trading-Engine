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
  function init(){
    var saved='light';
    try{saved=localStorage.getItem('theme')==='dark'?'dark':'light'}catch(e){}
    theme(saved);
    var light=document.getElementById('lightBtn'), dark=document.getElementById('darkBtn');
    if(light) light.onclick=function(){theme('light')};
    if(dark) dark.onclick=function(){theme('dark')};
    var search=document.getElementById('search');
    if(search) search.addEventListener('input',refresh);
    setTimeout(refresh,600);
  }
  window.addEventListener('load',init);
})();