const CACHE_NAME = 'news-hub-v44';
const ASSETS = ['./manifest.json', 'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap', 'https://cdnjs.cloudflare.com/ajax/libs/crypto-js/4.2.0/crypto-js.min.js'];
self.addEventListener('install', e => { e.waitUntil(caches.open(CACHE_NAME).then(c => c.addAll(ASSETS))); self.skipWaiting(); });
self.addEventListener('activate', e => { e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))))); self.clients.claim(); });
self.addEventListener('fetch', e => {
  const url = e.request.url;
  const cleanUrl = url.split('?')[0];
  const cleanUrl = url.split('?')[0];
  if (url.includes('data.json') || url.includes('offline.json') || e.request.mode === 'navigate' || cleanUrl.endsWith('.html')) {
    e.respondWith(
      fetch(e.request)
        .then(res => { const cl = res.clone(); caches.open(CACHE_NAME).then(c => c.put(cleanUrl, cl)); return res; })
        .catch(() => caches.match(cleanUrl).then(m => m || caches.match(e.request, {ignoreSearch: true})))
    );
    return;
  }
  e.respondWith(caches.match(e.request, {ignoreSearch: true}).then(cached => cached || fetch(e.request)));
});
