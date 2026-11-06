(async function () {
  'use strict';
  const periods = document.getElementById('fin-wa-tax-periods');
  if (window.swjOwnerReady && !(await window.swjOwnerReady)) return;

  try {
    const response = await fetch('/api/webhook?cash=tax_estimates', { credentials: 'same-origin' });
    if (!response.ok) throw new Error('Tax estimates unavailable');
    const { previous, current } = await response.json();
    const money = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });

    const rows = [[previous, 'Previous Month'], [current, 'Current Month']].map(([estimate, label]) => {
      const link = document.createElement('a');
      link.className = 'fin-tax-kpi';
      link.href = '/tax-report.html?period=' + encodeURIComponent(estimate.period);
      for (const [className, text] of [
        ['fin-tax-label', label],
        ['fin-tax-month', estimate.label],
        ['fin-tax-amount', money.format(estimate.estimated_cents / 100)]
      ]) {
        const line = document.createElement('span');
        line.className = className;
        line.textContent = text;
        link.append(line);
      }
      return link;
    });
    periods.replaceChildren(...rows);
  } catch {
    periods.textContent = 'Tax estimates unavailable. Reload to retry.';
  }
})();
