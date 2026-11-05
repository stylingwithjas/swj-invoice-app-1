/* Numeric staging invoices and their vN revisions only; never drafts/system records. */
(function(root){
 function next(rows,fallback=202093){
  let max=0;
  for(const row of rows||[]){
   const match=String(row.invnum||'').match(/^(\d+)(?:v\d+)?$/i);
   if(match){const n=Number(match[1]);if(Number.isSafeInteger(n)&&n>max)max=n;}
  }
  return max>0&&Number.isSafeInteger(max+1)?max+1:fallback;
 }
 let held=null,existing=null,initial=null,epoch=0,inflight=null;
 const field=()=>document.getElementById('invnum-display');
 function status(message){const note=document.getElementById('invnum-note');if(note){note.textContent=message;note.setAttribute('role','status');}}
 async function request(requested){
  if(root.swjOwnerReady && !(await root.swjOwnerReady))throw Error('Sign in before reserving a number.');
  const request_id=crypto.randomUUID();
  // The same UUID is reused for a lost response, never for another page/tab.
  for(let attempt=0;attempt<2;attempt++){
   const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),35000);
   try{
    const response=await fetch('/api/counter',{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'reserve',request_id,requested_invnum:requested||null}),signal:controller.signal});
    const result=await response.json();
    if(!response.ok){const error=Error(result.error||'Could not reserve invoice number.');error.noRetry=true;throw error;}
    if(!result.invnum||!result.proof||result.request_id!==request_id)throw Error('Invalid reservation response.');
    return result;
   }catch(error){if(error.noRetry||attempt===1)throw error;}
   finally{clearTimeout(timeout);}
  }
 }
 async function initialize(){
  if(inflight)return inflight.then(()=>{try{return context();}catch{return null;}});
  if(initial)return initial;
  try{return Promise.resolve(context());}catch{}
  const version=epoch;const input=field();const before=input?.value||'';
  status('Reserving invoice number…');
  initial=request(null).then(result=>{
   if(epoch===version){
    held=result;
    if(!field()?.value && !before && field())field().value=result.invnum;
    status(field()?.value===result.invnum?'Reserved · tap to edit if needed':'Edited number · checked before output');
   }
   return result;
  }).catch(error=>{status(error.message);return null;});
  return initial;
 }
 function loaded(data,options={}){
  epoch++;
  held=data.invoice_number?.mode==='allocated'?data.invoice_number:null;
  const draft=options.draft===undefined?!!data._is_draft:options.draft;
  existing=!draft?String(data.invnum||''):null;
  status(existing?'Existing invoice · number preserved':'Draft · number checked before output');
 }
 function context(){
  const value=field()?.value.trim()||'';
  if(held?.invnum===value)return held;
  if(existing && existing===value)return {mode:'existing',invnum:existing};
  throw Error('Reserve this invoice number before continuing.');
 }
 async function ensure(){
  if(inflight)return inflight;
  inflight=(async()=>{
   if(initial)await initial;
   try{context();return true;}catch{}
   const desired=field()?.value.trim()||'';const version=epoch;
   try{
    const result=await request(desired||null);
    // Never replace a number changed or an invoice loaded while this was pending.
    if(epoch!==version || (field()?.value.trim()||'')!==desired)throw Error('Invoice changed while reserving. Try again.');
    held=result;existing=null;if(field())field().value=result.invnum;
    status('Reserved · tap to edit if needed');return true;
   }catch(error){status(error.message);if(typeof setSt==='function')setSt('err',error.message);return false;}
  })();
  try{return await inflight;}finally{inflight=null;}
 }
 function attach(data){
  try{const metadata=context();if(String(data.invnum)!==metadata.invnum)throw Error('Invoice number changed. Try again.');data.invoice_number_version=1;data.invoice_number={...metadata};return data;}
  catch(error){status(error.message);return null;}
 }
 root.SWJInvoiceNumber={next,initialize,ensure,loaded,context,attach};
})(typeof window==='undefined'?globalThis:window);
