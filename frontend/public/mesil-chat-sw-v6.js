const CACHE_NAME = "mesil-chat-shell-v13";
const SHARED_FILE_CACHE = "mesil-chat-shared-files-v1";
const SHARED_PATH_PREFIX = "/__mesil_shared__/";
const MAX_SHARED_FILES = 10;
const MAX_SHARED_FILE_BYTES = 30 * 1024 * 1024;
const MAX_SHARED_TOTAL_BYTES = 100 * 1024 * 1024;
const SHARED_FILE_MAX_AGE_MS = 24 * 60 * 60 * 1000;
const ALLOWED_SHARED_FILE_TYPES = new Set([
  "image/jpeg",
  "image/png",
  "image/webp",
  "audio/mpeg",
  "audio/wav",
  "audio/x-wav",
  "audio/mp4",
  "audio/x-m4a",
  "audio/aac",
  "audio/webm",
  "audio/ogg",
  "video/mp4",
  "video/webm",
  "video/quicktime",
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.ms-excel",
  "application/vnd.hancom.hwp",
  "application/vnd.hancom.hwpx",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/msword",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  "application/vnd.ms-powerpoint",
  "text/plain",
  "text/csv",
]);
const SHARED_TYPE_BY_EXTENSION = new Map([
  [".jpg", "image/jpeg"],
  [".jpeg", "image/jpeg"],
  [".png", "image/png"],
  [".webp", "image/webp"],
  [".mp3", "audio/mpeg"],
  [".wav", "audio/wav"],
  [".m4a", "audio/mp4"],
  [".aac", "audio/aac"],
  [".ogg", "audio/ogg"],
  [".mp4", "video/mp4"],
  [".mov", "video/quicktime"],
  [".pdf", "application/pdf"],
  [".xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"],
  [".xls", "application/vnd.ms-excel"],
  [".hwp", "application/vnd.hancom.hwp"],
  [".hwpx", "application/vnd.hancom.hwpx"],
  [".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
  [".doc", "application/msword"],
  [".pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"],
  [".ppt", "application/vnd.ms-powerpoint"],
  [".txt", "text/plain"],
  [".csv", "text/csv"],
]);
const OFFLINE_FILES = [
  "/offline.html",
  "/icons/mesil-chat-192-v3.png",
  "/icons/mesil-chat-512-v3.png",
  "/sounds/mesil-medic-voice-v2.wav",
];

function sharedFileType(file) {
  const declaredType = String(file.type || "").toLowerCase();
  if (ALLOWED_SHARED_FILE_TYPES.has(declaredType)) return declaredType;
  const name = String(file.name || "").toLowerCase();
  const extension = name.includes(".") ? name.slice(name.lastIndexOf(".")) : "";
  return SHARED_TYPE_BY_EXTENSION.get(extension) || "";
}

async function deleteSharedToken(cache, token) {
  const tokenPrefix = new URL(
    `${SHARED_PATH_PREFIX}${token}/`,
    self.location.origin,
  ).href;
  const keys = await cache.keys();
  await Promise.all(
    keys
      .filter((request) => request.url.startsWith(tokenPrefix))
      .map((request) => cache.delete(request)),
  );
}

async function cleanupExpiredSharedFiles(cache) {
  const keys = await cache.keys();
  const metaRequests = keys.filter((request) =>
    new URL(request.url).pathname.startsWith(SHARED_PATH_PREFIX) &&
    new URL(request.url).pathname.endsWith("/meta"),
  );
  const now = Date.now();
  for (const request of metaRequests) {
    try {
      const response = await cache.match(request);
      const payload = response ? await response.json() : null;
      if (
        !payload ||
        Array.isArray(payload) ||
        typeof payload.created_at !== "number" ||
        now - payload.created_at <= SHARED_FILE_MAX_AGE_MS
      ) {
        continue;
      }
      const pathParts = new URL(request.url).pathname.split("/").filter(Boolean);
      const token = pathParts.at(-2);
      if (token) await deleteSharedToken(cache, token);
    } catch {
      // 손상된 메타는 추측해서 삭제하지 않습니다. 정상 소비 경로가 다시 정리합니다.
    }
  }
}

async function receiveSharedFiles(request) {
  let cache = null;
  let token = "";
  try {
    const formData = await request.formData();
    const files = formData.getAll("files").filter((value) => typeof value !== "string");
    if (files.length === 0) {
      return Response.redirect(new URL("/?share_error=no-file", self.location.origin), 303);
    }
    if (files.length > MAX_SHARED_FILES) {
      return Response.redirect(new URL("/?share_error=too-many", self.location.origin), 303);
    }
    if (files.some((file) => file.size <= 0 || file.size > MAX_SHARED_FILE_BYTES)) {
      return Response.redirect(new URL("/?share_error=too-large", self.location.origin), 303);
    }
    if (files.reduce((sum, file) => sum + file.size, 0) > MAX_SHARED_TOTAL_BYTES) {
      return Response.redirect(
        new URL("/?share_error=total-too-large", self.location.origin),
        303,
      );
    }
    const normalizedTypes = files.map(sharedFileType);
    if (normalizedTypes.some((type) => !type)) {
      return Response.redirect(new URL("/?share_error=unsupported", self.location.origin), 303);
    }

    token = self.crypto.randomUUID();
    cache = await caches.open(SHARED_FILE_CACHE);
    await cleanupExpiredSharedFiles(cache);
    const entries = [];
    for (const [index, file] of files.entries()) {
      const path = `${SHARED_PATH_PREFIX}${token}/${index}`;
      const normalizedType = normalizedTypes[index];
      entries.push({
        name: file.name || `공유파일-${index + 1}`,
        type: normalizedType,
        size: file.size,
        path,
      });
      await cache.put(
        new URL(path, self.location.origin).href,
        new Response(file, { headers: { "Content-Type": normalizedType } }),
      );
    }
    await cache.put(
      new URL(`${SHARED_PATH_PREFIX}${token}/meta`, self.location.origin).href,
      new Response(JSON.stringify({ created_at: Date.now(), entries }), {
        headers: { "Content-Type": "application/json; charset=utf-8" },
      }),
    );
    return Response.redirect(new URL(`/?shared=${token}`, self.location.origin), 303);
  } catch {
    if (cache && token) {
      try {
        await deleteSharedToken(cache, token);
      } catch {
        // 오류 안내를 막지 않도록 부분 캐시 정리 실패는 다음 정리 때 다시 시도합니다.
      }
    }
    return Response.redirect(new URL("/?share_error=receive-failed", self.location.origin), 303);
  }
}

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(OFFLINE_FILES)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    Promise.all([
      caches
        .keys()
        .then((keys) =>
          Promise.all(
            keys
              .filter((key) => key !== CACHE_NAME && key !== SHARED_FILE_CACHE)
              .map((key) => caches.delete(key)),
          ),
        ),
      self.clients.claim(),
      caches.open(SHARED_FILE_CACHE).then(cleanupExpiredSharedFiles),
    ]),
  );
});

self.addEventListener("message", (event) => {
  if (event.data?.type === "SKIP_WAITING") {
    self.skipWaiting();
  }
});

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch {
    payload = {};
  }
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });
      const isVoiceCall = payload.kind === "voice_call";
      const isVoiceCallCancel = payload.kind === "voice_call_cancel";
      if (isVoiceCall || isVoiceCallCancel) {
        windows.forEach((client) => client.postMessage(payload));
      }
      if (isVoiceCallCancel) {
        const notifications = await self.registration.getNotifications({
          tag: payload.tag,
        });
        notifications.forEach((notification) => notification.close());
        return;
      }
      const isTestNotification = payload.tag === "mesil-chat-test";
      if (
        !isTestNotification &&
        payload.kind !== "comment" &&
        windows.some((client) => client.visibilityState === "visible")
      ) {
        return;
      }
      await self.registration.showNotification(payload.title || "MESIL_Chat", {
        body: payload.body || "새 메시지가 도착했습니다.",
        icon: "/icons/mesil-chat-192-v3.png",
        badge: "/icons/mesil-chat-192-v3.png",
        tag: payload.tag || "mesil-chat-message",
        renotify: true,
        requireInteraction: isVoiceCall,
        vibrate: isVoiceCall
          ? [500, 180, 500, 180, 700]
          : [180, 80, 180],
        actions: isVoiceCall
          ? [{ action: "open-call", title: "통화 보기" }]
          : undefined,
        data: {
          url: payload.url || "/",
          kind: payload.kind || "message",
          callId: payload.call_id || null,
        },
      });
    })(),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = new URL(event.notification.data?.url || "/", self.location.origin).href;
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });
      for (const client of windows) {
        if ("navigate" in client) {
          await client.navigate(targetUrl);
        }
        return client.focus();
      }
      return self.clients.openWindow(targetUrl);
    })(),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (
    request.method === "POST" &&
    url.origin === self.location.origin &&
    url.pathname === "/share-target"
  ) {
    event.respondWith(receiveSharedFiles(request));
    return;
  }
  if (
    request.method !== "GET" ||
    url.origin !== self.location.origin ||
    url.pathname.startsWith("/api/") ||
    url.pathname.startsWith("/downloads/") ||
    url.pathname === "/app-version.json"
  ) {
    return;
  }
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request, { cache: "no-store" }).catch(() => caches.match("/offline.html")),
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
  event.respondWith(caches.match(request).then((cached) => cached || fetch(request)));
});
