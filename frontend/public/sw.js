/* Healing Frequencies — Service Worker
 * Strategy:
 *  - Navigation requests (HTML documents): network-first, cache fallback.
 *    Prevents users getting stuck on a stale index.html that references
 *    JS chunks the current deploy no longer has (the "long load / blank
 *    screen after redeploy" bug we saw on mobile).
 *  - Static assets (same-origin non-navigation GETs, non-API): cache-first
 *    with background refresh.
 *  - API calls (/api/*): always network (do not cache auth/state).
 *  - Pre-cache the app shell on install for instant offline open.
 */
const CACHE = 'hf-shell-v3';
const SHELL = [
  '/',
  '/index.html',
  '/manifest.json',
  '/icon-192.png',
  '/icon-512.png',
  '/icon-180.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Allow the app to trigger an immediate SW takeover after a version bump.
self.addEventListener('message', (event) => {
  if (event.data === 'SKIP_WAITING') self.skipWaiting();
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  // Never cache API — always hit the network so auth / state is fresh.
  if (url.pathname.startsWith('/api/')) return;
  // Only handle same-origin traffic; leave cross-origin resources to the
  // browser (CDN fonts, cross-domain backend, etc.).
  if (url.origin !== self.location.origin) return;

  // Navigation requests / HTML documents → network-first. Users must always
  // get the latest index.html so it references the current chunk hashes.
  const isNavigation =
    req.mode === 'navigate' ||
    (req.headers.get('accept') || '').includes('text/html');
  if (isNavigation) {
    event.respondWith(
      fetch(req)
        .then((resp) => {
          // Refresh the cached shell entry so offline still works.
          if (resp && resp.status === 200 && resp.type === 'basic') {
            const copy = resp.clone();
            caches.open(CACHE).then((c) => c.put('/index.html', copy)).catch(() => {});
          }
          return resp;
        })
        .catch(() => caches.match(req).then((m) => m || caches.match('/index.html')))
    );
    return;
  }

  // Static assets → cache-first with background refresh.
  event.respondWith(
    caches.match(req).then((cached) => {
      const network = fetch(req)
        .then((resp) => {
          if (resp && resp.status === 200 && resp.type === 'basic') {
            const copy = resp.clone();
            caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
          }
          return resp;
        })
        .catch(() => cached);
      return cached || network;
    })
  );
});
