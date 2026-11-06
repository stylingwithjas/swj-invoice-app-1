(() => {
  'use strict';
  const url = '/api/webhook?cash=budget_months';
  const money = new Intl.NumberFormat('en-US', {style:'currency', currency:'USD'});
  const dateLabel = (value, options) => new Intl.DateTimeFormat('en-US', {
    ...options, timeZone:'UTC'
  }).format(new Date(value + 'T00:00:00Z'));
  const receivedLabel = date => dateLabel(date, {month:'short', day:'numeric', year:'numeric'});
  const monthLabel = month => dateLabel(month + '-01', {month:'long', year:'numeric'});

  async function request(suffix, options) {
    if (window.swjOwnerReady && !(await window.swjOwnerReady)) {
      throw new Error('Sign in to view or change payment budget months.');
    }
    const response = await fetch(url + suffix, {credentials:'same-origin', ...options});
    if (!response.ok) {
      if (response.status === 401) throw new Error('Sign in to view or change payment budget months.');
      if (response.status === 503) throw new Error('Payment budget months are unavailable. Check that the budget-month migration has been applied, then retry.');
      const problem = await response.json().catch(() => ({}));
      throw new Error(problem.error || 'Could not load or save the budget month.');
    }
    return response.json();
  }

  async function load(period) {
    return request(period ? '&period=' + encodeURIComponent(period) : '');
  }

  async function save(event, month) {
    return request('', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({event_id:event.event_id, invnum:event.invnum,
        received_date:event.date, amount_cents:event.amount_cents, budget_month:month}),
    });
  }

  async function renderHome() {
    const root = document.getElementById('home-budget-month');
    if (!root) return;
    try {
      const budget = await load();
      root.querySelector('[data-budget-amount]').textContent = money.format(budget.budgeted_cents / 100);
      const parts = [monthLabel(budget.period) + ' · before costs and reserves'];
      if (budget.moved_in_cents) parts.push(money.format(budget.moved_in_cents / 100) + ' moved in');
      if (budget.moved_out_cents) parts.push(money.format(budget.moved_out_cents / 100) + ' moved out');
      if (budget.issues.length) parts.push('Receipt records need review');
      root.querySelector('[data-budget-note]').textContent = parts.join(' · ');
      root.href = '/payroll.html?period=' + encodeURIComponent(budget.period) + '#fin-budget';
    } catch (error) {
      root.querySelector('[data-budget-note]').textContent = error.message;
    }
  }

  window.SWJPaymentBudget = {load, save, monthLabel, receivedLabel};
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', renderHome);
  else renderHome();
})();
