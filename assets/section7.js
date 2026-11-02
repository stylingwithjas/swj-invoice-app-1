/* Presentation only: all comparisons and CSVs arrive from one verified server scan. */
(function(){
  'use strict';
  const $=id=>document.getElementById(id);
  const endpoint='/api/webhook?section7=';
  let current=null,busy=false;
  const names={lines_subtotal:'Visible lines versus subtotal',tax_base:'Tax versus claimed taxable base',subtotal_tax_total:'Subtotal + tax versus grand total',extension_rate:'Extension rate versus pretax subtotal / 3',missing_input:'More information needed',split_totals:'Rental + service totals versus lines',line_split:'Per-line split versus price',split_lines_vs_totals:'Per-line detail versus split totals',split_presentation:'Split presentation differs',extension_tax:'Extension tax versus fee',extension_total:'Extension fee + tax versus amount',extension_breakdown:'Extension breakdown unavailable',payment_context:'Payment context needs review',payment_timing:'Receipt date unavailable',document_type:'Separate document needs parent context'};
  function node(tag,text,className){const n=document.createElement(tag);if(text!=null)n.textContent=String(text);if(className)n.className=className;return n;}
  function setMessage(text,error=false){$('message').textContent=text;$('message').className='message'+(error?' error':'');}
  function clearReport(){current=null;$('report').hidden=true;$('results').replaceChildren();$('export').disabled=true;['checked','discrepancies','unverifiable','period-discrepancies'].forEach(id=>$(id).textContent='—');['scanned-at','review-note','coverage','basis','fingerprint'].forEach(id=>$(id).textContent='');$('limitations').replaceChildren();}
  async function request(action,options={}){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),65000);
    try{
      const response=await fetch(endpoint+action,{credentials:'same-origin',cache:'no-store',...options,signal:controller.signal});
      const data=await response.json();
      if(!response.ok){const e=new Error(data.error||'Report request failed.');e.status=response.status;throw e;}
      return data;
    }finally{clearTimeout(timer);}
  }
  function locked(configured){clearReport();$('workspace').hidden=true;$('logout').hidden=true;$('login-panel').hidden=!configured;$('setup-panel').hidden=configured;}
  function unlocked(){clearReport();$('workspace').hidden=false;$('logout').hidden=false;$('login-panel').hidden=true;$('setup-panel').hidden=true;setMessage('Ready. Run the check to read all stored historical invoices.');}
  async function session(){
    setMessage('Checking secure report access…');
    try{const s=await request('session');if(s.authenticated)unlocked();else{locked(s.configured);setMessage(s.configured?'Sign in to view financial records.':'Secure access is not configured. No records have been queried.');}}
    catch(e){locked(false);setMessage('Secure access could not be checked. Retry when the connection is available.',true);}
  }
  function currency(value){if(value==null)return 'Unavailable';const n=Number(value);return Number.isFinite(n)?n.toLocaleString('en-US',{style:'currency',currency:'USD'}):'Unavailable';}
  function periodName(value){const m=/^(\d{4})-(\d{2})$/.exec(value);return m?new Date(Number(m[1]),Number(m[2])-1,1).toLocaleDateString('en-US',{month:'long',year:'numeric'}):value;}
  function selectedRows(){
    if(!current)return [];
    return current.results.filter(r=>$('view').value==='all'||($('view').value==='paid'&&r.live_paid)||($('view').value==='paid_period'&&r.paid_in_review_period)||($('view').value==='unverifiable'&&r.has_unverifiable));
  }
  function valueItem(title,value){const item=node('div');item.append(node('small',title),node('span',value));return item;}
  function renderRow(row){
    const detail=node('details',null,'card invoice'),summary=node('summary'),title=node('div',null,'invoice-title'),badges=node('div',null,'badges');
    title.append(node('h3',row.client||'Client name unavailable'),node('div','#'+row.invnum+' · '+(row.invdate||'Invoice date unavailable')+' · '+row.pricing_mode.replaceAll('_',' '),'muted'));
    if(row.has_discrepancy)badges.append(node('span','Discrepancy','badge warn'));
    if(row.has_unverifiable)badges.append(node('span','Needs data','badge neutral'));
    if(row.live_paid)badges.append(node('span','Recorded paid','badge'));
    else if(row.test_paid)badges.append(node('span','Test payment','badge neutral'));
    summary.append(title,badges);detail.append(summary);
    const body=node('div',null,'invoice-body'),source=node('div',null,'source-grid');
    for(const [label,key]of [['Visible lines','visible_lines'],['Subtotal','subtotal'],['Tax','tax'],['Grand total','grand'],['Claimed taxable base','taxable_base']]){
      const cell=node('div');cell.append(node('span',label),node('strong',currency(row.values[key])));source.append(cell);
    }
    body.append(source,node('p',row.override_fields.length?'Stored total fields: '+row.override_fields.join(', ')+'. Intent: '+row.override_intent+'.':'No stored total overrides.','muted'));
    body.append(node('p','Sources — subtotal: '+(row.values.subtotal_source||'unavailable')+'; tax: '+(row.values.tax_source||'unavailable')+'; grand: '+(row.values.grand_source||'unavailable')+'. '+(row.values.taxable_base_source||''),'muted'));
    if(row.paid_date_unknown)body.append(node('p','A recorded payment is missing a valid receipt date. The review period cannot be fully established.','notice'));
    row.issues.forEach(f=>{
      const item=node('div',null,'finding');item.append(node('h4',(names[f.rule]||f.rule)+(f.scope!=='invoice'?' · '+f.scope:'')));
      if(f.actual!=null||f.comparison!=null){const values=node('div',null,'values');values.append(valueItem('Existing / reconstructed',currency(f.actual)),valueItem('Comparison, not correction',currency(f.comparison)));const delta=valueItem('Difference',currency(f.difference));delta.className='difference';values.append(delta);item.append(values);}
      if(f.kind==='unverifiable')item.append(node('p','Not enough recorded information to verify this check.'));
      if(f.detail)item.append(node('p',f.detail));body.append(item);
    });
    if(row.live_paid)body.append(node('p','Paid record: review with the owner/CPA. Do not restate or regenerate from this finding.','muted'));
    detail.append(body);return detail;
  }
  function renderList(){
    const rows=selectedRows();$('visible-count').textContent=rows.length+' invoice'+(rows.length===1?'':'s');$('results').replaceChildren(...rows.map(renderRow));
    if(!rows.length)$('results').append(node('div',current.flagged_count===0?'No discrepancies or missing-input findings in the stored invoices scanned. Records absent from the database cannot be checked.':'No flagged invoices match this view. Other views may contain findings.','empty'));
    $('export').disabled=!current;
  }
  function render(report){
    if(!report.coverage?.complete||!Array.isArray(report.results)||!report.exports)throw new Error('A complete verified result was not returned. No totals can be shown.');
    current=report;$('report').hidden=false;
    for(const [id,key]of [['checked','invoices_checked'],['discrepancies','discrepancy_count'],['unverifiable','unverifiable_count'],['period-discrepancies','paid_period_discrepancy_count']])$(id).textContent=String(report[key]);
    const period=periodName(report.review_period);$('period-label').textContent='Paid in '+period;
    $('scanned-at').textContent='Read completed '+new Date(report.coverage.completed_at).toLocaleString();
    $('review-note').textContent=(report.paid_period_discrepancy_count?report.paid_period_discrepancy_count+' recorded-paid invoice(s) with receipts in '+period+' have discrepancies for owner/CPA review.':'No arithmetic discrepancies were found on invoices with recorded receipts in '+period+'.')
      +(report.paid_period_unverifiable_count?' '+report.paid_period_unverifiable_count+' period-matched paid record(s) still need information to verify their figures.':'')
      +(report.paid_date_unknown_count?' '+report.paid_date_unknown_count+' paid record(s) have missing dates; period matching is incomplete.':'')+' Findings never authorize changing a paid invoice.';
    $('coverage').textContent=report.records_read+' database rows read; '+report.invoices_checked+' SWJ invoices checked; '+report.excluded_records+' known non-invoice/draft/GS rows excluded. '+report.coverage.verification+'. '+report.coverage.snapshot_note;
    $('basis').textContent=report.comparison_basis;$('fingerprint').textContent='Read fingerprint: '+report.fingerprint;
    $('limitations').replaceChildren(...report.limitations.map(t=>node('li',t)));renderList();
    setMessage(report.data_source==='synthetic_test'?'SYNTHETIC TEST DATA — these are not live business findings.':'Check complete. This is a read-only snapshot of the stored records.');
  }
  $('login-form').addEventListener('submit',async e=>{
    e.preventDefault();if(busy)return;busy=true;$('login-button').disabled=true;
    const password=$('password').value;$('password').value='';
    try{await request('login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password})});unlocked();}
    catch(e){setMessage(e.message,true);}finally{busy=false;$('login-button').disabled=false;}
  });
  $('scan-form').addEventListener('submit',async e=>{
    e.preventDefault();if(busy||!$('scan-form').reportValidity())return;busy=true;clearReport();$('scan-button').disabled=true;$('period').disabled=true;
    setMessage('Scanning every stored invoice and verifying complete coverage…');$('message').classList.add('loading');
    try{render(await request('report&period='+encodeURIComponent($('period').value)));}
    catch(e){clearReport();if(e.status===401)locked(true);setMessage(e.name==='AbortError'?'The scan timed out. No complete result is available; retry.':e.message,true);}
    finally{busy=false;$('scan-button').disabled=false;$('period').disabled=false;$('message').classList.remove('loading');}
  });
  $('view').addEventListener('change',renderList);
  $('period').addEventListener('change',()=>{clearReport();setMessage('Review period changed. Run the historical check again.');});
  $('export').addEventListener('click',()=>{
    if(!current)return;const view=$('view').value,csv=current.exports[view];if(typeof csv!=='string')return;
    const url=URL.createObjectURL(new Blob(['\ufeff',csv],{type:'text/csv;charset=utf-8'}));const a=node('a');a.href=url;a.download='SWJ_Historical_Reconciliation_'+current.review_period+'_'+view+'_'+current.fingerprint.slice(0,8)+'.csv';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  $('logout').addEventListener('click',async()=>{
    if(busy)return;busy=true;
    try{await request('logout',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});locked(true);setMessage('Signed out of this report.');}
    catch(e){clearReport();setMessage('Sign-out could not be confirmed. Close this page and retry; the server session expires after 30 minutes.',true);}
    finally{busy=false;}
  });
  $('retry').addEventListener('click',session);
  window.addEventListener('pagehide',clearReport);
  session();
})();
