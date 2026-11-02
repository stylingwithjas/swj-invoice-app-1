/* Presentation only: all comparisons and CSVs arrive from one verified server scan. */
(function(){
  'use strict';
  const $=id=>document.getElementById(id);
  const endpoint='/api/webhook?stripe_recon=';
  let current=null,busy=false;
  function node(tag,text,className){const n=document.createElement(tag);if(text!=null)n.textContent=String(text);if(className)n.className=className;return n;}
  function setMessage(text,error=false){$('message').textContent=text;$('message').className='message'+(error?' error':'');}
  function clearReport(){current=null;$('report').hidden=true;$('results').replaceChildren();$('export').disabled=true;['matched-count','unmatched-charge-count','unmatched-item-count','mismatch-count'].forEach(id=>$(id).textContent='—');['scanned-at','review-note','coverage','basis'].forEach(id=>$(id).textContent='');$('limitations').replaceChildren();}
  async function request(action,options={}){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),65000);
    try{
      const response=await fetch(endpoint+action,{credentials:'same-origin',cache:'no-store',...options,signal:controller.signal});
      const data=await response.json();
      if(!response.ok){const e=new Error(data.error||'Report request failed.');e.status=response.status;throw e;}
      return data;
    }finally{clearTimeout(timer);}
  }
  function locked(session){
    clearReport();$('workspace').hidden=true;$('logout').hidden=true;
    $('login-panel').hidden=!session.configured;$('setup-panel').hidden=session.configured;
    if(!session.configured){
      let detail='The report stays locked until its server-side administrator access is configured. No Stripe or database records have been queried.';
      if(session.password_configured&&!session.stripe_configured)detail='The report administrator password is configured, but no livemode restricted Stripe key (STRIPE_RECONCILE_KEY) has been set yet. No Stripe or database records have been queried.';
      else if(!session.password_configured&&session.stripe_configured)detail='A Stripe key is configured, but the report administrator password (SWJ_REPORT_ADMIN_PASSWORD) is not. No Stripe or database records have been queried.';
      $('setup-detail').textContent=detail;
    }
  }
  function unlocked(){clearReport();$('workspace').hidden=false;$('logout').hidden=false;$('login-panel').hidden=true;$('setup-panel').hidden=true;setMessage('Ready. Run the reconciliation to compare Stripe against stored invoices for this period.');}
  async function session(){
    setMessage('Checking secure report access…');
    try{const s=await request('session');if(s.authenticated)unlocked();else{locked(s);setMessage(s.configured?'Sign in to view financial records.':'Secure access is not fully configured. No records have been queried.');}}
    catch(e){locked({configured:false});setMessage('Secure access could not be checked. Retry when the connection is available.',true);}
  }
  function currency(value){if(value==null)return 'Unavailable';const n=Number(value);return Number.isFinite(n)?n.toLocaleString('en-US',{style:'currency',currency:'USD'}):'Unavailable';}
  function periodName(value){const m=/^(\d{4})-(\d{2})$/.exec(value);return m?new Date(Number(m[1]),Number(m[2])-1,1).toLocaleDateString('en-US',{month:'long',year:'numeric'}):value;}
  const VIEW_KEYS={matched:'matched',unmatched_charges:'unmatched_charges',unmatched_items:'unmatched_items',mismatches:'mismatches'};
  function selectedRows(){
    if(!current)return [];
    const view=$('view').value;
    if(view==='all')return [].concat(
      current.matched.map(r=>({list:'matched',row:r})),
      current.unmatched_charges.map(r=>({list:'unmatched_charges',row:r})),
      current.unmatched_items.map(r=>({list:'unmatched_items',row:r})),
      current.mismatches.map(r=>({list:'mismatches',row:r})));
    return (current[VIEW_KEYS[view]]||[]).map(r=>({list:view,row:r}));
  }
  function valueItem(title,value){const item=node('div');item.append(node('small',title),node('span',value));return item;}
  function renderMatched(row){
    const detail=node('details',null,'card invoice'),summary=node('summary'),title=node('div',null,'invoice-title'),badges=node('div',null,'badges');
    title.append(node('h3',row.client||row.client_name||'Client name unavailable'),node('div','#'+(row.invnum||row.display_label)+' · '+(row.paid_date||row.date||'date unavailable'),'muted'));
    badges.append(node('span',row.status==='partial'?'Partial':'Matched','badge'));
    if(row.refunded_amount)badges.append(node('span','Refunded','badge warn'));
    summary.append(title,badges);detail.append(summary);
    const body=node('div',null,'invoice-body'),source=node('div',null,'source-grid');
    for(const [label,value] of [['Stored amount',currency(row.stored_amount)],['Charge amount',currency(row.charge_amount)],['Match basis',row.match_basis==='metadata'?'Metadata + amount':'Amount + date + client'],['Stripe charge ID',row.charge_id],['Metadata (invoice field)',row.metadata_invoice||'(empty)'],['Client name (Stripe)',row.client_name||'Unavailable']]){
      const cell=node('div');cell.append(node('span',label),node('strong',value));source.append(cell);
    }
    body.append(source);
    if(row.refunded_amount)body.append(node('p','Refunded '+currency(row.refunded_amount)+' — net received '+currency(row.net_amount)+'.','notice'));
    if(row.amount_is_grand_fallback)body.append(node('p','No stored paid amount on this invoice — the figure above is the printed grand total used as a fallback, not a recorded receipt amount.','muted'));
    detail.append(body);return detail;
  }
  function renderUnmatchedCharge(row){
    const detail=node('details',null,'card invoice'),summary=node('summary'),title=node('div',null,'invoice-title'),badges=node('div',null,'badges');
    title.append(node('h3',row.client_name||'Client name unavailable (Stripe)'),node('div','Charge '+row.charge_id+' · '+(row.date||'date unavailable'),'muted'));
    badges.append(node('span',row.candidates?'Ambiguous':'No invoice found','badge warn'));
    summary.append(title,badges);detail.append(summary);
    const body=node('div',null,'invoice-body'),source=node('div',null,'source-grid');
    for(const [label,value] of [['Charge amount',currency(row.charge_amount)],['Metadata (invoice field)',row.metadata_invoice||'(empty)'],['Status',row.status||'Unavailable']]){
      const cell=node('div');cell.append(node('span',label),node('strong',value));source.append(cell);
    }
    body.append(source);
    if(row.refunded_amount)body.append(node('p','Refunded '+currency(row.refunded_amount)+' — net received '+currency(row.net_amount)+'.','notice'));
    if(row.candidates)body.append(node('p',row.detail+' Candidates: '+row.candidates.join(', ')+'.','notice'));
    body.append(node('p','No stored invoice or extension record matches this charge — review for a missing record.','muted'));
    detail.append(body);return detail;
  }
  function renderUnmatchedItem(row){
    const detail=node('details',null,'card invoice'),summary=node('summary'),title=node('div',null,'invoice-title'),badges=node('div',null,'badges');
    title.append(node('h3',row.client||'Client name unavailable'),node('div','#'+(row.invnum||row.display_label)+' · '+(row.paid_date||'date unavailable'),'muted'));
    badges.append(node('span','Paid record, no charge','badge warn'));
    summary.append(title,badges);detail.append(summary);
    const body=node('div',null,'invoice-body'),source=node('div',null,'source-grid');
    for(const [label,value] of [['Kind',row.kind],['Stored paid amount',row.amount_is_grand_fallback?'no stored paid amount':currency(row.stored_amount)],['Paid date (stored)',row.paid_date||'Unavailable']]){
      const cell=node('div');cell.append(node('span',label),node('strong',value));source.append(cell);
    }
    body.append(source);
    if(row.amount_is_grand_fallback)body.append(node('p','No stored paid amount exists on this record — the printed grand total is never substituted as a receipt figure.','notice'));
    body.append(node('p','Marked paid in the stored record, but no Stripe charge matches it — review with the owner/CPA. Do not restate or regenerate from this finding.','muted'));
    detail.append(body);return detail;
  }
  function renderMismatch(row){
    const detail=node('details',null,'card invoice'),summary=node('summary'),title=node('div',null,'invoice-title'),badges=node('div',null,'badges');
    title.append(node('h3',row.client||row.client_name||'Client name unavailable'),node('div','#'+(row.invnum||row.display_label)+' · charge '+row.charge_id,'muted'));
    badges.append(node('span','Mismatch','badge warn'));
    summary.append(title,badges);detail.append(summary);
    const body=node('div',null,'invoice-body');
    const values=node('div',null,'values');
    values.append(valueItem('Stored amount',currency(row.stored_amount)),valueItem('Charge amount',currency(row.charge_amount)));
    const delta=valueItem('Difference',currency(row.difference));delta.className='difference';values.append(delta);
    body.append(values);
    if(row.detail)body.append(node('p',row.detail));
    detail.append(body);return detail;
  }
  const RENDERERS={matched:renderMatched,unmatched_charges:renderUnmatchedCharge,unmatched_items:renderUnmatchedItem,mismatches:renderMismatch};
  function renderList(){
    const rows=selectedRows();$('visible-count').textContent=rows.length+' item'+(rows.length===1?'':'s');
    $('results').replaceChildren(...rows.map(({list,row})=>RENDERERS[list](row)));
    if(!rows.length)$('results').append(node('div','No findings in this view.','empty'));
    $('export').disabled=!current;
  }
  function render(report){
    if(!report.coverage?.complete||!report.exports)throw new Error('A complete verified result was not returned. No totals can be shown.');
    current=report;$('report').hidden=false;
    $('matched-count').textContent=String(report.matched_count);
    $('unmatched-charge-count').textContent=String(report.unmatched_charge_count);
    $('unmatched-item-count').textContent=String(report.unmatched_item_count);
    $('mismatch-count').textContent=String(report.mismatch_count);
    const period=periodName(report.review_period);
    $('scanned-at').textContent='Read completed '+new Date(report.coverage.completed_at).toLocaleString();
    $('review-note').textContent=report.unmatched_charge_count+' Stripe charge(s) with no matching invoice, '+report.unmatched_item_count+' paid invoice/extension record(s) with no matching charge, and '+report.mismatch_count+' amount/status mismatch(es) overall. '
      +(report.test_charge_count?report.test_charge_count+' test-mode charge(s) and ':'')+(report.gs_charge_count?report.gs_charge_count+' GS Photography charge(s) excluded. ':'')
      +(report.unconfirmed_livemode_count?report.unconfirmed_livemode_count+' charge(s) had an unconfirmable livemode flag and are excluded from every list above. ':'')
      +'Findings never authorize a charge, refund, or change to a stored record.';
    $('coverage').textContent='Invoice side: '+report.invoices_considered+' paid records/splits/extensions considered. Stripe side: '+report.live_charges_considered+' livemode SWJ charges in the scanned window ('+period+' plus a two-week pad). '+report.coverage.verification+'.';
    $('basis').textContent=report.comparison_basis;
    $('limitations').replaceChildren(...report.limitations.map(t=>node('li',t)));renderList();
    setMessage('Reconciliation complete. This is a read-only snapshot.');
  }
  $('login-form').addEventListener('submit',async e=>{
    e.preventDefault();if(busy)return;busy=true;$('login-button').disabled=true;
    const password=$('password').value;$('password').value='';
    try{await request('login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password})});unlocked();}
    catch(e){setMessage(e.message,true);}finally{busy=false;$('login-button').disabled=false;}
  });
  $('scan-form').addEventListener('submit',async e=>{
    e.preventDefault();if(busy||!$('scan-form').reportValidity())return;busy=true;clearReport();$('scan-button').disabled=true;$('period').disabled=true;
    setMessage('Reading Stripe and the stored invoices, and verifying complete coverage…');$('message').classList.add('loading');
    try{render(await request('report&period='+encodeURIComponent($('period').value)));}
    catch(e){clearReport();if(e.status===401)locked({configured:true});setMessage(e.name==='AbortError'?'The scan timed out. No complete result is available; retry.':e.message,true);}
    finally{busy=false;$('scan-button').disabled=false;$('period').disabled=false;$('message').classList.remove('loading');}
  });
  $('view').addEventListener('change',renderList);
  $('period').addEventListener('change',()=>{clearReport();setMessage('Review month changed. Run the reconciliation again.');});
  $('export').addEventListener('click',()=>{
    if(!current)return;const view=$('view').value,csv=current.exports[view];if(typeof csv!=='string')return;
    const url=URL.createObjectURL(new Blob(['﻿',csv],{type:'text/csv;charset=utf-8'}));const a=node('a');a.href=url;a.download='SWJ_Stripe_Reconciliation_'+current.review_period+'_'+view+'.csv';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  $('logout').addEventListener('click',async()=>{
    if(busy)return;busy=true;
    try{await request('logout',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});locked({configured:true});setMessage('Signed out of this report.');}
    catch(e){clearReport();setMessage('Sign-out could not be confirmed. Close this page and retry; the server session expires after 30 minutes.',true);}
    finally{busy=false;}
  });
  $('retry').addEventListener('click',session);
  window.addEventListener('pagehide',clearReport);
  session();
})();
