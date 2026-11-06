(function(){
'use strict';
const FINANCE_DEFAULTS = {
  warehouse_monthly: 1473,
  truck_per_job: 510,
  profit_target_pct: 25,       // Jasmine keep TARGET, as % of gross revenue (30% for 2027 jobs per pricing decision)
  tithe_pct: 10,                // of Project keep estimate before shared work (target), not of revenue or operating net
  stripe_pct: 2.9,
  stripe_flat: 0.30,
  mover_plan_staging: 400,     // planning-average estimate per job, used before actual hours are logged
  mover_plan_destage: 160,     // staging $400 + destage $160 = $560/job; warehouse labor is inside these two, never added on top
  mover_ideal_pct: 15,         // benchmark: mover cost as a % of job revenue
  shared_labor_normal_per_job: 75, // normal shared/warehouse-overhead labor per job in a non-special month (top of the $50-75 range) — above this × job count, a month auto-flags as one-off/special
};
function finCategorize(desc){
  const s=(desc||'').toLowerCase();
  // Fix spec (Nov 2, 2026): transport (delivery/pickup) is explicitly rental/taxable even
  // though it's already the default; design/arranging/placement is explicitly service.
  if(s.includes('delivery')||s.includes('pickup')||s.includes('pick up')||s.includes('transport')) return 'retailing';
  return (s.includes('photo')||s.includes('consult')||s.includes('design')||s.includes('arrang')||s.includes('placement')) ? 'service' : 'retailing';
}
function finInvoiceSplit(d){
  const _ov=v=>(v!=null&&v!=='')?parseFloat(v):null;
  const hasOverride = _ov(d.subtotal_override)!=null || _ov(d.tax_override)!=null || _ov(d.total_override)!=null;
  const base = parseFloat(d.base_price)||2500;
  const addons = d.addons||[];
  const addonTotal = addons.reduce((s,a)=>s+(parseFloat(a.price)||0),0);
  const rate = parseFloat(d.taxrate)||0;
  if(hasOverride || !d.price_led_tax){
    const subtotal = _ov(d.subtotal_override) ?? (base+addonTotal);
    const tax = _ov(d.tax_override) ?? (subtotal*rate/100);
    const grand = _ov(d.total_override) ?? (subtotal+tax);
    if(d.rental_service_split && d.rental_total!=null){
      const rental = Number(d.rental_total)||0;
      const service = Number(d.service_total)||0;
      return {subtotal,tax,grand,rentalShare:rental+service>0.009?rental/(rental+service):1};
    }
    let svc=0, ret=d.pricing_mode==='rate_card'?0:base;
    addons.forEach(a=>{ const amt=parseFloat(a.price)||0; if((a.type||finCategorize(a.desc))==='service') svc+=amt; else ret+=amt; });
    const lineTotal = svc+ret;
    const rentalShare = lineTotal>0.009 ? ret/lineTotal : 1;
    return { subtotal, tax, grand, rentalShare };
  }
  const grand = base+addonTotal+(base+addonTotal)*rate/100;
  const rsb = parseFloat(d.rental_share_base); const rentalShareBase = isNaN(rsb)?0.5:rsb;
  const rentalDollars = base*rentalShareBase + addons.filter(a=>a.type!=='service').reduce((s,a)=>s+(parseFloat(a.price)||0),0);
  const allDollars = base+addonTotal;
  const rentalShare = allDollars>0.009 ? rentalDollars/allDollars : 1;
  const r = rate/100;
  const subtotal = r>0 ? grand/(1+r*rentalShare) : grand;
  return { subtotal, tax: grand-subtotal, grand, rentalShare };
}
function finPaymentEvents(d){
  return SWJPayments.base(d,finComputeGrandForEvents);
}
// finInvoiceSplit(d) needs `d`'s own price-led flags, which is already in scope wherever
// finPaymentEvents is called with the right `d` — just needs the grand total here.
function finComputeGrandForEvents(d){ return finInvoiceSplit(d).grand; }

function planCents(value){ return Math.round((Number(value)||0)*100); }
function financeConfig(data){
  const config={...FINANCE_DEFAULTS,...data};
  if(Number(config.warehouse_monthly)===1380) config.warehouse_monthly=FINANCE_DEFAULTS.warehouse_monthly;
  return config;
}
function calculateFinanceMonth(period,periodStart,periodEnd,invoices,paymentBudget,periodEntries,transactions,FINANCE_CONFIG,overrides){
  const byInvoice = new Map(invoices.map(inv=>[inv.invnum, inv]));
  if(paymentBudget.events.some(event=>!byInvoice.has(event.invnum))){
    throw Error('Payment records changed while loading. Retry Run to verify the budget.');
  }
  const excludeSet = new Set(overrides.excludeInvnums||[]);
  const includeSet = new Set(overrides.includeInvnums||[]);
  // Revenue rule (confirmed with Gabe, Nov 2026): a paid job counts regardless of whether the
  // contract is signed yet — signature status no longer gates revenue here, in Monthly
  // Performance, or in the Board MTD strip (which already worked this way). excludeInvnums/
  // includeInvnums overrides still let a specific job be pulled out of or forced into a month.
  // Planning exclusions affect upcoming jobs/budgets, never recorded receipts.
  const counted = invoices.filter(inv=>!excludeSet.has(inv.invnum));
  invoices.forEach(inv=>{ if(includeSet.has(inv.invnum) && !counted.some(x=>x.invnum===inv.invnum)) counted.push(inv); });

  // Paid without a signed contract on file — still counted in Revenue (signature doesn't gate
  // revenue), but surfaced as a paperwork follow-up so a missing signature doesn't get lost.
  const paidButUnsigned = invoices.filter(inv=>{
    const d = inv.data||{};
    if(d.signed) return false;
    const hasPaidEvent = finPaymentEvents(d).some(e=>e.date>=periodStart && e.date<=periodEnd);
    return hasPaidEvent;
  }).map(inv=>`${inv.client||'Client'} #${inv.invnum}`);

  // Budget funding moves by payment. Actual receipts and physical costs retain their periods.
  const revenue = paymentBudget.budgeted_cents / 100;
  const cashReceipts = paymentBudget.received_cents / 100;
  const received = paymentBudget.events.filter(e=>e.budget_month===period);
  // Reserves and processing fees stay with the receipt month when funding is moved.
  const receiptCostsByJob = new Map();
  for(const event of paymentBudget.events.filter(e=>e.date.slice(0,7)===period)){
    const data = byInvoice.get(event.invnum).data || {};
    const amount = event.amount_cents/100;
    const split = finInvoiceSplit(data);
    const rate = (Number(data.taxrate)||0)/100;
    const extension = event.kind==='extension';
    const tax = data.exempt_reason ? 0 : extension
      ? amount-amount/(1+rate) : split.grand>0.009 ? amount*split.tax/split.grand : 0;
    const share = extension ? 1 : split.rentalShare;
    const cost = receiptCostsByJob.get(event.invnum) || {salesTax:0,boEstimate:0,stripeFee:0};
    cost.salesTax += tax;
    cost.boEstimate += (amount-tax)*(share*0.00471+(1-share)*0.015);
    if(event.method==='Stripe (Card)') cost.stripeFee += planCents(amount*FINANCE_CONFIG.stripe_pct/100+FINANCE_CONFIG.stripe_flat)/100;
    receiptCostsByJob.set(event.invnum,cost);
  }
  const sumReceiptCost = key => planCents([...receiptCostsByJob.values()].reduce((sum,cost)=>sum+cost[key],0))/100;
  const salesTax = sumReceiptCost('salesTax');
  const boEstimate = sumReceiptCost('boEstimate');
  const stripeFee = sumReceiptCost('stripeFee');

  // ---- Expected (unpaid balance, staged by this period's end, overdue if staged earlier) and
  // Committed (unpaid balance, staged more than 60 days out from TODAY — a forward strip that
  // doesn't depend on which month is picked, so a signed-but-not-yet-due job is never invisible). ----
  const expected = [], committed = [];
  const today = new Date().toISOString().split('T')[0];
  const in60 = new Date(); in60.setDate(in60.getDate()+60);
  const in60Str = in60.toISOString().split('T')[0];
  counted.forEach(inv=>{
    const d = inv.data||{};
    if(!d.startdate || d.startdate==='TBD') return;
    const split = finInvoiceSplit(d);
    const paidSoFar = (d.payments||[]).length
      ? (d.payments||[]).filter(p=>p.status==='paid').reduce((s,p)=>s+(parseFloat(p.amount)||0),0)
      : (d.paid ? split.grand : 0);
    const balance = Math.round(Math.max(0, split.grand - paidSoFar)*100)/100;
    if(balance<=0.009) return;
    if(d.startdate<=periodEnd){
      expected.push({ invnum:inv.invnum, client:inv.client||'Client', balance, startdate:d.startdate, overdue:d.startdate<periodStart });
    }
    if(d.startdate>today && d.startdate<=in60Str){
      committed.push({ invnum:inv.invnum, client:inv.client||'Client', balance, startdate:d.startdate, data:d });
    }
  });
  const noStagingDate = counted.filter(inv=>{
    const d = inv.data||{};
    return (!d.startdate || d.startdate==='TBD') && !d.paid;
  }).map(inv=>`${inv.client||'Client'} #${inv.invnum}`);

  const taxSetAside = salesTax + boEstimate;

  // ---- Mover pay — ACTUAL, from logged hours this month, grouped into display piles ($35
  // on-site / $25 general = destage+warehouse+travel) while the raw type stays on the entry
  // itself, and by project (for Allocation cards). Shared/internal-job entries (no real client
  // job) are isolated so the one-off check below can pull them out of the baseline. ----
  let moverCost = 0; const moverByType = {}; const moverByProject = Object.create(null); let sharedLaborTotal = 0;
  const internalInvnums = ['WAREHOUSE-ORG'];
  periodEntries.forEach(e=>{
    const amt = planCents(e.amount)/100;
    moverCost += amt;
    moverByType[e.work_type] = (moverByType[e.work_type]||0) + amt;
    if(e.invnum && e.amount!=null) moverByProject[e.invnum] = (moverByProject[e.invnum]||0) + amt;
    if(internalInvnums.includes(e.invnum)) sharedLaborTotal += amt;
  });
  moverCost = planCents(moverCost)/100;
  const moverOnSite = moverByType['staging']||0;
  const moverGeneral = (moverByType['destage']||0) + (moverByType['warehouse']||0) + (moverByType['travel']||0);

  // A planning-only transfer changes funding, while operational estimates follow staging and receipts.
  const recordedJobIds = new Set(paymentBudget.events.filter(e=>e.date.slice(0,7)===period).map(e=>e.invnum));
  const fundedJobIds = new Set(received.map(e=>e.invnum));
  const activeJobIds = new Set(counted.filter(inv=>{
    const date = inv.data?.startdate;
    return recordedJobIds.has(inv.invnum) || (date && date>=periodStart && date<=periodEnd);
  }).map(inv=>inv.invnum));
  const jobsThisMonthList = counted.filter(inv=>activeJobIds.has(inv.invnum)||fundedJobIds.has(inv.invnum));
  const jobsThisMonth = activeJobIds.size || 1;

  // ---- One-off / special month (§9) — auto-flag, no confirmation gate. Shared/internal-job
  // labor above the normal per-job baseline means the month is move/organizing-inflated, not
  // run-rate. Manual override (above) can force it either way. ----
  const sharedBaseline = jobsThisMonth * FINANCE_CONFIG.shared_labor_normal_per_job;
  const autoFlagged = sharedLaborTotal > sharedBaseline;
  const forced = overrides.oneOff && overrides.oneOff.force;
  const oneOffFlagged = forced===true ? true : (forced===false ? false : autoFlagged);
  const oneOffAmount = oneOffFlagged ? sharedLaborTotal : 0;
  // One-off flagging changes what's excluded from ratio/ideal-check baselines below — it never
  // changes what's actually owed, so the operating-net chain always uses the full moverCost.
  const moverCostExOneOff = moverCost - oneOffAmount;

  const truckByJob = new Map();
  const shoppingByJob = new Map();
  let shopping = 0;
  for(const tx of transactions){
    const amount = planCents(tx.amount);
    if(tx.pending) continue;
    if(tx.category==='Truck & Gas' && tx.job_invnum){
      truckByJob.set(tx.job_invnum,(truckByJob.get(tx.job_invnum)||0)+amount);
    }
    if(tx.category==='Furniture/Inventory'){
      shopping += amount;
      if(tx.job_invnum) shoppingByJob.set(tx.job_invnum,(shoppingByJob.get(tx.job_invnum)||0)+amount);
    }
  }
  const truckCost = jobsThisMonthList.reduce((sum,job)=>sum+(truckByJob.get(job.invnum) ?? (activeJobIds.has(job.invnum)?planCents(FINANCE_CONFIG.truck_per_job):0)),0)/100;
  const warehouseCost = Number(FINANCE_CONFIG.warehouse_monthly);
  const moverReserve = jobsThisMonthList.filter(job=>activeJobIds.has(job.invnum) && moverByProject[job.invnum]==null).length * (FINANCE_CONFIG.mover_plan_staging + FINANCE_CONFIG.mover_plan_destage);
  const netCashAfterFees = planCents(revenue-stripeFee)/100;
  const totalCosts = planCents(moverCost + moverReserve + truckCost + warehouseCost + taxSetAside + stripeFee)/100;
  const operatingNet = planCents(revenue-totalCosts)/100;
  const marginPct = revenue>0.009 ? (operatingNet/revenue*100) : 0;
  // Keep target is 25% of revenue WHEN THE MONTH SUPPORTS IT — capped at actual Net so an
  // aspirational benchmark can never manufacture a deficit on its own. In a healthy month this
  // is the full 25% (unchanged from before). In a lean month, Jasmine's target keep shrinks to
  // whatever profit actually exists — she takes all of it, Inventory lands at $0, not negative.
  // Inventory only goes negative when Net itself is negative — a real operating loss, not a
  // missed benchmark (Gabe, Oct 30: "is that how a business run?" — no).
  const keepTargetUncapped = revenue * FINANCE_CONFIG.profit_target_pct/100;
  const keepTarget = Math.min(planCents(keepTargetUncapped)/100, Math.max(0, operatingNet));
  const keepTargetCapped = keepTarget < keepTargetUncapped - 0.009;
  const inventoryBudget = Math.max(0,planCents(operatingNet - keepTarget))/100;
  const titheAmount = planCents(keepTarget*FINANCE_CONFIG.tithe_pct/100)/100; // tithe comes out of what Jasmine personally keeps, not the business leftover
  const personallyRetained = planCents(keepTarget-titheAmount)/100;

  // Mover ideal check (§5) — actual vs 15% ideal, one-off entries excluded so a warehouse move
  // doesn't falsely read as every project running over.
  const moverIdealTarget = revenue * FINANCE_CONFIG.mover_ideal_pct/100;
  const moverOverIdeal = moverCostExOneOff - moverIdealTarget;

  return { revenue, cashReceipts, paymentBudget, salesTax, boEstimate, taxSetAside, moverCost, moverByType, moverOnSite, moverGeneral,
    moverByProject, truckCost, warehouseCost, netCashAfterFees, operatingNet, marginPct, keepTarget, keepTargetUncapped,
    keepTargetCapped, inventoryBudget, titheAmount, personallyRetained, stripeFee, jobsThisMonth, jobsThisMonthList,
    received, expected, committed, paidButUnsigned, noStagingDate, moverCostExOneOff, moverIdealTarget, moverOverIdeal,
    overrides, periodEntries, totalCosts, receiptCostsByJob, activeJobIds, truckByJob, shoppingByJob, shoppingCents:shopping, moverReserve,
    oneOff:{ flagged:oneOffFlagged, amount:oneOffAmount, baseline:sharedBaseline, raw:sharedLaborTotal, autoFlagged } };

}
window.SWJFinancePlan={calculate:calculateFinanceMonth, config:financeConfig, finInvoiceSplit, finPaymentEvents};
})();
