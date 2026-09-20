const CACHE_NAME = 'news-hub-v2';
const ASSETS = ['./', './index.html', './archive.html', './manifest.json', 'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap', 'https://cdnjs.cloudflare.com/ajax/libs/crypto-js/4.2.0/crypto-js.min.js'];
self.addEventListener('install', e => { e.waitUntil(caches.open(CACHE_NAME).then(c => c.addAll(ASSETS))); self.skipWaiting(); });
self.addEventListener('activate', e => { e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))))); self.clients.claim(); });
self.addEventListener('fetch', e => {
  if(e.request.url.includes('data.json')){
    e.respondWith(fetch(e.request).then(res => { const cl = res.clone(); caches.open(CACHE_NAME).then(c => c.put(e.request, cl)); return res; }).catch(() => caches.match(e.request)));
    return;
  }
  e.respondWith(caches.match(e.request).then(cached => cached || fetch(e.request)));
});
