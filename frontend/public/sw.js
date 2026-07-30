const CACHE_NAME = "mesil-chat-shell-v10";
const OFFLINE_FILES = [
  "/offline.html",
  "/mesil-chat.webmanifest",
  "/icons/mesil-chat-192-v3.png",
  "/icons/mesil-chat-512-v3.png",
  "/sounds/mesil-medic-voice-v2.wav"
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(OFFLINE_FILES)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))),
      ),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (
    request.method !== "GET" ||
    url.origin !== self.location.origin ||
    url.pathname.startsWith("/api/")
  ) {
    return;
  }
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(() => caches.match("/offline.html")),
    );
    return;
  }
  if (
    url.pathname.startsWith("/assets/") ||
    request.destination === "script" ||
    request.destination === "style" ||
    request.destination === "worker"
  ) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
          return response;
        })
        .catch(() => caches.match(request)),
    );
    return;
  }
  event.respondWith(
    caches.match(request).then((cached) => cached || fetch(request)),
  );
});
