const CACHE_NAME = 'news-hub-v3';
const ASSETS = [
  './manifest.json',
  'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap',
  'https://cdnjs.cloudflare.com/ajax/libs/crypto-js/4.2.0/crypto-js.min.js'
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE_NAME).then(c => c.addAll(ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  const url = e.request.url;

  // HTML-Navigationen und data.json: IMMER Network-First (frische Version vom Server)
  if (url.includes('data.json') || e.request.mode === 'navigate' || url.endsWith('.html')) {
    e.respondWith(
      fetch(e.request).then(res => {
        const cl = res.clone();
        caches.open(CACHE_NAME).then(c => c.put(e.request, cl));
        return res;
      }).catch(() => caches.match(e.request))
    );
    return;
  }

  // Statische Assets (Fonts, Libs): Cache-First mit Network-Fallback
  e.respondWith(caches.match(e.request).then(cached => cached || fetch(e.request)));
});
