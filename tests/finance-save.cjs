const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('payroll.html','utf8'),start=html.indexOf('async function saveFinanceConfig(){'),end=html.indexOf('\n}',start)+2;
const nodes=new Map(),element=id=>{if(!nodes.has(id))nodes.set(id,{value:'1',textContent:'',className:''});return nodes.get(id);};
let error={message:'fixture database rejected save'},runs=0;
const context={document:{getElementById:element},FINANCE_CONFIG:{original:true},Date,parseFloat,setTimeout:()=>{},runFinanceView:()=>runs++,_sb:{from:()=>({upsert:async()=>({error})})}};
vm.createContext(context);vm.runInContext(html.slice(start,end),context);
(async()=>{
 await context.saveFinanceConfig();assert.equal(context.FINANCE_CONFIG.original,true);assert.equal(runs,0);assert.match(element('fc-msg').textContent,/Could not save/);assert.equal(element('fc-msg').className,'msg err');
 error=null;await context.saveFinanceConfig();assert.equal(context.FINANCE_CONFIG.warehouse_monthly,1);assert.equal(runs,1);assert.match(element('fc-msg').textContent,/Saved/);
 const overrideStart=html.indexOf('async function saveFinanceOverrides(){'),overrideEnd=html.indexOf('\n}',overrideStart)+2;
 context.FINANCE_OVERRIDES={'2026-11':{note:'new'}};context._savedFinanceOverrides={'2026-11':{note:'saved'}};context.alert=()=>{};
 vm.runInContext(html.slice(overrideStart,overrideEnd),context);
 error={message:'fixture override rejection'};assert.equal(await context.saveFinanceOverrides(),false);assert.equal(context.FINANCE_OVERRIDES['2026-11'].note,'saved');
 context.FINANCE_OVERRIDES['2026-11'].note='accepted';error=null;assert.equal(await context.saveFinanceOverrides(),true);assert.equal(context._savedFinanceOverrides['2026-11'].note,'accepted');
 console.log('PASS Finance settings and monthly overrides: failures preserve confirmed state, successes update it');
})().catch(error=>{console.error(error);process.exitCode=1;});
