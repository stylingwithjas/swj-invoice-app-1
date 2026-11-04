/* One recorded-payment driver. Deposits and signatures are not receipt events. */
(function(root){'use strict';
function base(d,grand){
 if(d.is_test)return [];
 const splits=Array.isArray(d.payments)?d.payments:[];
 if(splits.length)return splits.filter(p=>p&&p.status==='paid'&&p.paid_date&&!p.is_test).map(p=>({date:p.paid_date,amount:Number(p.amount)||0,method:(p.payment_id||p.manual_method==='stripe')?'Stripe (Card)':'Other/Manual',label:p.label||'Payment'}));
 if(d.paid&&d.paid_date)return [{date:d.paid_date,amount:Number(d.amount_paid??d.stripe_amount??grand(d))||0,method:(d.stripe_payment_id||d.manual_method==='stripe')?'Stripe (Card)':'Other/Manual',label:'Full Payment'}];
 return [];
}
function all(inv,grand){if(!inv||/^(DRAFT-|GS|TAX_REVIEW_)/.test(inv.invnum)||['PRICING_DEFAULTS','TIER_PRICING','FINANCE_CONFIG','FINANCE_OVERRIDES'].includes(inv.invnum))return [];const d=inv.data||{};if(d.is_test)return [];return [...base(d,grand),...(Array.isArray(d.extensions)?d.extensions:[]).filter(e=>e&&e.paid&&e.paid_date&&!e.is_test).map(e=>({date:e.paid_date,amount:Number(e.amount)||0,label:'Extension',method:e.stripe_payment_id?'Stripe (Card)':'Other/Manual'}))];}
root.SWJPayments={base,all};
})(typeof window==='undefined'?globalThis:window);
