// SWJ Admin Auth — single site-wide admin session (owner decision, Nov 3, 2026)
// Drop this <script> in the <head> of any admin page BEFORE other scripts/content load.
// Client-facing proposal pages must NOT include this file.
//
// Replaces the old auth.js: that gate checked a password baked directly into the JS file the
// site itself serves (readable from view-source by anyone with the URL — never actually
// secret) and stored only a browser flag. This file checks nothing itself; it asks the server
// (/api/webhook?admin=...), which holds the real password server-side only and issues a
// signed, HttpOnly session cookie the browser can't read or forge. Logging in here unlocks
// every admin page on this device — Section 7 and the Stripe reconciliation report included,
// no second prompt — because they all check the same session.
//
// To sign out (or "forget this device"), call window.swjSignOut() — this actually revokes the
// session server-side, not just a local flag.

(function() {
  document.documentElement.style.visibility = 'hidden';
  let resolveOwner;
  window.swjOwnerReady = new Promise(resolve => { resolveOwner = resolve; });

  const STYLE = `
    #swj-auth-gate {
      position: fixed; top: 0; left: 0; right: 0; bottom: 0;
      background: #fcfaf7;
      display: flex; align-items: center; justify-content: center;
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      z-index: 999999;
      padding: 24px;
    }
    .swj-auth-card {
      background: #fff;
      border: 0.5px solid #e7e1d9;
      border-radius: 16px;
      padding: 36px 28px 28px;
      max-width: 380px;
      width: 100%;
      box-shadow: 0 4px 24px rgba(0,0,0,0.04);
    }
    .swj-auth-brand { text-align: center; margin-bottom: 28px; }
    .swj-auth-logo {
      font-family: 'Cormorant Garamond', 'Times New Roman', serif;
      font-size: 38px; font-weight: 400; letter-spacing: 2px;
      color: #2c2926; line-height: 1; margin-bottom: 6px;
    }
    .swj-auth-tag { font-size: 9px; letter-spacing: 3px; color: #8c847c; text-transform: uppercase; }
    .swj-auth-title { font-family: 'Cormorant Garamond', serif; font-size: 22px; font-weight: 400; color: #2c2926; text-align: center; margin-bottom: 6px; }
    .swj-auth-sub { font-size: 12px; color: #8c847c; text-align: center; margin-bottom: 24px; line-height: 1.6; }
    .swj-auth-input {
      width: 100%; padding: 13px 14px; font-size: 15px;
      border: 0.5px solid #e7e1d9; border-radius: 10px; outline: none;
      background: #fcfaf7; color: #2c2926; margin-bottom: 12px;
      font-family: inherit; box-sizing: border-box;
    }
    .swj-auth-input:focus { border-color: #7a8574; }
    .swj-auth-input.err { border-color: #9a5a4a; background: #faf0ee; }
    .swj-auth-btn {
      width: 100%; padding: 14px; background: #2c2926; color: #f0ebe3;
      border: none; border-radius: 10px; font-family: 'Cormorant Garamond', serif;
      font-size: 16px; letter-spacing: 2px; text-transform: uppercase;
      cursor: pointer; transition: background 0.18s;
    }
    .swj-auth-btn:disabled { opacity: 0.6; cursor: wait; }
    .swj-auth-btn:hover:not(:disabled) { background: #3a3632; }
    .swj-auth-err { color: #9a5a4a; font-size: 12px; text-align: center; margin-top: 12px; min-height: 14px; }
    .swj-auth-foot { text-align: center; margin-top: 18px; font-size: 10px; color: #b8ada1; letter-spacing: 0.5px; line-height: 1.6; }
    .swj-auth-device-note { text-align: center; margin-top: 10px; font-size: 10.5px; color: #9a5a4a; line-height: 1.5; }
  `;

  function showContent() {
    document.documentElement.style.visibility = 'visible';
  }

  function showLoginGate(configured) {
    document.documentElement.style.visibility = 'visible';
    for (const child of document.body.children) {
      if (child.tagName !== 'SCRIPT') { child.inert = true; child.setAttribute('aria-hidden', 'true'); }
    }

    const gate = document.createElement('div');
    gate.id = 'swj-auth-gate';
    gate.innerHTML = `
      <style>${STYLE}</style>
      <div class="swj-auth-card">
        <div class="swj-auth-brand">
          <div class="swj-auth-logo">SWJ</div>
          <div class="swj-auth-tag">Styling With Jas</div>
        </div>
        <div class="swj-auth-title">${configured ? 'Sign in' : 'Sign-in not configured'}</div>
        <div class="swj-auth-sub">${configured ? 'Enter the admin password to continue' : 'Admin sign-in has not been configured on the server yet.'}</div>
        ${configured ? `
        <input type="password" class="swj-auth-input" id="swj-auth-pw" placeholder="Password" autocomplete="current-password" autofocus>
        <button class="swj-auth-btn" id="swj-auth-go">Continue</button>
        <div class="swj-auth-err" id="swj-auth-err"></div>
        <div class="swj-auth-device-note">Only use "remember this device" on your own device — never on a shared, borrowed, or public computer.</div>
        ` : ''}
        <div class="swj-auth-foot">Internal tools · Authorized access only</div>
      </div>
    `;
    document.body.appendChild(gate);
    if (!configured) return;

    const pwInput = document.getElementById('swj-auth-pw');
    const btn = document.getElementById('swj-auth-go');
    const errMsg = document.getElementById('swj-auth-err');

    function showError(text) {
      pwInput.classList.add('err');
      errMsg.textContent = text;
      pwInput.value = '';
      pwInput.focus();
    }

    async function tryAuth() {
      const entered = pwInput.value;
      if (!entered) return;
      btn.disabled = true;
      pwInput.classList.remove('err');
      errMsg.textContent = '';
      try {
        const res = await fetch('/api/webhook?admin=login', {
          method: 'POST', credentials: 'same-origin', cache: 'no-store',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ password: entered }),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.authenticated) {
          location.reload();
          return;
        }
        showError(res.status === 429 ? 'Too many attempts. Wait a minute and try again.' : (data.error || 'Incorrect password'));
      } catch (e) {
        showError('Could not reach the server. Check your connection and try again.');
      } finally {
        btn.disabled = false;
      }
    }

    btn.addEventListener('click', tryAuth);
    pwInput.addEventListener('keydown', e => { if (e.key === 'Enter') tryAuth(); });
    setTimeout(() => pwInput.focus(), 50);
  }

  async function checkSession() {
    try {
      const res = await fetch('/api/webhook?admin=session', { credentials: 'same-origin', cache: 'no-store' });
      const data = await res.json();
      if (data.authenticated) {
        resolveOwner(true);
        showContent();
      } else {
        resolveOwner(false);
        showLoginGate(!!data.configured);
      }
    } catch (e) {
      // No connection to verify against -- never silently show admin content without a
      // confirmed session, even if a stale local flag from the old auth.js is still around.
      resolveOwner(false);
      showLoginGate(true);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', checkSession);
  } else {
    checkSession();
  }
})();

// Sign out / "forget this device" -- actually revokes the session server-side (see
// lib/admin_auth.py's revoke_session()), not just a local browser flag.
window.swjSignOut = async function() {
  try {
    await fetch('/api/webhook?admin=logout', { method: 'POST', credentials: 'same-origin', cache: 'no-store', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  } catch (e) { /* the reload below still clears the client's view either way */ }
  location.reload();
};

// One-time cleanup: the old auth.js stored a browser flag under swj_auth_v*. It's never
// checked anymore, but clearing it means a shared/returned device can't show a stale "signed
// in" assumption anywhere else that might still read it.
try {
  Object.keys(localStorage).filter(k => k.startsWith('swj_auth_v')).forEach(k => localStorage.removeItem(k));
} catch (e) {}
