const VERSION = new URL(self.location.href).searchParams.get("v") || "v1";
const CACHE_NAME = `home13-pwa-${VERSION}`;
const APP_SHELL = [
  "/",
  "/static/site.webmanifest"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key !== CACHE_NAME)
          .map((key) => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") {
    return;
  }

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) {
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((networkResponse) => {
          const responseClone = networkResponse.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put("/", responseClone);
          });
          return networkResponse;
        })
        .catch(() => caches.match("/") || caches.match(request))
    );
    return;
  }

  if (
    url.pathname.endsWith("/favicon.ico")
    || url.pathname.endsWith("/site.webmanifest")
    || url.pathname.endsWith("/favicon-16x16.png")
    || url.pathname.endsWith("/favicon-32x32.png")
    || url.pathname.endsWith("/apple-touch-icon.png")
    || url.pathname.endsWith("/android-chrome-192x192.png")
    || url.pathname.endsWith("/android-chrome-512x512.png")
  ) {
    event.respondWith(
      fetch(request)
        .then((networkResponse) => {
          const responseClone = networkResponse.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(request, responseClone);
          });
          return networkResponse;
        })
        .catch(() => caches.match(request) || caches.match("/"))
    );
    return;
  }

  event.respondWith(
    caches.match(request).then((cachedResponse) => {
      if (cachedResponse) {
        return cachedResponse;
      }

      return fetch(request)
        .then((networkResponse) => {
          const responseClone = networkResponse.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(request, responseClone);
          });
          return networkResponse;
        })
        .catch(() => caches.match("/"));
    })
  );
});
