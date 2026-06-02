/* SWJ service worker — bump CACHE_VERSION on any release to retire old caches. */
const CACHE_VERSION = 'swj-v1';
const PRECACHE = [
  '/', '/index.html', '/today.html', '/board.html', '/shopping.html', '/proposal.html',
  '/auth.js', '/manifest.webmanifest',
  '/assets/icon-192.png', '/assets/icon-512.png', '/assets/icon-maskable-512.png', '/assets/apple-touch-icon.png',
  '/assets/swj_logo.png', '/assets/swj_logo_white.png'
];

// Install: warm the cache (best-effort — a missing file must not abort install).
self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE_VERSION);
    await Promise.all(PRECACHE.map((url) => cache.add(url).catch(() => {})));
    self.skipWaiting();
  })());
});

// Activate: drop caches from previous versions and take control immediately.
self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k !== CACHE_VERSION).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

const isStaticAsset = (path) => /\.(?:ttf|otf|woff2?|png|jpe?g|gif|svg|webp|ico)$/i.test(path);

self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // Only ever handle same-origin GETs. Let the API, Supabase, Stripe,
  // fonts CDNs and every POST pass straight through to the network.
  if (req.method !== 'GET' || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/')) return;

  // Fonts / images: cache-first (they're effectively immutable per filename).
  if (isStaticAsset(url.pathname)) {
    event.respondWith((async () => {
      const cached = await caches.match(req);
      if (cached) return cached;
      try {
        const res = await fetch(req);
        if (res && res.ok) (await caches.open(CACHE_VERSION)).put(req, res.clone());
        return res;
      } catch (e) {
        return cached || Response.error();
      }
    })());
    return;
  }

  // Pages and any other same-origin GET (incl. auth.js): network-first so code
  // stays fresh; fall back to cache (then to the app shell) only when offline.
  event.respondWith((async () => {
    try {
      const res = await fetch(req);
      if (res && res.ok) (await caches.open(CACHE_VERSION)).put(req, res.clone());
      return res;
    } catch (e) {
      const cached = await caches.match(req);
      if (cached) return cached;
      if (req.mode === 'navigate') {
        return (await caches.match('/')) || (await caches.match('/today.html')) || Response.error();
      }
      return Response.error();
    }
  })());
});
