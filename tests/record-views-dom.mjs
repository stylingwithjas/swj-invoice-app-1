import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const {Window}=await import(process.env.SWJ_HAPPY_DOM||'happy-dom');
function functionCode(file,name){const s=fs.readFileSync(file,'utf8');const start=s.indexOf('async function '+name+'(');return s.slice(start,s.indexOf('\n}',start)+2);}
const w=new Window({url:'https://fixture.test/payroll.html',settings:{disableJavaScriptEvaluation:true}});
w.document.write('<input id="pr-spend-month" value="2026-11"><input id="pr-spend-job-filter" value="JOB"><input id="pr-spend-show-pending" type="checkbox"><div id="pr-spend-body"></div>');
const context=vm.createContext(w);vm.runInContext(fs.readFileSync('assets/records.js','utf8'),context);
w._spendingLoadSeq=0;w.esc=s=>String(s||'');w.PLAID_CATEGORIES=[];w.jobDropdownOptions=()=>'';
const rows=Array.from({length:1201},(_,i)=>({transaction_id:'tx'+String(i).padStart(5,'0'),amount:1,date:'2026-11-04',job_invnum:'JOB',name:'Fixture'}));
let fail=false;let filtered=0;
w._sb={from:table=>{assert.equal(table,'plaid_transactions');let min,max,job;
 const q=new Proxy({}, {get:(_,name)=>{
  if(name==='range')return async(start,end)=>{if(fail&&start>0)return {error:{message:'fixture later page failure'}};assert.equal(job,'JOB');assert.equal(min,'2026-11-01');assert.equal(max,'2026-11-30');filtered++;return{data:rows.slice(start,Math.min(end+1,start+125)),count:rows.length};};
  return(...args)=>{if(name==='gte')min=args[1];if(name==='lte')max=args[1];if(name==='eq')job=args[1];if(name==='select')assert.equal(args[1].count,'exact');return q;};
 }});return q;}};
vm.runInContext(functionCode('payroll.html','renderSpendingTransactions'),context);await w.renderSpendingTransactions();
assert.equal(w.document.querySelectorAll('#pr-spend-body tbody tr').length,1201);assert.match(w.document.querySelector('#pr-spend-body tfoot').textContent,/1201 transaction/);assert.match(w.document.querySelector('#pr-spend-body tfoot').textContent,/1,201\.00/);assert.ok(filtered>2);
rows.push({transaction_id:'pending',amount:999,date:'2026-11-04',job_invnum:'JOB',name:'Authorization fixture',pending:true});
await w.renderSpendingTransactions();
assert.equal(w.document.querySelectorAll('#pr-spend-body tbody tr').length,1201);
assert.match(w.document.querySelector('#pr-spend-body tfoot').textContent,/Posted total.*1,201\.00/);
w.document.getElementById('pr-spend-show-pending').checked=true;
await w.renderSpendingTransactions();
assert.equal(w.document.querySelectorAll('#pr-spend-body tbody tr').length,1202);
assert.match(w.document.querySelector('#pr-spend-body tbody').textContent,/Authorization fixture pending/);
assert.match(w.document.querySelector('#pr-spend-body tfoot').textContent,/Posted total.*1,201\.00/);
fail=true;await w.renderSpendingTransactions();assert.equal(w.document.querySelector('#pr-spend-body tfoot'),null);assert.match(w.document.getElementById('pr-spend-body').textContent,/Could not load/);
// Board failure clears old cards and cannot run its stage transitions on partial data.
w.document.body.innerHTML='<div id="swj-jobs-mobile"></div>'+['proposal','confirmed','staged','destage','done'].map(s=>'<div id="col-body-'+s+'"></div>').join('');
w._boardLoadSeq=0;w.CARDS={stale:{}};let advances=0;w.autoAdvanceStages=()=>advances++;
w._sb={from:()=>{const q=new Proxy({}, {get:(_,name)=>name==='range'?async()=>({error:{message:'fixture incomplete'}}):()=>q});return q;}};
vm.runInContext(functionCode('board.html','loadBoard'),context);await w.loadBoard();assert.equal(Object.keys(w.CARDS).length,0);assert.equal(advances,0);assert.match(w.document.getElementById('board-record-warning').textContent,/incomplete/);
// Home incomplete source does not render zero totals from an empty fallback array.
w.document.body.innerHTML='<div id="s1-body">old</div><div id="s6-body">$999</div>';
w._homeLoadSeq=0;w.showSkeletons=()=>{};w.viewDate=new Date();w.toISODate=()=> '2026-11-04';w.allInvoices=[{invnum:'old'}];w.activeInvoices=w.allInvoices;w.allTasks=[];
w.loadMonthlyFinanceData=()=>{throw Error('Must not compute from incomplete invoices');};
vm.runInContext(functionCode('today.html','loadAll'),context);await w.loadAll();assert.equal(w.allInvoices.length,0);assert.match(w.document.getElementById('s6-body').textContent,/incomplete/);assert.doesNotMatch(w.document.getElementById('s6-body').textContent,/\$0/);
await w.happyDOM.close();console.log('PASS actual views: Spending 1,201 filtered rows/total, later-page failure hides totals, Board clears partial cards before transitions, Home rejects partial metrics');
