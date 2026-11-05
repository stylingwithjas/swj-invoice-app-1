(function () {
  'use strict';
  const money = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });
  const el = id => document.getElementById(id);

  function renderPlanning(snapshot) {
    const planning = el('home-planning');
    if (!planning) return;
    const balance = snapshot.manual_checking_balance;
    const tax = snapshot.wa_tax_estimate;
    el('home-checking-value').textContent = balance ? money.format(balance.balance_cents / 100) : 'Not entered';
    el('home-checking-caption').textContent = balance
      ? 'Entered · as of ' + new Date(balance.as_of + 'T00:00:00').toLocaleDateString('en-US', { dateStyle: 'medium' })
      : 'Includes cash carried forward';
    el('home-checking-amount').value = balance ? (balance.balance_cents / 100).toFixed(2) : '';
    el('home-checking-date').value = balance?.as_of ||
      new Intl.DateTimeFormat('sv-SE', { timeZone: 'America/Los_Angeles' }).format(new Date());
    el('home-tax-label').textContent = tax ? 'WA tax estimate · ' + tax.label : 'WA tax estimate';
    el('home-tax-value').textContent = tax ? money.format(tax.estimated_cents / 100) : 'Unavailable';
    el('home-tax-caption').textContent = tax?.filing_recorded
      ? 'Filing recorded · check the actual amount in My DOR'
      : 'Before credits and payments';
    el('home-tax-report').href = tax ? '/tax-report.html?period=' + encodeURIComponent(tax.period) : '/tax-report.html';
    planning.hidden = false;
  }

  async function saveBalance(event) {
    event.preventDefault();
    const button = event.currentTarget.querySelector('button[type="submit"]');
    const status = el('home-checking-status');
    button.disabled = true;
    status.textContent = 'Saving…';
    try {
      const r = await fetch('/api/webhook?cash=manual_balance', {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ amount: el('home-checking-amount').value, as_of: el('home-checking-date').value }),
      });
      const data = await r.json();
      if (!r.ok || !data.manual_checking_balance) throw new Error(data.error || 'Could not save the balance.');
      window.SWJCashSnapshot.manual_checking_balance = data.manual_checking_balance;
      renderPlanning(window.SWJCashSnapshot);
      status.textContent = 'Balance saved.';
    } catch (error) {
      status.textContent = error.message || 'Could not save the balance.';
    } finally {
      button.disabled = false;
    }
  }

  async function load() {
    el('home-checking-form')?.addEventListener('submit', saveBalance);
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
