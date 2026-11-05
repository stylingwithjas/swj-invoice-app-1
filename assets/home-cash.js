(function () {
  'use strict';
  const money = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });
  const el = id => document.getElementById(id);

  function renderPlanning(snapshot) {
    const planning = el('home-planning');
    if (!planning) return;
    const tax = snapshot.wa_tax_estimate;
    el('home-tax-label').textContent = tax ? 'WA tax estimate · ' + tax.label : 'WA tax estimate';
    el('home-tax-value').textContent = tax ? money.format(tax.estimated_cents / 100) : 'Unavailable';
    el('home-tax-caption').textContent = tax?.filing_recorded
      ? 'Filing recorded · check the actual amount in My DOR'
      : 'Before credits and payments';
    el('home-tax-report').href = tax ? '/tax-report.html?period=' + encodeURIComponent(tax.period) : '/tax-report.html';
    planning.hidden = false;
  }

  async function load() {
    try {
      const r = await fetch('/api/webhook?cash=snapshot&home=1', { credentials: 'same-origin' });
      if (!r.ok) throw new Error('Unavailable');
      window.SWJCashSnapshot = await r.json();
      window.SWJCashUnavailable = false;
      renderPlanning(window.SWJCashSnapshot);
    } catch {
      window.SWJCashUnavailable = true;
    }
    if (typeof window.buildS6 === 'function') window.buildS6();
    if (typeof window.buildS11 === 'function') window.buildS11();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', load);
  else load();
})();
