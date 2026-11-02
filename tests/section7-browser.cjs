/* Actual browser UI; all API calls mocked. No real records or credentials. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn}=require('node:child_process');const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');const fixture=JSON.parse(fs.readFileSync(path.join(root,'tests/fixtures/section7-report.json'),'utf8'));
(async()=>{
 const server=spawn('python3',['-m','http.server','8766','--bind','127.0.0.1'],{cwd:root,stdio:'ignore'});
 let browser;
 try{
  for(let i=0;i<40;i++){try{await fetch('http://127.0.0.1:8766/reconciliation.html');break}catch{await new Promise(r=>setTimeout(r,100))}}
  const executablePath=process.env.SWJ_CHROMIUM_EXECUTABLE;
  browser=await chromium.launch({headless:true,...(executablePath?{executablePath,args:['--no-sandbox','--disable-dev-shm-usage','--single-process','--no-zygote','--in-process-gpu','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']}: {})});
  const context=await browser.newContext({viewport:{width:390,height:844},acceptDownloads:true});
  const page=await context.newPage(),errors=[],requests=[];
  let authenticated=false,configured=true,failScan=false;
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async route=>{
   const req=route.request(),url=new URL(req.url());
   if(url.origin!=='http://127.0.0.1:8766')return route.abort();
   if(url.pathname==='/api/webhook'){
    const action=url.searchParams.get('section7');requests.push({action,method:req.method()});
    let status=200,body={};
    if(action==='session')body={configured,authenticated};
    else if(action==='login'){assert.equal(req.method(),'POST');authenticated=true;body={authenticated:true};}
    else if(action==='logout'){authenticated=false;body={authenticated:false};}
    else if(action==='report'){
     assert.equal(req.method(),'GET');assert.equal(url.searchParams.get('period'),'2026-10');
     if(!authenticated){status=401;body={error:'Administrator sign-in required.'}}
     else if(failScan){status=503;body={error:'Incomplete scan; retry.',coverage:{complete:false}}}
     else body=fixture;
    }else throw Error('Unexpected endpoint');
    return route.fulfill({status,contentType:'application/json',headers:{'Cache-Control':'no-store'},body:JSON.stringify(body)});
   }
   if(url.pathname.startsWith('/api/'))throw Error('Report attempted another API');
   return route.continue();
  });
  await page.goto('http://127.0.0.1:8766/reconciliation.html');await page.locator('#login-panel').waitFor({state:'visible'});
  assert.equal(requests.filter(r=>r.action==='report').length,0,'No automatic financial scan before sign-in');
  await page.fill('#password','synthetic-browser-password');await page.click('#login-button');await page.locator('#workspace').waitFor({state:'visible'});
  assert.equal(await page.inputValue('#password'),'');assert.equal(await page.evaluate(()=>localStorage.length),0);assert.equal(await page.evaluate(()=>sessionStorage.length),0);
  await page.click('#scan-button');await page.locator('#report').waitFor({state:'visible'});
  assert.equal(await page.textContent('#checked'),String(fixture.invoices_checked));assert.equal(await page.textContent('#discrepancies'),String(fixture.discrepancy_count));
  assert.match(await page.textContent('#message'),/SYNTHETIC TEST DATA/);assert.equal(await page.locator('.invoice').count(),fixture.flagged_count);
  await page.locator('.invoice summary').first().click();
  fs.mkdirSync(path.join(root,'tests/artifacts'),{recursive:true});
  await page.screenshot({path:path.join(root,'tests/artifacts/section7-mobile.png'),fullPage:true});
  for(const width of [360,768,1440]){await page.setViewportSize({width,height:1000});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'No overflow at '+width);}
  await page.screenshot({path:path.join(root,'tests/artifacts/section7-desktop.png'),fullPage:true});
  await page.selectOption('#view','paid_period');assert.equal(await page.locator('.invoice').count(),fixture.results.filter(r=>r.paid_in_review_period).length);
  const download=page.waitForEvent('download');await page.click('#export');const file=await download;
  const bytes=fs.readFileSync(await file.path(),'utf8').replace(/^\ufeff/,'');assert.equal(bytes,fixture.exports.paid_period,'CSV must be the exact server scan/filter export');
  assert.match(file.suggestedFilename(),/2026-10_paid_period/);
  await page.selectOption('#view','unverifiable');assert.equal(await page.locator('.invoice').count(),fixture.results.filter(r=>r.has_unverifiable).length);
  failScan=true;await page.click('#scan-button');await page.waitForFunction(()=>document.getElementById('message').textContent.includes('Incomplete scan'));
  assert.equal(await page.locator('#report').isVisible(),false);assert.equal(await page.locator('.invoice').count(),0);assert.equal(await page.locator('#export').isDisabled(),true);
  await page.click('#logout');await page.locator('#login-panel').waitFor({state:'visible'});assert.equal(await page.locator('#workspace').isVisible(),false);
  configured=false;await page.reload();await page.locator('#setup-panel').waitFor({state:'visible'});assert.equal(await page.locator('#workspace').isVisible(),false);
  assert.deepEqual(errors,[]);assert.ok(requests.every(r=>['session','login','report','logout'].includes(r.action)));
  console.log('PASS Section 7 browser: sign-in gate, no stored credential, server figures, responsive views, exact filtered CSV, failure clearing, logout and missing configuration.');
 }finally{if(browser)await browser.close();server.kill()}
})().catch(e=>{console.error(e);process.exitCode=1});
