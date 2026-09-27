/* Coach PWA service worker — minimal by design.
   Shell (HTML/icon/manifest) is network-first with a cached fallback so the
   app still opens offline; every API call goes to the network (live data must
   be live). Only clean 200s are cached: behind the password gate, "/" can
   redirect to /login, and a cached redirect would trap the app on that page. */
const CACHE = "coach-v2";
const SHELL = ["/", "/icon.svg", "/manifest.json", "/apple-touch-icon.png"];
const PRECACHE = ["/icon.svg", "/manifest.json"];   // public; "/" is cached on first real load

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(PRECACHE)));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || !SHELL.includes(url.pathname)) return; // APIs → network
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        if (res.ok && !res.redirected && res.type === "basic") {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return res;
      })
      .catch(() => caches.match(e.request)) // offline → cached shell
  );
});
