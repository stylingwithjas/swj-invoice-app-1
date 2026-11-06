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
 // These pricing fixtures assume tax verification; dedicated tax DOM tests exercise the real guard.
 window.SWJTaxGuard={allowed:()=>true,artifactAllowed:()=>true,reset(){},attach:data=>data};
 window.eval(fs.readFileSync(path.join(root,'assets/invoice-number.js'),'utf8'));
 window.SWJInvoiceNumber.ensure=async()=>true;window.SWJInvoiceNumber.attach=data=>data;
 window.eval(fs.readFileSync(path.join(root,'assets/records.js'),'utf8'));
 window.eval(fs.readFileSync(path.join(root,'assets/payment-events.js'),'utf8'));
 window.eval(fs.readFileSync(path.join(root,'assets/finance-plan.js'),'utf8'));
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
 run(`window.__stateForNavigation=captureState();genShowTab('recent');loadInvoiceData({invnum:'TEST-STATE-NAV',client:'Structured state client',invdate:'2026-01-15',base_price:2500,addons:[],state:window.__stateForNavigation})`);
 assert.ok(w.document.getElementById('gen-panel-create').classList.contains('on'),'structured-state invoice must open editor');

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
 fixture.push({invnum:'TEST-NOV',client:'Synthetic November payment',data:{paid:true,paid_date:'2026-11-05',amount_paid:2770.58,base_price:100,taxrate:10,addons:[]}});
 fixture.push({invnum:'TEST-STAGED',client:'Synthetic staged job',data:{paid:false,signed:true,startdate:'2026-11-02',base_price:1000,taxrate:0,addons:[]}});
 fixture.push({invnum:'TEST-OVERDUE',client:'Synthetic overdue balance',data:{paid:false,signed:true,startdate:'2026-09-15',base_price:13453.93,taxrate:0,addons:[]}});
 w.__dbRows.invoices=fixture;
 let moved=false;
 w.SWJPaymentBudget={
  monthLabel:m=>m==='2026-11'?'November 2026':'October 2026',
  receivedLabel:d=>d==='2026-10-31'?'Oct 31, 2026':d,
  load:async period=>{
   const rows=fixture.filter(x=>x.data.paid).map(x=>({event_id:x.invnum+'::',invnum:x.invnum,client:x.client,date:x.data.paid_date,amount_cents:Math.round(x.data.amount_paid*100),kind:'invoice',method:'Other/Manual',can_assign:true,budget_month:moved&&x.invnum==='TEST-2'?'2026-11':x.data.paid_date.slice(0,7)}));
   const sum=pred=>rows.filter(pred).reduce((n,e)=>n+e.amount_cents,0);
   return {period,issues:[],received_cents:sum(e=>e.date.startsWith(period)),budgeted_cents:sum(e=>e.budget_month===period),moved_in_cents:sum(e=>e.budget_month===period&&!e.date.startsWith(period)),moved_out_cents:sum(e=>e.date.startsWith(period)&&e.budget_month!==period),events:rows.filter(e=>e.date.startsWith(period)||e.budget_month===period)};
  },
  save:async (payment,month)=>{assert.equal(payment.invnum,'TEST-2');assert.equal(month,'2026-11');moved=true;},
 };

 run(`document.getElementById('fin-month').innerHTML='<option value="10">October</option>';document.getElementById('fin-year').innerHTML='<option value="2026">2026</option>'`);
 await run('runFinanceView()');
 assert.equal(Math.round(run('_lastFinanceSummary.revenue')*100)/100,11616.88);
 assert.match(w.document.getElementById('finance-data-status').textContent,/11,616.88/);
 assert.match(w.document.getElementById('fin-budget').textContent,/Budget total · October 2026\$25,070\.81/);
 assert.ok(!w.document.getElementById('fin-details-body').textContent.includes('Signed, unpaid'));
 const form=w.document.querySelector('form[data-payment-id="TEST-2::"]');
 form.elements.budget_month.value='2026-11';
 await run('savePaymentBudgetMonth').call(w,{preventDefault(){},currentTarget:form});
 assert.equal(run('_lastFinanceSummary.revenue'),7505.28);
 assert.equal(run('_lastFinanceSummary.cashReceipts'),11616.88);
 assert.match(w.document.getElementById('fin-budget').textContent,/Received Oct 31, 2026 · Budgeted in November 2026 \(moved out\)/);
 w.__dbRows.plaid_transactions=[
  {transaction_id:'oct-shopping',date:'2026-10-20',category:'Furniture/Inventory',job_invnum:'TEST-0',amount:400,pending:false},
  {transaction_id:'oct-truck',date:'2026-10-20',category:'Truck & Gas',job_invnum:'TEST-0',amount:300,pending:false},
  {transaction_id:'nov-truck',date:'2026-11-03',category:'Truck & Gas',job_invnum:'TEST-NOV',amount:257.22,pending:false},
  {transaction_id:'nov-gas',date:'2026-11-04',category:'Truck & Gas',job_invnum:'TEST-NOV',amount:52.78,pending:false},
  {transaction_id:'nov-shopping',date:'2026-11-03',category:'Furniture/Inventory',job_invnum:'TEST-NOV',amount:475.62,pending:false},
  {transaction_id:'nov-pending',date:'2026-11-03',category:'Furniture/Inventory',job_invnum:'TEST-NOV',amount:999,pending:true},
 ];
 w.__dbRows.time_entries=[
  {id:'oct-logged-job',invnum:'TEST-0',work_type:'staging',amount:100,clock_in:'2026-10-04T12:00:00'},
  {id:'logged-job',invnum:'TEST-NOV',work_type:'staging',amount:218.45,clock_in:'2026-11-04T12:00:00'},
  {id:'outside-job',invnum:'TEST-PROPOSAL',work_type:'warehouse',amount:358.55,clock_in:'2026-11-05T12:00:00'},
 ];
 run(`document.getElementById('fin-month').innerHTML='<option value="11">November</option>'`);
 await run('runFinanceView()');
 assert.equal(run('_lastFinanceSummary.revenue'),6882.18);
 assert.match(w.document.getElementById('fin-budget').textContent,/Budget total · November 2026\$21,336\.11/);
 assert.match(w.document.getElementById('pr-proj-mover-reconciliation').textContent,/\$218\.45[\s\S]+\$358\.55[\s\S]+\$577\.00/);
 assert.equal(run('_lastFinanceSummary.cashReceipts'),2770.58);
 assert.equal(run('_lastFinanceSummary.warehouseCost'),1473);
 assert.ok(run('_lastFinanceSummary.inventoryBudget')>0);
 assert.match(w.document.getElementById('fin-period').textContent,/November 2026/);
 assert.match(w.document.getElementById('fin-budget').textContent,/Received Oct 31, 2026 · Budgeted in November 2026/);
 const plan=run('_lastFinanceSummary');
 assert.equal(plan.truckCost,820);
 assert.equal(plan.shoppingCents,47562);
 assert.equal(plan.moverReserve,560);
 assert.equal(run("prBuildProjectList().find(p=>p.invnum==='TEST-NOV').truck"),310);
 assert.equal(run("prBuildProjectList().find(p=>p.invnum==='TEST-STAGED').truck"),510);
 assert.equal(run("prBuildProjectList().find(p=>p.invnum==='TEST-2').truck"),0);
 assert.equal(run("prBuildProjectList().find(p=>p.invnum==='TEST-NOV').warehouseShare"),736.50);
 assert.equal(run("prBuildProjectList().find(p=>p.invnum==='TEST-NOV').shopping"),475.62);
 assert.match(w.document.getElementById('fin-allocation-movers').textContent,/\$218\.45[\s\S]+\$358\.55[\s\S]+\$577\.00/);
 const previous=await run("_financeMonthCache.get('2026-10')");
 const carried=Math.max(0,Math.round(previous.inventoryBudget*100)-40000)/100;
 assert(carried>0);
 assert.match(w.document.getElementById('fin-inventory-carryover').textContent,new RegExp('Unused allowance carried in'+run('finMoney('+carried+')').replaceAll('$','\\$')));
 await run('loadMonthlyPerformance()');
 const performance=run('_prPerfCache.at(-1)');
 assert.deepEqual([performance.revenue,performance.costs,performance.net],[plan.revenue,plan.totalCosts,plan.operatingNet]);
 assert.equal(performance.m,10);
 assert.match(w.document.getElementById('pr-perf-tbody').textContent,/Nov 26/);
 assert.equal(w.document.getElementById('f-from').value,'2026-11-01');
 assert.equal(w.document.getElementById('f-to').value,'2026-11-30');
 const loadBudget=w.SWJPaymentBudget.load;
 let releaseNovember;
 w.SWJPaymentBudget.load=period=>period==='2026-11'
   ? new Promise(resolve=>{releaseNovember=async()=>resolve(await loadBudget(period));})
   : loadBudget(period);
 const staleNovember=run('runFinanceView()');
 assert.equal(w.document.getElementById('fin-headline').textContent,'');
 run(`document.getElementById('fin-month').innerHTML='<option value="10">October</option>'`);
 await run('runFinanceView()');
 await releaseNovember();
 await staleNovember;
 assert.equal(w.document.getElementById('fin-period').textContent,'October 2026');
 assert.equal(run('_lastFinanceSummary.revenue'),7505.28);
 assert.equal(run('_lastFinanceSummary.warehouseCost'),1473);
 w.SWJPaymentBudget.load=loadBudget;
 // A loss stays visible as net while the new allowance is zero across views.
 moved=false;
 run(`document.getElementById('fin-month').innerHTML='<option value="11">November</option>'`);
 await run('runFinanceView()');
 assert.ok(run('_lastFinanceSummary.operatingNet')<0);
 assert.equal(run('_lastFinanceSummary.inventoryBudget'),0);
 assert.match(w.document.getElementById('fin-waterfall').textContent,/New inventory allowance \$0\.00/);
 assert.match(w.document.getElementById('fin-allocation-movers').textContent,/#TEST-PROPOSAL · \$358\.55/);
 const lossCarryover=w.document.getElementById('fin-inventory-carryover').textContent;
 assert.match(lossCarryover,/New allowance after costs and keep\$0\.00/);
 const priorAllowance=(await run("_financeMonthCache.get('2026-10')")).inventoryBudget-400;
 assert.ok(priorAllowance>475.62);
 const planningLoss=-run('_lastFinanceSummary.operatingNet');
 assert.ok(lossCarryover.includes('Less planning loss'+run('finMoney('+planningLoss+')')));
 const remaining=priorAllowance-planningLoss-475.62;
 assert.ok(remaining>0);
 assert.ok(lossCarryover.includes('Inventory remaining'+run('finMoney('+remaining+')')));
 run(`document.getElementById('fin-month').innerHTML='<option value="12">December</option>'`);
 await run('runFinanceView()');
 assert.ok(w.document.getElementById('fin-inventory-carryover').textContent.includes(
   'Unused allowance carried in'+run('finMoney('+remaining+')')));

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
 const split={base_price:2650,tax_override:120.58,total_override:2770.58,rental_service_split:true,rental_total:1325,service_total:1325};
 const month='2026-11';
 const reserve=run(`calculateFinanceMonth('${month}','${month}-01','${month}-30',
  [{invnum:'FIXTURE',data:${JSON.stringify(split)}}],{received_cents:277058,budgeted_cents:277058,
  events:[{invnum:'FIXTURE',date:'2026-11-05',budget_month:'2026-11',kind:'invoice',amount_cents:277058}]},[],[])`);
 assert.equal(Math.round(reserve.taxSetAside*100),14670);
 w.__dbFailure=true;await run('runFinanceView()');
 assert.equal(run('_lastFinanceSummary'),null);assert.match(w.document.getElementById('finance-data-status').textContent,/incomplete/);assert.equal(w.document.getElementById('pr-kpi-row').textContent,'');
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
