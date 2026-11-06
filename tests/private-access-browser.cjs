/* Isolated browser integration. Every API is mocked and external traffic aborted. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {chromium} = require('playwright');
const root = path.resolve(__dirname,'..');
const origin = 'http://127.0.0.1:8794';
const invoice = {invnum:'2020132',client:'Fixture Client',address:'Synthetic property',invdate:'2026-11-05',data:{
  client:'Fixture Client',base_price:2650,taxrate:10.55,baserooms:'Living Room',startdate:'TBD',
  payment_url:'https://buy.stripe.com/fixture',signed:false,paid:false,signatures:[],addons:[]
}};
const sdk = `window.supabase={createClient:(url,key,options)=>({from:table=>{
  let method='GET',body;const query=new Proxy({}, {get:(_,name)=>{
    if(name==='then')return (resolve,reject)=>options.global.fetch(url+'/rest/v1/'+table,{method,headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined}).then(async r=>({data:await r.json(),error:r.ok?null:{message:'Sign in'}})).then(resolve,reject);
    return (...args)=>{if(name==='update'){method='PATCH';body=args[0];}return query;};
  }});return query;}})};`;
(async()=>{
 const server = http.createServer((req,res)=>{
   const filename = path.join(root,new URL(req.url,origin).pathname);
   if(!filename.startsWith(root+path.sep)){res.writeHead(403);res.end();return;}
   fs.readFile(filename,(error,bytes)=>{if(error){res.writeHead(404);res.end();return;}
     res.setHeader('Content-Type',filename.endsWith('.js')?'application/javascript':filename.endsWith('.css')?'text/css':'text/html');res.end(bytes);});
 });
 await new Promise(resolve=>server.listen(8794,'127.0.0.1',resolve));
 let browser;
 try {
  browser=await chromium.launch({headless:true,executablePath:process.env.SWJ_CHROMIUM_EXECUTABLE||'/usr/bin/chromium',args:['--no-sandbox','--disable-dev-shm-usage','--single-process','--no-zygote','--in-process-gpu','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
  const context=await browser.newContext({viewport:{width:390,height:844}});
  let owner=false, ownerReads=0, ownerWrites=0, signatures=[], crewReplies=[];
  const errors=[];
  await context.route('**/*',async route=>{
   const req=route.request(),url=new URL(req.url());
   if(req.url().includes('supabase-js'))return route.fulfill({contentType:'application/javascript',body:sdk});
   if(url.origin!==origin)return route.abort();
   if(!url.pathname.startsWith('/api/'))return route.continue();
   const data=req.postDataJSON()||{};
   const reply=(body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
   if(url.searchParams.get('admin')==='session')return reply({configured:true,authenticated:owner});
   if(url.searchParams.get('admin')==='login'){owner=true;return reply({authenticated:true});}
   if(url.searchParams.get('data')){
    assert.equal(owner,true,'business data requires owner login');
    if(req.method()==='PATCH')ownerWrites++;else ownerReads++;
    return reply([{id:'fixture-hour',employee_name:'Fixture Crew',work_type:'staging',clock_in:'2026-11-05T09:00:00Z',clock_out:'2026-11-05T10:00:00Z',hours:1,amount:35,approved:false}]);
   }
   if(url.searchParams.get('public')==='invoice'){
    if(data.token!=='fixture-client-private-token')return reply({error:'Please ask Jasmine for a new private approval link.'},403);
    assert.equal(data.role,'client');return reply({invoice});
   }
   if(url.pathname==='/api/sign'){
    signatures.push(data);return reply({signed:true,fully_signed:true,contract_url:'https://fixture.example.test/signed.pdf'});
   }
   if(url.searchParams.get('public')==='confirm'){
    assert.equal(data.token,'fixture-crew-private-token');
    return reply({invoice:{invnum:'2020132',address:'Synthetic property',data:{mover_name:'Fixture Crew',startdate:'2026-11-10',truck_pickup_time:'09:00',confirmations:{mover1:{}}}}});
   }
   if(url.searchParams.get('public')==='respond'){
    crewReplies.push(data);
    return reply({invoice:{invnum:'2020132',address:'Synthetic property',data:{startdate:'2026-11-10',confirmations:{mover1:{status:data.status}}}},notified:true});
   }
   throw new Error('Unexpected API '+req.url());
  });
  const page=await context.newPage();
  page.on('pageerror',error=>errors.push(error.message));
  // Owner login gates payroll approval, retains DOM nodes and prevents requests.
  await page.goto(origin+'/approve-hours.html?ids=fixture-hour');
  await page.locator('#swj-auth-go').waitFor();
  assert.equal(await page.locator('#main-content').count(),1);
  assert.equal(ownerReads,0);
  await page.locator('#swj-auth-pw').fill('fixture-password');
  await page.locator('#swj-auth-go').click();
  await page.locator('#approve-btn').waitFor();
  await page.locator('#approve-btn').click();
  await page.getByText('Hours Approved',{exact:true}).waitFor();
  assert.equal(ownerReads,1);assert.equal(ownerWrites,1);
  // Clients get no login or password prompt. Invoice data/token survives signing.
  await page.goto(origin+'/proposal.html?inv=2020132&role=client&token=fixture-client-private-token');
  await page.locator('#sign-btn').waitFor();
  assert.equal(await page.locator('input[type=password]').count(),0);
  await page.getByRole('button',{name:'Type instead →'}).click();
  await page.locator('#sig-name-typed').fill('Fixture Client');
  await page.locator('#sig-agree').check();
  await page.locator('#sign-btn').click();
  await page.getByText('Thank you',{exact:false}).first().waitFor();
  assert.equal(await page.locator('a[href="https://buy.stripe.com/fixture"]').count()>0,true);
  assert.equal(signatures.length,1);
  assert.equal(signatures[0].token,'fixture-client-private-token');
  assert.equal(signatures[0].invnum,'2020132');assert.equal(signatures[0].role,'client');
  assert.equal(signatures[0].agreed,true);assert.ok(signatures[0].sig_image);
  // A predictable invoice URL cannot display the invoice/signing controls.
  await page.goto(origin+'/proposal.html?inv=2020132');
  await page.getByText('Please ask Jasmine for a new private approval link.').waitFor();
  assert.equal(await page.locator('#sign-btn').count(),0);
  // Crew confirmation keeps the existing one-tap flow with a restricted payload.
  await page.goto(origin+'/confirm.html?inv=2020132&role=mover1&token=fixture-crew-private-token');
  await page.locator('#btn-yes').click();
  await page.getByText("You're confirmed!",{exact:true}).waitFor();
  assert.equal(crewReplies.length,1);
  assert.deepEqual(crewReplies[0],{invnum:'2020132',role:'mover1',status:'confirmed',token:'fixture-crew-private-token'});
  assert.deepEqual(errors,[]);
  await context.close();
  console.log('PASS isolated Chromium: owner login/approval, client signing/token, Stripe URL, missing-token denial, crew confirmation; zero external requests');
 } finally {
  if(browser)await browser.close();
  await new Promise(resolve=>server.close(resolve));
 }
})().catch(error=>{console.error(error);process.exitCode=1;});
