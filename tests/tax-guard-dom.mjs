import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const {Window}=await import(process.env.SWJ_HAPPY_DOM||'happy-dom');
const w=new Window({url:'https://fixture.test/index.html',settings:{disableJavaScriptEvaluation:true}});
w.document.write('<div><input id="address" value="600 4th Ave"><input id="city" value="Seattle"><input id="zipcode" value="98104"><input id="invdate" value="2026-11-05"><input id="taxrate" value="10.25"><input id="invnum-display" value="2020132"><span id="tax-note"></span></div>');
const timers=new Map();let timerId=0,calls=[];
w.setTimeout=(fn,ms)=>{timers.set(++timerId,{fn,ms});return timerId;};w.clearTimeout=id=>timers.delete(id);
w.AbortController=AbortController;w.URLSearchParams=URLSearchParams;
w.recalc=()=>{};let payload={manualFields:[],rental_service_split:true,rental_total:1325,ovSub:2650,ovTax:139.79,grand:2789.79};
w.buildPricingPayload=()=>payload;
let response={ok:true,rate:10.55,addressMatch:true,needsConfirmation:false,period:'Q42026',locationCode:'1726',matchedAddress:'600 4TH AVE SEATTLE WA 98104',verification:{proof:'fixture-proof',expiresAt:Date.now()/1000+3600}};
w.fetch=async url=>{calls.push(url);return{ok:true,json:async()=>response};};
vm.runInContext(fs.readFileSync('assets/tax-guard.js','utf8'),vm.createContext(w));
const tick=async()=>{const entry=[...timers].find(([,t])=>t.ms===500);if(entry){timers.delete(entry[0]);return await entry[1].fn();}};
const guard=w.SWJTaxGuard;
assert.equal(guard.allowed('generating',payload),false);assert.equal(guard.allowed('saving a draft',payload),true);
guard.lookup();assert.equal(guard.allowed('generating',payload),false);await tick();
assert.match(calls[0],/city=Seattle/);assert.match(calls[0],/date=2026-11-05/);
assert.equal(w.document.getElementById('taxrate').value,'10.55');assert.equal(guard.allowed('generating',payload),true);
// A client-visible PDF must match the verified snapshot at send time.
const artifact={invnum:'2020132',total_override:2789.79,tax_override:139.79,tax_verification:guard.provenance()};
assert.equal(guard.artifactAllowed(artifact,payload),true);assert.equal(guard.artifactAllowed({...artifact,total_override:2000},payload),false);
// Editing then clearing the ZIP invalidates the pending result before early return.
let resolve;w.fetch=url=>{calls.push(url);return new Promise(r=>{resolve=r;});};
guard.lookup();const pending=tick();w.document.getElementById('zipcode').value='981';guard.lookup();
resolve({ok:true,json:async()=>({...response,rate:9.6})});await pending;
assert.equal(w.document.getElementById('taxrate').value,'10.55');assert.equal(guard.allowed('generating',payload),false);
// ZIP fallback and failed lookup do not authorize the previously displayed rate.
w.document.getElementById('zipcode').value='98104';w.fetch=async()=>({ok:true,json:async()=>({ok:true,rate:9.6,addressMatch:false})});guard.lookup();await tick();assert.equal(guard.allowed('generating',payload),false);
w.fetch=async()=>{throw Error('fixture offline');};guard.lookup();await tick();assert.equal(guard.allowed('generating',payload),false);
// Address-updated result requires an explicit match confirmation; manual tax differences require review.
response={...response,needsConfirmation:true};w.fetch=async()=>({ok:true,json:async()=>response});guard.lookup();await tick();assert.equal(guard.allowed('generating',payload),false);
w.document.getElementById('tax-match-confirm').checked=true;w.document.getElementById('tax-match-confirm').dispatchEvent(new w.Event('change'));
assert.equal(guard.allowed('generating',payload),true);
payload={...payload,manualFields:['tax'],ovTax:100,grand:2750};assert.equal(guard.allowed('generating',payload),false);
w.document.getElementById('tax-manual-reason').value='Fixture documented tax exception';w.document.getElementById('tax-manual-confirm').checked=true;w.document.getElementById('tax-manual-confirm').dispatchEvent(new w.Event('change'));
assert.equal(guard.allowed('generating',payload),true);
payload={...payload,rental_total:1400};assert.equal(guard.allowed('generating',payload),false,'changing pricing invalidates the manual review');
// City-only changes also invalidate evidence even if input event delivery is delayed.
w.document.getElementById('city').value='Tacoma';assert.equal(guard.allowed('generating',payload),false);
await w.happyDOM.close();
console.log('PASS tax DOM: pending/stale/failed/ZIP-only denial, city/date forwarding, corrected-address confirmation, manual-tax review and PDF snapshot binding');
