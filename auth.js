// SWJ Auth — password protection for admin pages
// Drop this <script> in the <head> of any admin page BEFORE other scripts/content load.
// Proposal page (client-facing) should NOT include this file.
//
// To change the password: update SWJ_PASSWORD and bump SWJ_AUTH_VERSION below.
// Bumping the version forces all existing sessions to re-authenticate.

(function() {
  const SWJ_PASSWORD = 'J@ebrielle27';
  const SWJ_AUTH_VERSION = '1';
  const STORAGE_KEY = 'swj_auth_v' + SWJ_AUTH_VERSION;

  // Already authenticated on this device?
  if (localStorage.getItem(STORAGE_KEY) === 'ok') {
    return;
  }

  // Hide page content immediately to prevent flash
  document.documentElement.style.visibility = 'hidden';

  function showLoginGate() {
    // Restore visibility but show only login screen
    document.documentElement.style.visibility = 'visible';

    // Clear any existing content
    const body = document.body;
    const originalContent = body.innerHTML;
    body.innerHTML = '';

    // Build login screen
    const gate = document.createElement('div');
    gate.id = 'swj-auth-gate';
    gate.innerHTML = `
      <style>
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
        .swj-auth-brand {
          text-align: center;
          margin-bottom: 28px;
        }
        .swj-auth-logo {
          font-family: 'Cormorant Garamond', 'Times New Roman', serif;
          font-size: 38px;
          font-weight: 400;
          letter-spacing: 2px;
          color: #2c2926;
          line-height: 1;
          margin-bottom: 6px;
        }
        .swj-auth-tag {
          font-size: 9px;
          letter-spacing: 3px;
          color: #8c847c;
          text-transform: uppercase;
        }
        .swj-auth-title {
          font-family: 'Cormorant Garamond', serif;
          font-size: 22px;
          font-weight: 400;
          color: #2c2926;
          text-align: center;
          margin-bottom: 6px;
        }
        .swj-auth-sub {
          font-size: 12px;
          color: #8c847c;
          text-align: center;
          margin-bottom: 24px;
          line-height: 1.6;
        }
        .swj-auth-input {
          width: 100%;
          padding: 13px 14px;
          font-size: 15px;
          border: 0.5px solid #e7e1d9;
          border-radius: 10px;
          outline: none;
          background: #fcfaf7;
          color: #2c2926;
          margin-bottom: 12px;
          font-family: inherit;
          box-sizing: border-box;
        }
        .swj-auth-input:focus {
          border-color: #7a8574;
        }
        .swj-auth-input.err {
          border-color: #9a5a4a;
          background: #faf0ee;
        }
        .swj-auth-btn {
          width: 100%;
          padding: 14px;
          background: #2c2926;
          color: #f0ebe3;
          border: none;
          border-radius: 10px;
          font-family: 'Cormorant Garamond', serif;
          font-size: 16px;
          letter-spacing: 2px;
          text-transform: uppercase;
          cursor: pointer;
          transition: background 0.18s;
        }
        .swj-auth-btn:hover { background: #3a3632; }
        .swj-auth-err {
          color: #9a5a4a;
          font-size: 12px;
          text-align: center;
          margin-top: 12px;
          min-height: 14px;
        }
        .swj-auth-foot {
          text-align: center;
          margin-top: 18px;
          font-size: 10px;
          color: #b8ada1;
          letter-spacing: 0.5px;
        }
      </style>
      <div class="swj-auth-card">
        <div class="swj-auth-brand">
          <div class="swj-auth-logo">SWJ</div>
          <div class="swj-auth-tag">Styling With Jas</div>
        </div>
        <div class="swj-auth-title">Sign in</div>
        <div class="swj-auth-sub">Enter password to access admin tools</div>
        <input type="password" class="swj-auth-input" id="swj-auth-pw" placeholder="Password" autocomplete="current-password" autofocus>
        <button class="swj-auth-btn" id="swj-auth-go">Continue</button>
        <div class="swj-auth-err" id="swj-auth-err"></div>
        <div class="swj-auth-foot">Internal tools · Authorized access only</div>
      </div>
    `;
    body.appendChild(gate);

    const pwInput = document.getElementById('swj-auth-pw');
    const btn = document.getElementById('swj-auth-go');
    const errMsg = document.getElementById('swj-auth-err');

    function tryAuth() {
      const entered = pwInput.value;
      if (entered === SWJ_PASSWORD) {
        localStorage.setItem(STORAGE_KEY, 'ok');
        // Reload to render the actual page content
        location.reload();
      } else {
        pwInput.classList.add('err');
        errMsg.textContent = 'Incorrect password';
        pwInput.value = '';
        pwInput.focus();
        setTimeout(() => {
          pwInput.classList.remove('err');
          errMsg.textContent = '';
        }, 2500);
      }
    }

    btn.addEventListener('click', tryAuth);
    pwInput.addEventListener('keydown', e => {
      if (e.key === 'Enter') tryAuth();
    });

    // Focus the input
    setTimeout(() => pwInput.focus(), 50);
  }

  // Wait for DOM to be ready, then show the gate
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', showLoginGate);
  } else {
    showLoginGate();
  }
})();

// Optional: expose a sign-out helper for adding a "Sign Out" button later
window.swjSignOut = function() {
  Object.keys(localStorage).filter(k => k.startsWith('swj_auth_v')).forEach(k => localStorage.removeItem(k));
  location.reload();
};
