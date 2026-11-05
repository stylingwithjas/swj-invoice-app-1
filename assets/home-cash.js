(function(){'use strict';let latest=null;
async function load(){try{const r=await fetch('/api/webhook?cash=snapshot',{credentials:'same-origin'});if(!r.ok)throw Error('unavailable');if(r.ok){latest=await r.json();window.SWJCashUnavailable=false;window.SWJCashSnapshot=latest;if(typeof window.buildS6==='function')window.buildS6();if(typeof window.buildS11==='function')window.buildS11()}}catch{window.SWJCashUnavailable=true;if(typeof window.buildS6==='function')window.buildS6();if(typeof window.buildS11==='function')window.buildS11()}}
document.addEventListener('DOMContentLoaded',load);if(document.readyState!=='loading')load();
})();
