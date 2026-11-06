(function () {
  'use strict';

  async function load() {
    if (window.swjOwnerReady && !(await window.swjOwnerReady)) return;
    try {
      const r = await fetch('/api/webhook?cash=snapshot', { credentials: 'same-origin' });
      if (!r.ok) throw new Error('Unavailable');
      window.SWJCashSnapshot = await r.json();
      window.SWJCashUnavailable = false;
    } catch {
      window.SWJCashUnavailable = true;
    }
    if (typeof window.buildS6 === 'function') window.buildS6();
    if (typeof window.buildS11 === 'function') window.buildS11();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', load);
  else load();
})();
