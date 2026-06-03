/* SWJ service worker — KILL SWITCH.
   Caching caused stale code to stick on devices. This version unregisters
   itself, deletes all caches, and forces a one-time reload so every client
   drops to plain network (always-fresh) and never caches the app again. */
self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.map((k) => caches.delete(k)));
    try { await self.registration.unregister(); } catch (e) {}
    const clients = await self.clients.matchAll({ type: 'window' });
    clients.forEach((c) => { try { c.navigate(c.url); } catch (e) {} });
  })());
});

// Never intercept anything — let every request hit the network directly.
self.addEventListener('fetch', () => {});
