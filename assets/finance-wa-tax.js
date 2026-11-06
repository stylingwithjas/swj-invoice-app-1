(async function () {
  'use strict';
  const periods = document.getElementById('fin-wa-tax-periods');
  if (window.swjOwnerReady && !(await window.swjOwnerReady)) return;

  try {
    const response = await fetch('/api/webhook?cash=tax_estimates', { credentials: 'same-origin' });
    if (!response.ok) throw new Error('Tax estimates unavailable');
    const { previous, current } = await response.json();
    const money = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });

    const rows = [[previous, 'Last month'], [current, 'This month so far']].map(([estimate, label]) => {
      const row = document.createElement('div');
      const link = document.createElement('a');
      link.href = '/tax-report.html?period=' + encodeURIComponent(estimate.period);
      link.textContent = label + ' · ' + estimate.label;
      const amount = document.createElement('div');
      amount.textContent = money.format(estimate.estimated_cents / 100);
      row.append(link, amount);
      if (estimate.filing_recorded) {
        const note = document.createElement('div');
        note.className = 'pr-hdr-sub';
        note.textContent = 'Filing recorded · check My DOR for the actual amount';
        row.append(note);
      }
      return row;
    });
    periods.replaceChildren(...rows);
  } catch {
    periods.textContent = 'Tax estimates unavailable. Reload to retry.';
  }
})();
