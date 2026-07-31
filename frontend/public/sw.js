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
const CACHE = 'hf-shell-v5';
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
    Promise.all([
      caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {}),
      // skipWaiting inside waitUntil so the browser awaits activation
      // rather than treating it as fire-and-forget. Future SW versions
      // now auto-activate on existing clients without a manual cache
      // clear on the user's device.
      self.skipWaiting(),
    ])
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    Promise.all([
      caches.keys().then((keys) =>
        Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
      ),
      // Take control of any already-open tabs immediately so the new
      // worker starts serving them right away, no reload required.
      self.clients.claim(),
    ])
  );
});

// Allow the app to trigger an immediate SW takeover after a version bump.
self.addEventListener('message', (event) => {
  if (event.data === 'SKIP_WAITING') self.skipWaiting();
});

// -- Web Push (Phase 10) ---------------------------------------------------
// Payloads are JSON: {title, body, destination, id, category}. We show a
// simple system notification and route notificationclick to the right URL.
self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = {}; }
  const title = data.title || 'Solarisound';
  const body = data.body || '';
  const destination = data.destination || '/';
  event.waitUntil(
    self.registration.showNotification(title, {
      body,
      icon: '/icon-192.png',
      badge: '/icon-192.png',
      tag: data.id || undefined,
      data: { destination, id: data.id, category: data.category },
      // Never fire loud OS-level alerts; this is a supportive channel.
      silent: false,
      requireInteraction: false,
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const dest = (event.notification.data && event.notification.data.destination) || '/';
  event.waitUntil((async () => {
    const all = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const client of all) {
      if ('focus' in client) {
        try {
          client.postMessage({ type: 'notification-click', destination: dest, id: event.notification.data?.id });
          return client.focus();
        } catch (e) { /* fall through */ }
      }
    }
    if (self.clients.openWindow) return self.clients.openWindow(dest);
    return null;
  })());
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
