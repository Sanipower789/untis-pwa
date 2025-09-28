const CACHE = "untis-cache-v23";
// cache a tiny core; app.js & styles load fast anyway
const CORE = ["/", "/api/timetable"];

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

self.addEventListener("fetch", (event)=>{
  const url = new URL(event.request.url);

  // network-first for API (fresh data), fall back to cache if offline
  if (url.pathname.startsWith("/api/")) {
    event.respondWith((async () => {
      try {
        const fresh = await fetch(event.request, { cache: 'no-store' });
        return fresh;
      } catch (e) {
        const cached = await caches.match(event.request);
        return cached || new Response(JSON.stringify({ ok:true, lessons:[] }), {
          status: 200, headers: { "content-type": "application/json" }
        });
      }
    })());
    return;
  }

  // cache-first for everything else (simple)
  event.respondWith(caches.match(event.request).then(r => r || fetch(event.request)));
});