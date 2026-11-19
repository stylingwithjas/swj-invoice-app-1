/* PR 63 follow-up: shared diagnostics only. No source writes or communications. */
(function(root){
  'use strict';
  const round = n => Math.round((n + Number.EPSILON) * 100) / 100;
  const present = v => v !== null && v !== undefined && v !== '';
  function isPaid(d){
    return !!(d && (d.paid === true || Number(d.amount_paid)>0 || Number(d.stripe_amount)>0 ||
      (d.payments||[]).some(p=>p.status==='paid')));
  }
  function reconcile({linesSum,sub,tax,grand,extrate,taxableBase,rate,rawOvTax,split}){
    const issues=[], eps=0.02, fmt=n=>Number(n).toFixed(2);
    if(![linesSum,sub,tax,grand,taxableBase,rate].every(Number.isFinite)){
      return ['A required pricing figure is missing or invalid.'];
    }
    if(Math.abs(linesSum-sub)>eps) issues.push(`Subtotal $${fmt(sub)} does not match the sum of lines $${fmt(linesSum)}.`);
    if(!present(rawOvTax) && Math.abs(tax-round(taxableBase*rate/100))>eps)
      issues.push(`Tax $${fmt(tax)} does not match ${rate}% of the taxable base $${fmt(taxableBase)} ($${fmt(round(taxableBase*rate/100))}).`);
    if(Math.abs(sub+tax-grand)>eps) issues.push(`Subtotal + tax ($${fmt(sub+tax)}) does not match the grand total ($${fmt(grand)}). Clear or correct Manual Totals before continuing.`);
    // Nov 18, 2026 split-override spec, Option A: a split invoice's extension rate follows
    // the rental portion (taxableBase × 2/3), not the full pretax subtotal (sub/3) -- the two
    // agree exactly at the standard 50% split, where taxableBase is already sub/2, so nothing
    // changes for a standard invoice. `split` must be the same split flag the invoice itself
    // was built with (rental_service_split), not re-derived here.
    const expectedExtrate = split ? round(taxableBase*2/3) : round(sub/3);
    if(extrate!=null && (!Number.isFinite(extrate)||Math.abs(extrate-expectedExtrate)>eps))
      issues.push(`Extension rate $${fmt(extrate)} does not match ${split?'the rental portion × 2/3':'pretax subtotal / 3'} ($${fmt(expectedExtrate)}).`);
    return issues;
  }
  function term(raw){
    const n=Number(raw);
    return {value:n,valid:String(raw).trim()!=='' && Number.isFinite(n) && Number.isInteger(n) && n>0,
      note:n===90?'90-day term: all room charges are rental; no service split is shown for zero-dollar design lines.':''};
  }
  function totals({lines,rental,rate,split,ovSub,ovTax,ovTot}){
    const subtotal=present(ovSub)?Number(ovSub):lines;
    const taxableBase=split?rental:subtotal;
    const tax=present(ovTax)?Number(ovTax):round(taxableBase*rate/100);
    const grand=present(ovTot)?Number(ovTot):round(subtotal+tax);
    return {subtotal,tax,grand,taxableBase,blocked:!Number.isFinite(grand)||Math.abs(subtotal+tax-grand)>0.02};
  }
  function extension({fee,tax,total,rate}){
    const issues=[];
    if(![fee,tax,total,rate].every(Number.isFinite)) return ['Extension figures are invalid. Nothing will be charged.'];
    if(Math.abs(round(fee*rate/100)-tax)>0.02) issues.push('Extension tax does not match the stated rental fee and tax rate.');
    if(Math.abs(round(fee+tax)-total)>0.02) issues.push('Extension fee + tax does not match the amount to charge.');
    return issues;
  }
  root.SWJInvoiceSafety={round,present,isPaid,reconcile,term,totals,extension};
})(typeof window==='undefined'?globalThis:window);
