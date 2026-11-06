/* Actual page code exercised offline; all API/email/payment requests are fixtures. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const {Window}=await import(process.env.SWJ_HAPPY_DOM || 'happy-dom');
function page(file,query='',fetcher){
 const w=new Window({url:'https://preview.example.test/'+file+query,settings:{disableCSSFileLoading:true,disableJavaScriptFileLoading:true,disableJavaScriptEvaluation:true}});
 const html=fs.readFileSync(file,'utf8');
 w.document.write(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,''));
 const context=vm.createContext(w);w.eval=code=>vm.runInContext(code,context);
 w.fetch=fetcher;w.alert=message=>{throw new Error('Unexpected alert: '+message);};w.scrollTo=()=>{};
 w.HTMLCanvasElement.prototype.getContext=()=>({fillRect(){},clearRect(){},fillText(){},measureText(){return{width:100};}});
 w.HTMLCanvasElement.prototype.toDataURL=()=> 'data:image/png;base64,fixture';
 for(const m of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi))if(m[1].trim())w.eval(m[1]);
 w.eval("if(typeof loadProposal==='function')document.removeEventListener('DOMContentLoaded',loadProposal);if(typeof load==='function')document.removeEventListener('DOMContentLoaded',load);");
 return w;
}
const invoice={invnum:'2020132',client:'Fixture Client',address:'Synthetic property',invdate:'2026-11-05',data:{client:'Fixture Client',base_price:2650,taxrate:10.55,baserooms:'Living Room',startdate:'TBD',signed:false,paid:false,payment_url:'https://buy.stripe.com/fixture',signatures:[],addons:[]}};
const calls=[];
let w=page('proposal.html','?inv=2020132&role=client&token=fixture-token',async(url,options)=>{
 const body=JSON.parse(options.body);calls.push({url,body});
 if(url.includes('public=invoice'))return {ok:true,json:async()=>({invoice})};
 assert.equal(url,'/api/sign');return {ok:true,json:async()=>({signed:true,fully_signed:true,contract_url:'https://fixture.example.test/signed.pdf'})};
});
await w.eval('loadProposal()');
assert.equal(w.document.querySelectorAll('input[type=password]').length,0);
w.eval('switchToType()');w.document.getElementById('sig-name-typed').value='Fixture Client';w.document.getElementById('sig-agree').checked=true;
await w.eval('submitSignature()');
assert.equal(calls[1].body.token,'fixture-token');assert.equal(calls[1].body.role,'client');assert.equal(calls[1].body.invnum,'2020132');
assert.match(w.document.getElementById('main-content').textContent,/Thank you/);
assert.ok(w.document.querySelector('a[href="https://buy.stripe.com/fixture"]'));
await w.happyDOM.close();
w=page('proposal.html','?inv=2020132',async()=>({ok:false,json:async()=>({error:'Please ask Jasmine for a new private approval link.'})}));
await w.eval('loadProposal()');assert.equal(w.document.querySelector('#sign-btn'),null);assert.match(w.document.body.textContent,/new private approval link/);await w.happyDOM.close();
const crew=[];
w=page('confirm.html','?inv=2020132&role=mover1&token=crew-token',async(url,options)=>{
 const body=JSON.parse(options.body);crew.push({url,body});
 return {ok:true,json:async()=>({invoice:{invnum:'2020132',address:'Synthetic property',data:{mover_name:'Fixture Crew',startdate:'2026-11-10',confirmations:{mover1:url.includes('respond')?{status:body.status}:{}}}},notified:true})};
});
await w.eval('load()');await w.eval("respond('confirmed')");
assert.deepEqual(crew[1].body,{invnum:'2020132',role:'mover1',status:'confirmed',token:'crew-token'});
assert.match(w.document.body.textContent,/You're confirmed/);await w.happyDOM.close();
// Cold login preserves all page nodes while blocking interaction beneath the gate.
w=new Window({url:'https://preview.example.test/approve-hours.html',settings:{disableJavaScriptEvaluation:true}});
w.document.write('<main id="main-content"><input id="existing-control"></main>');
w.fetch=async()=>({json:async()=>({configured:true,authenticated:false})});
const context=vm.createContext(w);vm.runInContext(fs.readFileSync('admin-auth.js','utf8'),context);
w.document.dispatchEvent(new w.Event('DOMContentLoaded'));await w.swjOwnerReady;
assert.ok(w.document.getElementById('existing-control'));assert.equal(w.document.getElementById('main-content').inert,true);assert.ok(w.document.getElementById('swj-auth-pw'));await w.happyDOM.close();
// Prefetched crew links preserve the synchronous SMS click path; no unsafe fallback.
w=new Window({url:'https://preview.example.test/board.html'});w.swjOwnerReady=Promise.resolve(true);
let minted=0;w.fetch=async()=>{minted++;return{ok:true,json:async()=>({url:'https://preview.example.test/confirm.html?inv=2020132&role=mover1&token=private'})};};
vm.runInContext(fs.readFileSync('assets/swj-private-links.js','utf8'),vm.createContext(w));
await Promise.all([w.swjPrivateLink('2020132','mover1','confirm'),w.swjPrivateLink('2020132','mover1','confirm')]);
assert.equal(minted,1);assert.match(w.swjCrewLink('2020132','mover1'),/token=private/);await w.happyDOM.close();
console.log('PASS offline DOM: private proposal signing without login, unchanged Stripe URL, legacy-link rejection, crew reply, login DOM retention and SMS link prefetch');
