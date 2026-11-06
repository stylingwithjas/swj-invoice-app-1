/* Local-only browser regression. All DB/API traffic is intercepted; no real sends. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
const mock=`window.__dbRows={};window.__dbWrites=[];window.__dbFailure=false;
window.supabase={createClient:()=>({from:table=>{
 let filter=[],single=false,write=null;
 const q=new Proxy({}, {get:(_,key)=>{
 if(key==='then')return (resolve,reject)=>{let rows=window.__dbRows[table]||[];for(const [op,col,v]of filter)rows=rows.filter(r=>op==='eq'?r[col]===v:op==='gte'?r[col]>=v:r[col]<=v);if(write)window.__dbWrites.push({table,write});return Promise.resolve({data:single?(rows[0]||null):rows,count:rows.length,error:window.__dbFailure?{message:'Test connection failure'}:null}).then(resolve,reject)};
 return (...args)=>{if(['eq','gte','lte'].includes(key))filter.push([key,...args]);if(key==='single'||key==='maybeSingle')single=true;if(['upsert','insert','update','delete'].includes(key))write=key;return q};
 }});return q;
}})};`;
(async()=>{
 const server=spawn('python3',['-m','http.server','8765','--bind','127.0.0.1'],{cwd:root,stdio:'ignore'});
 const executablePath=process.env.SWJ_CHROMIUM_EXECUTABLE;
 const browser=await chromium.launch({headless:true,...(executablePath?{executablePath,args:['--no-sandbox','--disable-dev-shm-usage','--single-process','--no-zygote','--in-process-gpu','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']}: {})});
 try{
  for(let n=0;n<30;n++){try{await fetch('http://127.0.0.1:8765/index.html');break;}catch{await new Promise(r=>setTimeout(r,100));}}
  const context=await browser.newContext({viewport:{width:390,height:844}});
  for(const file of ['index.html','luxury.html']){
   const page=await context.newPage();
   const errors=[],dialogs=[],requests=[];
   page.on('pageerror',e=>errors.push(e.message));
   page.on('dialog',async d=>{dialogs.push(d.message());await d.accept()});
   await page.addInitScript(mock);
   await page.addInitScript(()=>localStorage.setItem('swj_auth_v1','ok'));
   await page.route('**/*',route=>{
    const url=route.request().url();
    if(url.includes('supabase-js'))return route.fulfill({contentType:'application/javascript',body:''});
    // Pricing-only fixtures assume tax verification; the real guard has dedicated DOM/browser coverage.
    if(url.endsWith('/assets/tax-guard.js'))return route.fulfill({contentType:'application/javascript',body:'window.SWJTaxGuard={allowed:()=>true,artifactAllowed:()=>true,reset(){},attach:data=>data};'});
    if(!url.startsWith('http://127.0.0.1:8765/'))return route.abort();
    if(url.includes('/api/')){requests.push(url);return route.fulfill({contentType:'application/json',body:'{"value":2020999,"url":"https://example.test/payment","saved":true}'});}
    return route.continue();
   });
   await page.goto('http://127.0.0.1:8765/'+file);
   await page.waitForFunction(()=>typeof buildPricingPayload==='function' && document.getElementById('base_price').value!=='');
   const initial=await page.evaluate(()=>{
    _loadedInvoiceData=null;pricingMode='rooms';
    document.getElementById('base_price').value='3933';document.getElementById('taxrate').value='10.55';document.getElementById('rental-term-days').value='45';
    ['ov-subtotal','ov-tax','ov-total'].forEach(id=>document.getElementById(id).value='');
    selAddons.clear();extraBedrooms=[];if(typeof extraAreas!=='undefined')extraAreas=[];
    customs=[{desc:'Logistics & Handling',price:185,scope:''}];selRooms.add('Living Room');recalc();
    const p=buildPricingPayload();return {tax:p.ovTax,grand:p.grand,sub:p.ovSub,rental:p.rental_total,service:p.service_total,issues:p.reconcile};
   });
   assert.deepEqual(initial,{tax:226.98,grand:4344.98,sub:4118,rental:2151.5,service:1966.5,issues:[]});
   const adjustment=await page.evaluate(()=>{
    customs.push({desc:'Additional adjustment visit',price:250,scope:''});recalc();const p=buildPricingPayload();return {tax:p.ovTax,grand:p.grand,rental:p.rental_total,service:p.service_total};
   });
   assert.deepEqual(adjustment,{tax:226.98,grand:4594.98,rental:2151.5,service:2216.5});
   const manual=await page.evaluate(()=>{document.getElementById('ov-total').value='4604.98';recalc();const p=buildPricingPayload();return {tax:p.ovTax,split:p.rental_service_split,blocked:p.blocked,issues:p.reconcile};});
   assert.equal(manual.tax,226.98);assert.equal(manual.split,true);assert.equal(manual.blocked,true);assert.ok(manual.issues.some(x=>x.includes('grand total')));
   const before=requests.length;
   await page.evaluate(async()=>{await saveDraft(false);await downloadPdfNow();await generatePDF();await generate();await generateAndEmail();await sendEmail()});
   assert.equal(requests.length,before,'Blocked overrides must not call APIs');
   await page.locator('#invoice-safety').scrollIntoViewIfNeeded();
   fs.mkdirSync(path.join(root,'tests/artifacts'),{recursive:true});
   await page.screenshot({path:path.join(root,'tests/artifacts',file.replace('.html','')+'-manual-warning.png')});
   for(const width of [360,768,1440]){await page.setViewportSize({width,height:900});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'No page overflow at '+width);}
   await page.screenshot({path:path.join(root,'tests/artifacts',file.replace('.html','')+'-desktop-warning.png')});
   await page.setViewportSize({width:390,height:844});
   const loaded=await page.evaluate(()=>{
    clearManualTotals();customs.pop();const p=buildPricingPayload();
    const d={invnum:'TEST-PR63',client:'Preview Test Only',address:'Test property',invdate:'2026-11-02',startdate:'TBD',taxrate:10.55,base_price:p.base,addons:p.allAddons,subtotal_override:p.ovSub,tax_override:p.ovTax,total_override:p.grand,manual_totals_fields:[],state:captureState()};
    window.__fixture=d;loadInvoiceData(d);
    extraBedrooms.push({name:'Test Bedroom',bed:'Queen',price:200,scope:'Test room'});recalc();
    return {sub:buildPricingPayload().ovSub,manual:document.getElementById('ov-subtotal').value};
   });
   assert.deepEqual(loaded,{sub:4318,manual:''});
   const frozen=await page.evaluate(()=>{const d={...window.__fixture};delete d.manual_totals_fields;loadInvoiceData(d);customs.push({desc:'Additional adjustment visit',price:15,scope:''});recalc();return {sub:buildPricingPayload().ovSub,warning:document.getElementById('invoice-safety').textContent};});
   assert.equal(frozen.sub,4118);assert.match(frozen.warning,/Manual totals active/);assert.match(frozen.warning,/4133/);
   await page.evaluate(()=>clearManualTotals());
   assert.equal(await page.evaluate(()=>buildPricingPayload().ovSub),4133);
   const invalid=await page.evaluate(()=>{document.getElementById('rental-term-days').value='0';recalc();return {value:document.getElementById('rental-term-days').value,error:buildPricingPayload().termError};});
   assert.equal(invalid.value,'0');assert.ok(invalid.error);
   const ninety=await page.evaluate(()=>{document.getElementById('rental-term-days').value='90';recalc();return {base:buildPricingPayload().base_split,note:buildPricingPayload().termNote};});
   assert.equal(ninety.base.service,0);assert.ok(ninety.note);
   const paid=await page.evaluate(()=>{loadInvoiceData({...window.__fixture,paid:true});return {locked:SWJInvoiceSafety.isPaid(_loadedInvoiceData),warning:document.getElementById('invoice-safety').textContent};});
   assert.equal(paid.locked,true);assert.match(paid.warning,/Paid invoice protected/);
   const writes=await page.evaluate(()=>window.__dbWrites.length),pre=requests.length;
   await page.evaluate(async()=>{await saveDraft(false);await downloadPdfNow();await generatePDF();await generate();await saveInvoice(window.__fixture)});
   assert.equal(await page.evaluate(()=>window.__dbWrites.length),writes);assert.equal(requests.length,pre);
   await page.locator('#invoice-safety').scrollIntoViewIfNeeded();
   await page.screenshot({path:path.join(root,'tests/artifacts',file.replace('.html','')+'-paid-lock.png')});
   assert.deepEqual(errors,[],file+' browser errors');
   assert.ok(dialogs.length>=10);
   console.log('PASS',file,'actual calculation, all generation/draft guards, adjustment, manual totals, loaded forms, term validation and paid lock');

  }
 }finally{await browser.close();server.kill()}
})().catch(e=>{console.error(e);process.exitCode=1});
