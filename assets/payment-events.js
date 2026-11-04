/* Option A: recorded paid events only. Signature, deposits and transfers never create receipts. */
(function(root){'use strict';
const basis='Revenue = recorded paid invoice payments and paid extensions, by received month, regardless of signature. Stripe and checking deposits reconcile settlement only.';
function money(value){if(value==null||value===''||typeof value==='boolean')return null;const n=Number(value);return Number.isFinite(n)&&Math.abs(n)<=1e9?Math.round((n+Number.EPSILON)*100)/100:null;}
function date(value){const text=String(value||'');if(!/^\d{4}-\d{2}-\d{2}$/.test(text))return null;const d=new Date(text+'T00:00:00Z');return Number.isFinite(d.getTime())&&d.toISOString().slice(0,10)===text?text:null;}
function event(raw,amount,kind){const received=date(raw.paid_date),n=money(amount);if(!received||n==null||n<=0)return null;return {date:received,amount:n,kind,method:(raw.payment_id||raw.stripe_payment_id||raw.manual_method==='stripe')?'Stripe (Card)':'Other/Manual',label:raw.label||(kind==='extension'?'Extension':'Full Payment')};}
function base(d,grand){
 if(!d||d.is_test)return [];
 const splits=Array.isArray(d.payments)?d.payments:[];
 if(splits.length)return splits.filter(p=>p&&p.status==='paid'&&!p.is_test).map(p=>event(p,p.amount,'payment')).filter(Boolean);
 if(d.paid!==true)return [];
 let amount=null;for(const value of [d.amount_paid,d.stripe_amount]){amount=money(value);if(amount!=null)break;}
 if(amount==null)amount=grand(d);
 const receipt=event(d,amount,'payment');return receipt?[receipt]:[];
}
function extensions(d){if(!d||d.is_test)return [];return (Array.isArray(d.extensions)?d.extensions:[]).filter(e=>e&&e.paid===true&&!e.is_test).map(e=>{const receipt=event(e,e.amount,'extension');return receipt?{...e,...receipt,paid_date:receipt.date}:null;}).filter(Boolean);}
function all(inv,grand){if(!inv||/^(DRAFT-|GS|TAX_REVIEW_)/.test(inv.invnum)||['PRICING_DEFAULTS','TIER_PRICING','FINANCE_CONFIG','FINANCE_OVERRIDES'].includes(inv.invnum))return [];const d=inv.data||{};return [...base(d,grand),...extensions(d)];}
function inPeriod(inv,grand,start,end){return all(inv,grand).filter(e=>e.date>=start&&e.date<=end);}
function total(events){return events.reduce((sum,e)=>sum+Math.round(e.amount*100),0)/100;}
root.SWJPayments={base,extensions,all,inPeriod,total,basis};
})(typeof window==='undefined'?globalThis:window);
