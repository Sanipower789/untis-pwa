const CACHE = "untis-cache-v22";
const CORE = ["/", "/api/timetable"]; // minimal

self.addEventListener("install", (e)=>{
  e.waitUntil(caches.open(CACHE).then(c=>c.addAll(CORE)));
  self.skipWaiting();
});
self.addEventListener("activate", (e)=>{
  e.waitUntil(
    caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k))))
  );
  self.clients.claim();
});
self.addEventListener("fetch", (e)=>{
  const url = new URL(e.request.url);
  // API immer fresh (Fallback Cache)
  if (url.pathname.startsWith("/api/")){
    e.respondWith(fetch(e.request).catch(()=>caches.match(e.request)));
    return;
  }
  // statische Dateien cache-first
  e.respondWith(caches.match(e.request).then(r=>r||fetch(e.request)));
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  if (url.pathname.startsWith('/api/')) {
    // network-first for API
    event.respondWith((async () => {
      try {
        const fresh = await fetch(event.request, { cache: 'no-store' });
        return fresh;
      } catch (e) {
        // fall back to cache only if you really want
        const cached = await caches.match(event.request);
        return cached || new Response('[]', { status: 200 });
      }
    })());
    return;
  }});