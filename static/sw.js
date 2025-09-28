const CACHE = "untis-cache-v24"; // bump!
const CORE = ["/", "/api/timetable", "/lessons_mapped.json"];

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

  if (url.pathname.startsWith("/api/")) {
    event.respondWith((async () => {
      try {
        return await fetch(event.request, { cache: 'no-store' });
      } catch (e) {
        const cached = await caches.match(event.request);
        return cached || new Response(JSON.stringify({ ok:true, lessons:[] }), {
          status: 200, headers: { "content-type": "application/json" }
        });
      }
    })());
    return;
  }

  // cache-first for static (incl. lessons_mapped.json)
  event.respondWith(caches.match(event.request).then(r => r || fetch(event.request)));
});