const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync('today.html','utf8');
const nodes={};const node=id=>nodes[id]||(nodes[id]={style:{},innerHTML:'',textContent:''});
function fn(name){const start=html.indexOf((['buildPayoutSnapshot','loadMonthlyFinanceData'].includes(name)?'async ':'')+'function '+name+'(');const end=html.indexOf('\n}',start)+2;return html.slice(start,end);}
const c={document:{getElementById:node},Date,Number,Math,Set,console,allInvoices:[],_monthlyPlaidTx:[],todayDate:new Date('2026-11-04T00:00:00'),fmt:n=>'$'+Math.round(n).toLocaleString(),esc:s=>String(s||''),toISODate:d=>d.toISOString().slice(0,10),computeGrand:d=>d.total};c.window=c;vm.createContext(c);vm.runInContext(fs.readFileSync('assets/records.js','utf8'),c);
vm.runInContext(fs.readFileSync('assets/payment-events.js','utf8'),c);
vm.runInContext(fn('buildS6')+'\n'+fn('buildS11')+'\n'+fn('fmtPayoutDate')+'\n'+fn('fmtExact')+'\n'+fn('buildPayoutSnapshot'),c);
c.buildS6();assert(node('s6-body').textContent.includes('Verifying recorded receipts and costs'));
c.SWJCashSnapshot={monthly:{collected_cents:277058,costs_cents:253658,card_spend_cents:237794,mover_spend_cents:0,stripe_fees_cents:8164,bank_fees_cents:7700,sales_tax_cents:12058,revenue_ex_tax_cents:265000,result_cents:11342,margin_percent:4.26}};
c.buildS6();assert.match(node('s6-body').innerHTML,/\$2,770\.58 − recorded costs \$2,536\.58 − sales tax collected \$120\.58 = \$113\.42/);
assert.match(node('s6-body').innerHTML,/Recorded surplus<\/div><div class="snap-val">\$113\.42/);
assert.match(node('s6-body').innerHTML,/Card purchases: \$2,377\.94/);
assert.match(node('s6-body').innerHTML,/tax set-aside that includes B&amp;O/);
assert(node('s6-body').innerHTML.includes('Planning net'));
c.SWJHomePlan={revenue:2770.58,operatingNet:-16.77,truckCost:510,warehouseCost:1473,moverCost:577,moverReserve:0,stripeFee:80.65,taxSetAside:146.70};
c.buildS6();
assert.match(node('s6-body').innerHTML,/Planning net<\/div><div class="snap-val">−\$16\.77/);
assert.match(node('s6-body').innerHTML,/−\$130\.19 difference/);
assert.match(node('s6-body').innerHTML,/Deduct planned movers · −\$577\.00/);

Object.assign(c.SWJCashSnapshot.monthly,{costs_cents:300000,result_cents:-35000,margin_percent:-13.21});
c.buildS6();assert(node('s6-body').innerHTML.includes('snap-cell loss'));
c.pfView='monthly';c.SWJCashSnapshot={monthly:{collected_cents:7500,costs_cents:2500,result_cents:5000},monthly_jobs:[{invnum:'EXT',client:'Extension job',receipts_cents:7500,recorded_cost_cents:2500,contribution_cents:5000}]};c.buildS11();assert(node('s11-body').innerHTML.includes('$75'));c.pfView='jobs';c.buildS11();assert(node('s11-body').innerHTML.includes('Extension job'));
(async()=>{c.monthRangeISO=()=>['2026-11-01','2026-11-30'];c._sb={from:table=>{const data=table==='plaid_transactions'?[{transaction_id:'posted',amount:74.2,pending:false},{transaction_id:'pending',amount:2262.78,pending:true}]:[];const q=new Proxy({}, {get:(_,key)=>key==='range'?async()=>({data,count:data.length}):()=>q});return q;}};vm.runInContext(fn('loadMonthlyFinanceData'),c);await c.loadMonthlyFinanceData();assert.equal(c._monthlyPlaidTx.length,1);assert.equal(c._monthlyPlaidTx[0].amount,74.2);c.fetch=async()=>({json:async()=>({as_of:'2026-11-04',headline:{available:0,pending:0,in_transit:null,next_expected:null},timeline:[{landing_date:'2026-11-05',released_date:'2026-11-04',total_amount:3992.06,landing_is_estimate:true,items:[]}]})});await c.buildPayoutSnapshot();assert(node('s6b-body').innerHTML.includes('swj-money-empty">$0.00'));
c.fetch=async()=>({json:async()=>({as_of:'2026-11-04',headline:{available:0,pending:0,in_transit:null,next_expected:{amount:200,arrival_date:'2026-11-07',is_estimate:false}},timeline:[]})});
await c.buildPayoutSnapshot();assert(node('s6b-body').innerHTML.includes('>$200.00</strong>'));assert(!node('s6b-body').innerHTML.includes('No pending funds'));c.fetch=async()=>{throw Error('offline')};await c.buildPayoutSnapshot();assert(node('s6b-body').innerHTML.includes('unavailable'));assert(!node('s6b-body').innerHTML.includes('3,992'));console.log('Home briefing: loading, sales tax, loss, settled and upcoming payouts, unavailable states passed');})();
