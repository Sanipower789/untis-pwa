// static/sw.js
// one stable name, no version bumps ever again
const CACHE = "untis-cache";
const CORE = ["/"]; // just cache the shell (index.html)

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(CORE)));
  self.skipWaiting(); // take over asap
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// let the page tell us: “skip waiting now”
self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SKIP_WAITING") {
    self.skipWaiting();
  }
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // --- API: always fresh, fallback if offline
  if (url.pathname.startsWith("/api/")) {
    event.respondWith((async () => {
      try {
        return await fetch(event.request, { cache: "no-store" });
      } catch {
        const cached = await caches.match(event.request);
        return (
          cached ||
          new Response(JSON.stringify({ ok: true, lessons: [] }), {
            status: 200,
            headers: { "content-type": "application/json" },
          })
        );
      }
    })());
    return;
  }

  // --- JS & CSS: network-first so updates roll out automatically
  if (url.pathname.endsWith("/app.js") || url.pathname.endsWith("/styles.css")) {
    event.respondWith((async () => {
      try {
        const fresh = await fetch(event.request, { cache: "no-store" });
        const cache = await caches.open(CACHE);
        cache.put(event.request, fresh.clone());
        return fresh;
      } catch {
        return (await caches.match(event.request)) || fetch(event.request);
      }
    })());
    return;
  }

  // --- everything else: cache-first (icons, fonts…)
  event.respondWith(caches.match(event.request).then(r => r || fetch(event.request)));
});