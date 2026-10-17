const CACHE_NAME = 'news-hub-v20';
const ASSETS = ['./manifest.json', 'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap'];
self.addEventListener('install', e => { e.waitUntil(caches.open(CACHE_NAME).then(c => c.addAll(ASSETS))); self.skipWaiting(); });
self.addEventListener('activate', e => { e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))))); self.clients.claim(); });
self.addEventListener('fetch', e => {
  const url = e.request.url;
  if (url.includes('data.json') || e.request.mode === 'navigate' || url.endsWith('.html')) {
    e.respondWith(fetch(e.request).then(res => { const cl = res.clone(); caches.open(CACHE_NAME).then(c => c.put(e.request, cl)); return res; }).catch(() => caches.match(e.request)));
    return;
  }
  e.respondWith(caches.match(e.request).then(cached => cached || fetch(e.request)));
});
