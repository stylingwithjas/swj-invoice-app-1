/* Address verification only. Existing pricing, allocations and tax formulas stay in the generators. */
(function(root){
 'use strict';
 let timer,seq=0,controller,result=null,confirmed=false,manualKey='';
 const el=id=>document.getElementById(id), value=id=>(el(id)?.value||'').trim();
 const inputs=()=>({addr:value('address'),city:value('city'),zip:value('zipcode'),date:value('invdate')});
 const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
 let matchedInput=null;
 function mount(){
  if(el('tax-guard-controls')||!el('tax-note'))return;
  const region=document.createElement('div');region.id='tax-guard-controls';
  region.innerHTML='<label id="tax-match-wrap" hidden style="display:flex;gap:8px;align-items:center;min-height:44px;font-size:13px;color:#514d42"><input id="tax-match-confirm" type="checkbox" style="min-width:22px;min-height:22px"> <span id="tax-match-label"></span></label><div id="tax-manual-wrap" hidden><p id="tax-manual-note" style="font-size:13px;color:#8c4437"></p><label style="font-size:13px">Manual tax review reason (at least 10 characters)<input id="tax-manual-reason" type="text" minlength="10" style="display:block;width:100%;min-height:44px;box-sizing:border-box" placeholder="Why this manual tax amount is correct"></label><label style="display:flex;gap:8px;align-items:center;min-height:44px;font-size:13px"><input id="tax-manual-confirm" type="checkbox" style="min-width:22px;min-height:22px">I reviewed this manual tax exception.</label></div><button type="button" id="tax-retry" style="min-height:44px;background:transparent;border:1px solid #817e76;border-radius:8px;padding:8px;font:inherit">Verify property tax</button>';
  el('tax-note').setAttribute('role','status');el('tax-note').setAttribute('aria-live','polite');
  el('tax-note').parentElement.appendChild(region);
  // The hidden attribute must win over this label's explicit display style.
  el('tax-match-wrap').style.display='none';
  el('tax-match-confirm').addEventListener('change',()=>{confirmed=el('tax-match-confirm').checked;});
  el('tax-manual-confirm').addEventListener('change',()=>{manualKey=el('tax-manual-confirm').checked?reviewKey():'';});
  el('tax-retry').addEventListener('click',lookup);
 }
 function note(text,error=false){mount();if(el('tax-note')){el('tax-note').textContent=text;el('tax-note').style.color=error?'#8c4437':'#555b46';}}
 function lookup(){
  clearTimeout(timer);controller?.abort();const generation=++seq;
  result=null;matchedInput=null;confirmed=false;manualKey='';mount();
  if(el('tax-match-confirm'))el('tax-match-confirm').checked=false;
  if(el('tax-manual-confirm'))el('tax-manual-confirm').checked=false;
  if(el('tax-match-wrap'))el('tax-match-wrap').style.display='none';
  const request=inputs();
  if(!request.addr||!request.city||!/^\d{5}$/.test(request.zip)||!request.date){note('Tax unverified — enter street, city, five-digit ZIP and invoice date.',true);return;}
  note('Checking the property with WA DOR…');
  timer=setTimeout(async()=>{
   const requestController=new AbortController();controller=requestController;const timeout=setTimeout(()=>requestController.abort(),12000);
   try{
    const response=await fetch('/api/taxrate?'+new URLSearchParams(request),{signal:requestController.signal,cache:'no-store'});
    const data=await response.json();
    if(generation!==seq||!same(request,inputs()))return;
    if(!response.ok||!data.ok||!data.addressMatch||!data.verification){note(data.error||'DOR only matched a ZIP area. Correct the full property address before generating.',true);return;}
    result=data;matchedInput=request;
    el('taxrate').value=Number(data.rate).toFixed(2);if(typeof recalc==='function')recalc();
    if(data.needsConfirmation){
     el('tax-match-wrap').hidden=false;el('tax-match-wrap').style.display='flex';
     el('tax-match-label').textContent='DOR updated the address. Confirm this is the property: '+data.matchedAddress;
     note(data.rate.toFixed(2)+'% · '+data.period+' · location '+data.locationCode+' — confirm the matched property.');
    }else{confirmed=true;note('Verified '+data.rate.toFixed(2)+'% · '+data.period+' · location '+data.locationCode+' · '+data.matchedAddress);}
   }catch(error){if(generation===seq)note('Tax lookup unavailable. Retry before generating; the displayed rate is unverified.',true);}
   finally{clearTimeout(timeout);}
  },500);
 }
 function reviewKey(){
  if(typeof buildPricingPayload!=='function')return '';
  const P=buildPricingPayload();const base=P.rental_service_split?P.rental_total:P.ovSub;
  return JSON.stringify([inputs(),Number(value('taxrate')),base,P.ovTax]);
 }
 function manualReview(P){
  mount();const base=P.rental_service_split?P.rental_total:P.ovSub;
  const expected=Math.round((Number(base)*Number(value('taxrate'))/100+Number.EPSILON)*100)/100;
  const mismatch=(P.manualFields||[]).includes('tax')&&Math.abs(Number(P.ovTax)-expected)>0.02;
  if(el('tax-manual-wrap'))el('tax-manual-wrap').hidden=!mismatch;
  if(!mismatch)return {confirmed:false,reason:''};
  el('tax-manual-note').textContent='Manual tax $'+Number(P.ovTax).toFixed(2)+' differs from verified-rate tax $'+expected.toFixed(2)+'. Correct it or document why this exception is appropriate.';
  const checked=el('tax-manual-confirm').checked&&manualKey===reviewKey();
  if(!checked)el('tax-manual-confirm').checked=false;
  return {confirmed:checked&&value('tax-manual-reason').length>=10,reason:value('tax-manual-reason'),required:true};
 }
 function allowed(action,P){
  if(action==='saving a draft')return true;
  mount();
  if(!result||!matchedInput||!same(inputs(),matchedInput)||!confirmed||Number(value('taxrate'))!==result.rate||Date.now()/1000>=result.verification.expiresAt){
   note('Verify the property tax and confirm the DOR address before '+action+'.',true);el('tax-note')?.scrollIntoView?.({block:'center'});return false;
  }
  if(P){const review=manualReview(P);if(review.required&&!review.confirmed){note('Correct the manual tax or complete its review before '+action+'.',true);return false;}}
  return true;
 }
 function provenance(){return result&&same(inputs(),matchedInput)?{...result.verification,addressConfirmed:confirmed}:null;}
 function context(){
  if(!allowed('creating a payment link',typeof buildPricingPayload==='function'?buildPricingPayload():null))throw Error('Property tax must be verified first.');
  const x=inputs();let address=x.addr;
  if(x.city&&!address.toLowerCase().includes(x.city.toLowerCase()))address+=(address?', ':'')+x.city;
  if(x.zip&&!address.includes(x.zip))address+=' '+x.zip;
  return {address,city:x.city,zip:x.zip,invdate:x.date,taxrate:Number(value('taxrate')),tax_verification:provenance()};
 }
 function attach(data,draft=false){
  if(!draft){
   try {
    const current=context();
    for(const field of ['address','invdate','taxrate'])if(String(current[field])!==String(data[field]))throw Error('Invoice changed during generation. Verify it and try again.');
   } catch(error) { if(typeof setSt==='function')setSt('err',error.message);return null; }
  }
  data.tax_guard_version=1;data.tax_verification=provenance();
  if(typeof buildPricingPayload==='function')data.manual_tax_review=manualReview(buildPricingPayload());
  return data;
 }
 function artifactAllowed(data,P){
  if(!data||data.tax_verification?.proof!==provenance()?.proof||String(data.invnum)!==value('invnum-display')||Math.abs(Number(data.total_override)-P.grand)>0.02||Math.abs(Number(data.tax_override)-P.ovTax)>0.02){
   note('The invoice changed after the PDF was generated. Generate it again before sending.',true);return false;
  }
  return true;
 }
 root.SWJTaxGuard={lookup,allowed,provenance,context,attach,artifactAllowed,reset:lookup};
 document.addEventListener('DOMContentLoaded',()=>{mount();el('invdate')?.addEventListener('change',lookup);});
})(window);
