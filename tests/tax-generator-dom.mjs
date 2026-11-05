/* Both actual generators + real tax guard; all DB/PDF/Stripe endpoints are mocked. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {webcrypto} from 'node:crypto';
const {Window}=await import(process.env.SWJ_HAPPY_DOM||'happy-dom');
const sdk=fs.readFileSync('tests/pr63-browser.cjs','utf8').match(/const mock=`([\s\S]*?)`;/)[1];
for(const file of ['index.html','luxury.html']){
 const html=fs.readFileSync(file,'utf8'),w=new Window({url:'https://fixture.test/'+file,settings:{disableCSSFileLoading:true,disableJavaScriptFileLoading:true,disableJavaScriptEvaluation:true}});
 w.document.write(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,''));
 const context=vm.createContext(w);w.eval=code=>vm.runInContext(code,context);w.eval(sdk);
 Object.defineProperty(w,'crypto',{value:webcrypto});
 w.scrollTo=()=>{};w.alerts=[];w.alert=message=>w.alerts.push(message);w.confirm=()=>true;w.open=()=>({location:{href:''}});
 w.URL.createObjectURL=()=> 'blob:fixture';
 const calls=[];
 w.fetch=async(url,options={})=>{
  const body=options.body?JSON.parse(options.body):null;calls.push({url,body});
  if(url.startsWith('/api/taxrate'))return {ok:true,json:async()=>({ok:true,rate:10.55,addressMatch:true,needsConfirmation:true,period:'Q42026',locationCode:'1726',matchedAddress:'600 4TH AVE SEATTLE WA 98104',verification:{proof:'fixture-proof',expiresAt:Date.now()/1000+3600}})};
  if(url==='/api/counter')return {ok:true,json:async()=>({mode:'allocated',invnum:body.requested_invnum||'2020132',request_id:body.request_id,proof:'fixture-number-proof'})};
  if(url==='/api/stripe')return {ok:true,json:async()=>({url:'https://buy.stripe.com/fixture',id:'fixture-link'})};
  if(url==='/api/generate')return {ok:true,blob:async()=>new w.Blob(['%PDF '+'.'.repeat(2000)],{type:'application/pdf'})};
  if(url==='/api/counter'||url==='/api/invoices')return {ok:true,json:async()=>({saved:true})};
  throw Error('Unexpected request '+url);
 };
 for(const asset of ['invoice-safety.js','tax-guard.js','invoice-number.js','payment-events.js'])w.eval(fs.readFileSync('assets/'+asset,'utf8'));
 for(const m of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi))if(m[1].trim())w.eval(m[1]);
 w.eval(`document.getElementById('base_price').value='2650';document.getElementById('client').value='Fixture Client';document.getElementById('client_email').value='fixture@example.test';document.getElementById('address').value='600 4th Ave';document.getElementById('city').value='Seattle';document.getElementById('zipcode').value='98104';document.getElementById('invdate').value='2026-11-05';document.getElementById('invnum-display').value='2020132';document.getElementById('rental-term-days').value='45';setStartDateFromData('TBD');selRooms.add('Living Room');recalc();`);
 assert.equal(await w.eval('generatePDF()'),null);assert.equal(calls.filter(c=>c.url==='/api/stripe'||c.url==='/api/generate').length,0,'unverified generator must not create payment links or PDFs');
 w.SWJTaxGuard.lookup();await new Promise(resolve=>setTimeout(resolve,550));
 assert.ok(w.document.getElementById('tax-match-confirm'));
 w.document.getElementById('tax-match-confirm').checked=true;w.document.getElementById('tax-match-confirm').dispatchEvent(new w.Event('change'));
 const generated=await w.eval('generatePDF()');
 assert.ok(generated,file+' must still generate after tax verification');
 const stripe=calls.find(call=>call.url==='/api/stripe'),pdf=calls.find(call=>call.url==='/api/generate');
 assert.ok(stripe);assert.ok(pdf);assert.equal(pdf.body.invoice_number.proof,'fixture-number-proof');assert.equal(stripe.body.invoice_number.proof,'fixture-number-proof');assert.equal(pdf.body.taxrate,10.55);assert.equal(pdf.body.tax_verification.proof,'fixture-proof');assert.equal(pdf.body.tax_verification.addressConfirmed,true);
 assert.equal(stripe.body.tax_context.tax_verification.proof,'fixture-proof');assert.equal(stripe.body.tax_context.address,pdf.body.address);
 assert.equal(pdf.body.subtotal_override,2650);assert.equal(Math.round(pdf.body.tax_override*100),13979);assert.equal(Math.round(pdf.body.total_override*100),278979);
 assert.equal(w.SWJTaxGuard.artifactAllowed(pdf.body,w.eval('buildPricingPayload()')),true);
 w.document.getElementById('zipcode').value='98402';assert.equal(w.SWJTaxGuard.allowed('sending',w.eval('buildPricingPayload()')),false);
 await w.happyDOM.close();console.log('PASS '+file+' actual generator: unverified outputs blocked; verified tax evidence reaches Stripe/PDF; prices/totals preserved');
}
