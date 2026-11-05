(() => {
  'use strict';

  // Keep the scene through January 3 in Washington, even for visitors in another time zone.
  const expiresAt = Date.parse('2027-01-04T00:00:00-08:00');
  const sessionKey = 'swj-christmas-window-2026';
  const template = document.getElementById('christmas-window-template');
  if (!template || Date.now() >= expiresAt) return;

  const scene = template.content.firstElementChild.cloneNode(true);
  try {
    if (!sessionStorage.getItem(sessionKey)) {
      scene.classList.add('swj-christmas-play');
      sessionStorage.setItem(sessionKey, '1');
    }
  } catch {
    scene.classList.add('swj-christmas-play');
  }
  template.after(scene);

  let expiryTimer;
  function checkExpiry() {
    clearTimeout(expiryTimer);
    const remaining = expiresAt - Date.now();
    if (remaining <= 0) {
      scene.remove();
      window.removeEventListener('pageshow', checkExpiry);
      document.removeEventListener('visibilitychange', checkExpiry);
      return;
    }
    // Browser timers have a maximum delay, and background tabs can be suspended.
    expiryTimer = setTimeout(checkExpiry, Math.min(remaining, 24 * 60 * 60 * 1000));
  }
  window.addEventListener('pageshow', checkExpiry);
  document.addEventListener('visibilitychange', checkExpiry);
  checkExpiry();
})();
