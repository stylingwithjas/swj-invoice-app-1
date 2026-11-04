/* Offline DOM regression of the actual inline application code. No network allowed. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
const {Window}=await import(process.env.SWJ_HAPPY_DOM || 'happy-dom');
const root=path.resolve(import.meta.dirname,'..');
const mock=fs.readFileSync(path.join(root,'tests/pr63-browser.cjs'),'utf8').match(/const mock=`([\s\S]*?)`;/)[1];
function app(file){
 const window=new Window({url:'https://preview.example.test/'+file,settings:{disableCSSFileLoading:true,disableJavaScriptFileLoading:true,disableJavaScriptEvaluation:true}});
 window.document.write(fs.readFileSync(path.join(root,file),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,''));
 const context=vm.createContext(window);window.eval=code=>vm.runInContext(code,context);
 window.eval(mock);window.alerts=[];window.alert=message=>window.alerts.push(message);window.confirm=()=>true;window.scrollTo=()=>{};
 window.fetch=()=>{throw Error('Unexpected network call')};
 window.eval(fs.readFileSync(path.join(root,'assets/invoice-safety.js'),'utf8'));
 window.eval(fs.readFileSync(path.join(root,'assets/payment-events.js'),'utf8'));
 for(const m of fs.readFileSync(path.join(root,file),'utf8').matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi))if(m[1].trim())window.eval(m[1]);
 return window;
}
for(const file of ['index.html','luxury.html']){
 const w=app(file),run=code=>w.eval(code),get=code=>JSON.parse(JSON.stringify(run(code)));
 assert.equal(w.document.getElementById('invoice-save').textContent,'SAVE');
 run(`genShowTab('drafts');loadInvoiceData({invnum:'TEST-NAV',client:'Saved client',invdate:'2026-01-15',client_email:'saved@example.test',base_price:2500,addons:[]})`);
 assert.ok(w.document.getElementById('gen-panel-create').classList.contains('on'));
 assert.equal(w.document.getElementById('client_email').value,'saved@example.test');
 assert.equal(w.document.getElementById('invdate').value,'2026-01-15');
 run(`_loadedInvoiceData=null;document.getElementById('client').value='Email test';document.getElementById('client_email').value='saved@example.test';recalc()`);
 run(`window.__originalFrom=_sb.from;_sb.from=()=>({upsert:async row=>{window.__savedDraft=row;return {error:null}}})`);
 await run('saveDraft(true)');
 assert.equal(run('window.__savedDraft.data.client_email'),'saved@example.test');
 run('_sb.from=window.__originalFrom');
 run('window.__dbWrites=[]');

 run(`document.getElementById('base_price').value='3933';document.getElementById('taxrate').value='10.55';document.getElementById('rental-term-days').value='45';customs=[{desc:'Logistics & Handling',price:185,scope:''}];selRooms.add('Living Room');recalc();`);
 assert.deepEqual(get(`(()=>{const p=buildPricingPayload();return [p.ovSub,p.ovTax,p.grand,p.rental_total,p.service_total,p.reconcile]})()`),[4118,226.98,4344.98,2151.5,1966.5,[]]);
 run(`customs.push({desc:'Additional adjustment visit',price:250,scope:''});recalc()`);
 assert.deepEqual(get(`(()=>{const p=buildPricingPayload();return [p.ovTax,p.grand,p.rental_total,p.service_total]})()`),[226.98,4594.98,2151.5,2216.5]);
 run(`document.getElementById('ov-total').value='4604.98';recalc()`);
 assert.equal(run('buildPricingPayload().ovTax'),226.98);assert.equal(run('buildPricingPayload().blocked'),true);assert.equal(run('buildPricingPayload().rental_service_split'),true);
 for(const fn of ['saveDraft(false)','downloadPdfNow()','generatePDF()','generate()','generateAndEmail()','sendEmail()'])await run(fn);
 assert.match(w.document.getElementById('invoice-safety').textContent,/grand total/);
 run(`clearManualTotals();customs.pop();window.fixture=(()=>{const p=buildPricingPayload();return {invnum:'TEST-PR63',client:'Preview Test Only',address:'Synthetic property',invdate:'2026-11-02',startdate:'TBD',taxrate:10.55,base_price:p.base,addons:p.allAddons,subtotal_override:p.ovSub,tax_override:p.ovTax,total_override:p.grand,manual_totals_fields:[],state:captureState()}})();loadInvoiceData(window.fixture);extraBedrooms.push({name:'Test Bedroom',bed:'Queen',price:200,scope:'Test room'});recalc()`);
 assert.equal(run('buildPricingPayload().ovSub'),4318);assert.equal(w.document.getElementById('ov-subtotal').value,'');
 run(`window.legacy={...window.fixture};delete window.legacy.manual_totals_fields;loadInvoiceData(window.legacy);customs.push({desc:'Additional adjustment visit',price:15,scope:''});recalc()`);
 assert.equal(run('buildPricingPayload().ovSub'),4118);assert.match(w.document.getElementById('invoice-safety').textContent,/Manual totals active/);assert.match(w.document.getElementById('invoice-safety').textContent,/4133/);
 run('clearManualTotals()');assert.equal(run('buildPricingPayload().ovSub'),4133);
 run(`document.getElementById('rental-term-days').value='0';recalc()`);assert.ok(run('buildPricingPayload().termError'));assert.equal(w.document.getElementById('rental-term-days').value,'0');
 run(`document.getElementById('rental-term-days').value='90';recalc()`);assert.equal(run('buildPricingPayload().base_split.service'),0);assert.ok(run('buildPricingPayload().termNote'));
 const warnings=get(`SWJInvoiceSafety.reconcile({linesSum:100,sub:100,tax:8,grand:108,extrate:null,taxableBase:50,rate:10,rawOvTax:null})`);assert.equal(warnings.length,1);assert.match(warnings[0],/Tax/);
 assert.equal(run(`SWJInvoiceSafety.reconcile({linesSum:100,sub:100,tax:8,grand:108,extrate:null,taxableBase:50,rate:10,rawOvTax:8}).length`),0);
 run(`_loadedInvoiceData=null;clearManualTotals();document.getElementById('base_price').value='4133';document.getElementById('taxrate').value='9.6';document.getElementById('rental-term-days').value='45';selAddons.clear();extraBedrooms=[];if(typeof extraAreas!=='undefined')extraAreas=[];customs=[{desc:'Logistics & Handling',price:535,scope:''}];document.getElementById('ov-total').value='4927.74';recalc()`);
 assert.equal(run('buildPricingPayload().ovSub'),4668);assert.equal(run('buildPricingPayload().ovTax'),249.74);assert.equal(run('buildPricingPayload().grand'),4927.74);assert.equal(run('buildPricingPayload().blocked'),true);assert.equal(run('buildPricingPayload().rental_service_split'),true);
 run(`loadInvoiceData({...window.fixture,paid:true})`);assert.match(w.document.getElementById('invoice-safety').textContent,/Paid invoice protected/);
 for(const fn of ['saveDraft(false)','downloadPdfNow()','generatePDF()','generate()','saveInvoice(window.fixture)'])await run(fn);
 assert.equal(run('window.__dbWrites.length'),0);
 console.log('PASS',file,'actual app DOM, adjustment, manual/frozen totals, all output guards, paid lock, term and tax checks');
 await w.happyDOM.close();
}
// Definition regression: execute Finance and Tax Report's real payment readers.
{
 const w=app('payroll.html'),run=code=>w.eval(code);
 const fixture=[['2026-10-15',3978],['2026-10-01',3527.28],['2026-10-31',4111.60],['2026-08-10',6888.24]].map(([date,amount],i)=>({invnum:'TEST-'+i,client:'Synthetic client '+i,data:{paid:true,paid_date:date,amount_paid:amount,base_price:100,taxrate:10,addons:[],signed:i===0}}));
 fixture.push({invnum:'TEST-UNSIGNED',client:'Unsigned synthetic test',data:{paid:false,signed:false,base_price:100,taxrate:10,addons:[]}});
 w.__dbRows.invoices=fixture;
 run(`document.getElementById('fin-month').innerHTML='<option value="10">October</option>';document.getElementById('fin-year').innerHTML='<option value="2026">2026</option>'`);
 await run('runFinanceView()');
 assert.equal(Math.round(run('_lastFinanceSummary.revenue')*100)/100,11616.88);
 assert.match(w.document.getElementById('finance-data-status').textContent,/11,616.88/);
 assert.ok(!w.document.getElementById('fin-details-body').textContent.includes('Signed, unpaid'));
 // Job results consume actual payments from the authenticated cash ledger, not hours.
 w.fetch=async()=>({ok:true,json:async()=>({revenue_source:'Recorded paid invoices',monthly:{shared_costs_cents:2000,issues:[]},monthly_jobs:[{invnum:'TEST-EXT',client:'Paid extension',receipts_cents:7500,recorded_cost_cents:2500,contribution_cents:5000}]})});
 run(`document.getElementById('pr-ledger-month').value='2026-11'`);
 await run('renderRealLedger()');
 assert.match(w.document.getElementById('pr-ledger-body').textContent,/Paid extension/);
 assert.match(w.document.getElementById('pr-ledger-body').textContent,/75/);
 assert.match(w.document.getElementById('pr-ledger-body').textContent,/50/);
 assert.match(w.document.getElementById('pr-ledger-body').textContent,/not net profit/);
 w.fetch=async()=>({ok:false});await run('renderRealLedger()');
 assert.match(w.document.getElementById('pr-ledger-body').textContent,/could not be verified/);
 assert.ok(!w.document.getElementById('pr-ledger-body').textContent.includes('Paid extension'));

 const tax=app('tax-report.html');tax.__rows=fixture;
 for(const month of ['2026-10','2026-08','2026-09']){
  const a=run(`window.__dbRows.invoices.flatMap(x=>finPaymentEvents(x.data)).filter(e=>e.date.startsWith('${month}')).reduce((n,e)=>n+e.amount,0)`);
  const b=tax.eval(`window.__rows.flatMap(x=>paymentEvents(x.data)).filter(e=>e.date.startsWith('${month}')).reduce((n,e)=>n+e.amount,0)`);
  assert.equal(a,b);
 }
 w.__dbFailure=true;await run('runFinanceView()');
 assert.equal(run('_lastFinanceSummary'),null);assert.match(w.document.getElementById('finance-data-status').textContent,/unavailable/);assert.equal(w.document.getElementById('pr-kpi-row').textContent,'');
 console.log('PASS Finance receipt definition matches Tax Report (October/August/September fixture); unsigned label and failed-load state');
 await w.happyDOM.close();await tax.happyDOM.close();
}
{
 const w=app('board.html'),run=code=>w.eval(code);
 run(`CARDS={'TEST':{invnum:'TEST',client:'Synthetic test',data:{base_price:3933,addons:[{price:185}],taxrate:10.55,subtotal_override:4118,tax_override:226.98,total_override:4344.98,startdate:'2026-11-01',destage_date:'2026-12-15'}}};window.capturedRate=null;promptExtensionDate=async(...args)=>{window.capturedRate=args[3];return null;}`);
 await run(`extendRental('TEST')`);assert.equal(w.capturedRate,1372.67);assert.equal(run('window.__dbWrites.length'),0);
 run(`promptExtensionDate=async()=>({days:30,fee:100,tax:2,chargeAmount:102,taxInclusive:false,newEndDate:'2027-01-14'})`);
 await run(`extendRental('TEST')`);assert.match(w.alerts.at(-1),/nothing charged/);assert.equal(run('window.__dbWrites.length'),0);
 assert.deepEqual(JSON.parse(JSON.stringify(run('SWJInvoiceSafety.extension({fee:90.46,tax:9.54,total:100,rate:10.55})'))),[]);
 console.log('PASS Board pretax missing-rate fallback and extension guard precede Stripe/save');
 await w.happyDOM.close();
}
{
 const w=app('proposal.html'),run=code=>w.eval(code);
 run(`_inv={invnum:'TEST-PR63',client:'Synthetic Test',address:'Synthetic property'};window.data={client:'Synthetic Test',base_price:3933,addons:[{desc:'Additional room',price:200,split:{rental:100,service:100}}],base_split:{rental:1966.5,service:1966.5},subtotal_override:4133,tax_override:226.98,total_override:4359.98,rental_service_split:true,rental_total:2151.5,service_total:1981.5,baserooms:'Living room',taxrate:10.55,startdate:'TBD',extrate:1377.67};_grand=4359.98;renderProposal(window.data)`);
 const text=w.document.getElementById('main-content').textContent;
 assert.match(text,/Furniture rental: \$1,966.50/);assert.match(text,/Staging design & arrangement: \$100.00/);assert.match(text,/Furniture Rental Total/);assert.match(text,/Staging Design Total/);assert.equal(run('window.__dbWrites.length'),0);
 console.log('PASS Proposal shows saved per-line split and bottom totals without writes');
 await w.happyDOM.close();
}
