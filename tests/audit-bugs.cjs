const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),{webcrypto}=require('node:crypto');
const context={crypto:webcrypto,TextEncoder,Date,console};vm.createContext(context);
for(const file of ['assets/invoice-number.js','assets/task-carry.js'])vm.runInContext(fs.readFileSync(file,'utf8'),context);
assert.equal(context.SWJInvoiceNumber.next([{invnum:'2020131'},{invnum:'2020131v3'},{invnum:'DRAFT-AUDIT-20261104-1'},{invnum:'GS2026999'},{invnum:'TAX_REVIEW_20261105'}]),2020132);
(async()=>{
 const original={id:'source-task',title:'Fixture',status:'pending',created_at:'2026-11-04T00:00:00Z'};
 const first=await context.SWJTaskCarry.rows([original],'2026-11-05'),second=await context.SWJTaskCarry.rows([original],'2026-11-05');
 assert.equal(first[0].id,second[0].id);assert.notEqual(first[0].id,(await context.SWJTaskCarry.rows([original],'2026-11-06'))[0].id);
 assert.notEqual(first[0].id,(await context.SWJTaskCarry.rows([{...original,id:'different-source'}],'2026-11-05'))[0].id);
 assert.equal(first[0].created_at,undefined);assert.equal(original.id,'source-task');
 const stored=new Map();let writes=0;
 context.todayDate=new Date('2026-11-05T12:00:00Z');context.viewDate=context.todayDate;context.isToday=()=>true;context.toISODate=d=>d.toISOString().slice(0,10);
 context.document={getElementById:()=>null};context._sb={from:()=>({select:()=>({eq:()=>({eq:async()=>({data:[original]})})}),upsert:async(rows,options)=>{assert.equal(options.ignoreDuplicates,true);for(const row of rows)if(!stored.has(row.id)){stored.set(row.id,row);writes++;}return{};}})};
 const html=fs.readFileSync('today.html','utf8'),start=html.indexOf('async function carryForward(){'),end=html.indexOf('\n}',start)+2;vm.runInContext(html.slice(start,end),context);
 await context.carryForward();stored.get(first[0].id).status='done';await context.carryForward();
 assert.equal(writes,1);assert.equal(stored.get(first[0].id).status,'done');
 console.log('PASS invoice namespace and task carry-forward retries/concurrency key/completion preservation');
})().catch(error=>{console.error(error);process.exitCode=1;});
