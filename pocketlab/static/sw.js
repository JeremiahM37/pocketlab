// pocketlab service worker — installable PWA + offline fallback.
const CACHE_NAME = 'pocketlab-v2';
const STATIC_ASSETS = ['/', '/manifest.json', '/static/tailwind-3.4.17.js'];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET') return;

  // API + the embedded terminal pages are dynamic: network-first, fall back to
  // cache only when offline. (Caching these "forever" would pin stale data.)
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/term-app/')) {
    event.respondWith(
      fetch(event.request)
        .then(resp => { const c = resp.clone(); caches.open(CACHE_NAME).then(cache => cache.put(event.request, c)); return resp; })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  // Everything else (shell, manifest): cache-first.
  event.respondWith(
    caches.match(event.request).then(cached => {
      if (cached) return cached;
      return fetch(event.request).then(resp => {
        const c = resp.clone(); caches.open(CACHE_NAME).then(cache => cache.put(event.request, c)); return resp;
      });
    }).catch(() => {
      if (event.request.mode === 'navigate') {
        return new Response(
          `<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
          <title>pocketlab — Offline</title>
          <style>body{background:#0f172a;color:#e2e8f0;font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;text-align:center}h1{font-size:1.5rem;margin-bottom:.5rem}p{color:#94a3b8}</style></head>
          <body><div><h1>pocketlab</h1><p>You're offline. Reconnect to your network to continue.</p></div></body></html>`,
          { headers: { 'Content-Type': 'text/html' } }
        );
      }
    })
  );
});
