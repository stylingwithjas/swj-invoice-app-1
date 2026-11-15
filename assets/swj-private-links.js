/* Owner-only link creation. Client links require no password or account. */
(function () {
  'use strict';
  const ready = new Map(), pending = new Map();
  const keyFor = (invnum,role,kind) => JSON.stringify([invnum,role,kind]);
  window.swjPrivateLink = async function (invnum, role = 'client', kind = 'proposal') {
    const key = keyFor(invnum,role,kind);
    if (ready.has(key)) return ready.get(key);
    if (pending.has(key)) return pending.get(key);
    const attempt = async () => {
      const response = await fetch('/api/webhook?public=mint', {method:'POST', credentials:'same-origin', cache:'no-store',
        headers:{'Content-Type':'application/json'}, body:JSON.stringify({invnum,role,kind})});
      const result = await response.json();
      if (!response.ok || !result.url) throw new Error(result.error || 'Could not prepare a private link.');
      return result.url;
    };
    const work = (async () => {
      if (window.swjOwnerReady && !(await window.swjOwnerReady)) throw new Error('Sign in to prepare this link.');
      let url;
      try { url = await attempt(); }
      catch (e) {
        // iOS Safari/WKWebView surfaces this fetch failing as "Load failed" when it fires right
        // after a blob PDF download starts (e.g. the Titus button's generate-then-email flow) --
        // the download briefly contends with the page's own network stack. That's a TypeError,
        // never the application errors thrown above, so one short retry clears the transient
        // failure without ever masking a real "not signed in" / "could not prepare" error.
        if (!(e instanceof TypeError)) throw e;
        await new Promise(r => setTimeout(r, 600));
        url = await attempt();
      }
      ready.set(key,url);
      return url;
    })();
    pending.set(key,work);
    try { return await work; } finally { pending.delete(key); }
  };
  window.swjPrepareCrewLinks = function (invnum) {
    for (const role of ['mover1','mover2','dmover','photographer']) {
      window.swjPrivateLink(invnum,role,'confirm').catch(() => {});
    }
  };
  // SMS must open synchronously on iOS. Prefetch on editor open, never await in
  // the click handler and never fall back to an unprotected invoice-number URL.
  window.swjCrewLink = function (invnum,role) {
    const cached = ready.get(keyFor(invnum,role,'confirm'));
    if (cached) return cached;
    window.swjPrivateLink(invnum,role,'confirm').then(() => {
      alert('The private confirmation link is ready. Tap Text again.');
    }).catch(() => { alert('Could not prepare the private confirmation link. Check your connection and try again.'); });
    return null;
  };
})();
