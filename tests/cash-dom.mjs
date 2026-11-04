/* Offline cash UI smoke check. Synthetic fixture; no bank or production requests. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
const {Window}=await import(process.env.SWJ_HAPPY_DOM || 'happy-dom');
const root=path.resolve(import.meta.dirname,'..');
const fixture=JSON.parse(fs.readFileSync(path.join(root,'tests/fixtures/cash-snapshot.json')));
fixture.bank_transactions=[{transaction_id:'synthetic-venmo',date:'2026-11-03',amount:75,pending:false,name:'Venmo payment',fingerprint:'synthetic-fingerprint',review:null}];
fixture.jobs_options=[{invnum:'JOB-A',client:'Job A'},{invnum:'JOB-B',client:'Job B'}];
const w=new Window({url:'https://preview.example.test/cash.html',settings:{disableCSSFileLoading:true,disableJavaScriptFileLoading:true,disableJavaScriptEvaluation:true}});
w.document.write(fs.readFileSync(path.join(root,'cash.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,''));
const calls=[];w.fetch=async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>structuredClone(fixture)}};
vm.runInContext(fs.readFileSync(path.join(root,'assets/cash.js'),'utf8'),vm.createContext(w));
await new Promise(resolve=>setTimeout(resolve,20));
assert.match(w.document.getElementById('dashboard').textContent,/\$1,000/);
assert.match(w.document.getElementById('dashboard').textContent,/\$650/);
assert.deepEqual([...w.document.querySelectorAll('nav a')].map(x=>x.textContent),['Home','Invoice','Finance','Board','More']);
assert.equal(calls.length,1);assert.equal(calls[0].options.method,'GET');
assert.deepEqual(JSON.parse(JSON.stringify(w.SWJCashCSV.parse('a,b\n"with, comma","line\nsecond"'))),[['a','b'],['with, comma','line\nsecond']]);
const form=w.document.querySelector('.bank-mover-form');assert.ok(form);
form.querySelector('.add-allocation').click();
const rows=form.querySelectorAll('.bank-mover-allocation');assert.equal(rows.length,2);
for(const [i,row] of [...rows].entries()){
 row.querySelector('[name=alloc_name]').value='Mover '+i;
 row.querySelector('[name=alloc_job]').value=i?'JOB-B':'JOB-A';
 row.querySelector('[name=alloc_amount]').value=i?'50.00':'25.00';
}
form.elements.note.value='Confirmed paid movers from this debit';form.elements.confirmed_payment.checked=true;
form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
await new Promise(resolve=>setTimeout(resolve,20));
const writes=calls.filter(c=>c.options.method==='POST');assert.equal(writes.length,1);
assert.match(writes[0].url,/cash=bank_movers/);
const request=JSON.parse(writes[0].options.body);
assert.equal(request.confirmed_payment,true);assert.equal(request.allocations.length,2);
assert.equal(request.allocations.reduce((n,r)=>n+Number(r.amount),0),75);
assert.equal(request.transaction_id,'synthetic-venmo');
console.log('PASS cash UI carryover, CSV, navigation and checking-to-mover/job split submission; synthetic only');
await w.happyDOM.close();
