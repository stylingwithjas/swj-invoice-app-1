import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {webcrypto} from 'node:crypto';
const {Window}=await import(process.env.SWJ_HAPPY_DOM||'happy-dom');
let next=2020132;const ids=new Map(),used=new Map(),requests=[];
async function api(url,options){
 assert.equal(url,'/api/counter');const body=JSON.parse(options.body);requests.push(body);
 if(ids.has(body.request_id))return{ok:true,json:async()=>ids.get(body.request_id)};
 const invnum=body.requested_invnum||String(next++);
 if(used.has(invnum))return{ok:false,json:async()=>({error:'Number occupied'})};
 const result={invnum,request_id:body.request_id,mode:'allocated',proof:'fixture-'+body.request_id};ids.set(body.request_id,result);used.set(invnum,true);return{ok:true,json:async()=>result};
}
function windowWith(fetcher=api){
 const w=new Window({url:'https://fixture.test/index.html',settings:{disableJavaScriptEvaluation:true}});
 w.document.write('<input id="invnum-display" value=""><span id="invnum-note"></span>');
 Object.defineProperty(w,'crypto',{value:webcrypto});w.fetch=fetcher;w.swjOwnerReady=Promise.resolve(true);
 vm.runInContext(fs.readFileSync('assets/invoice-number.js','utf8'),vm.createContext(w));return w;
}
const one=windowWith(),two=windowWith();await Promise.all([one.SWJInvoiceNumber.initialize(),two.SWJInvoiceNumber.initialize()]);
assert.notEqual(one.document.getElementById('invnum-display').value,two.document.getElementById('invnum-display').value);
assert.notEqual(requests[0].request_id,requests[1].request_id);
const racing=windowWith();const raceStart=requests.length;await Promise.all([racing.SWJInvoiceNumber.ensure(),racing.SWJInvoiceNumber.initialize()]);assert.equal(requests.length-raceStart,1);assert.equal(racing.SWJInvoiceNumber.context().invnum,racing.document.getElementById('invnum-display').value);
const count=requests.length;await Promise.all([one.SWJInvoiceNumber.ensure(),one.SWJInvoiceNumber.ensure()]);assert.equal(requests.length,count);
const held=one.SWJInvoiceNumber.context();assert.equal(one.SWJInvoiceNumber.attach({invnum:held.invnum}).invoice_number.proof,held.proof);
one.document.getElementById('invnum-display').value=two.document.getElementById('invnum-display').value;assert.equal(await one.SWJInvoiceNumber.ensure(),false,'manual number collision cannot issue another invoice');
// Opening existing records while initialization is pending cannot overwrite their number.
let release,lateId;const late=windowWith((url,options)=>new Promise(resolve=>{lateId=JSON.parse(options.body).request_id;release=resolve;}));const initialization=late.SWJInvoiceNumber.initialize();await new Promise(resolve=>setImmediate(resolve));
late.document.getElementById('invnum-display').value='2020100';late.SWJInvoiceNumber.loaded({invnum:'2020100'});
release({ok:true,json:async()=>({request_id:lateId,invnum:'2020999',proof:'fixture'})});await initialization;
assert.equal(late.document.getElementById('invnum-display').value,'2020100');assert.equal(late.SWJInvoiceNumber.context().mode,'existing');
late.SWJInvoiceNumber.loaded({invnum:'2020100',_is_draft:true},{draft:false});assert.equal(late.SWJInvoiceNumber.context().mode,'existing');
// A dropped response retries the SAME request UUID, never a second allocation.
let lost=true;const retryIds=[];const retry=windowWith(async(url,options)=>{const body=JSON.parse(options.body);retryIds.push(body.request_id);if(lost){lost=false;throw Error('Lost response');}return api(url,options);});await retry.SWJInvoiceNumber.initialize();assert.equal(retryIds[0],retryIds[1]);
const failed=windowWith(async()=>({ok:false,json:async()=>({error:'Reservation setup unavailable'})}));await failed.SWJInvoiceNumber.initialize();assert.equal(failed.document.getElementById('invnum-display').value,'');assert.equal(await failed.SWJInvoiceNumber.ensure(),false);
for(const w of [one,two,late,retry,failed,racing])await w.happyDOM.close();
console.log('PASS number DOM: distinct tabs, duplicate-click/retry identity, manual collision denial, late response protection, existing-number preservation and no fallback');
