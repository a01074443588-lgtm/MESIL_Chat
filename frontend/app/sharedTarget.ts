const SHARED_FILE_CACHE = "mesil-chat-shared-files-v1";
const SHARED_PATH_PREFIX = "/__mesil_shared__/";
const MAX_SHARED_FILES = 10;
const MAX_SHARED_FILE_BYTES = 30 * 1024 * 1024;
const MAX_SHARED_TOTAL_BYTES = 100 * 1024 * 1024;
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

type SharedFileEntry = {
  name: string;
  type: string;
  size: number;
  path: string;
};

type SharedFileMetadata = {
  created_at: number;
  entries: SharedFileEntry[];
};

function validToken(token: string) {
  return /^[a-zA-Z0-9-]{8,100}$/.test(token);
}

export async function consumeSharedTargetFiles(token: string): Promise<File[]> {
  if (!validToken(token) || !("caches" in window)) return [];
  const cache = await caches.open(SHARED_FILE_CACHE);
  const metaPath = `${SHARED_PATH_PREFIX}${token}/meta`;
  const metaResponse = await cache.match(metaPath);
  if (!metaResponse) return [];

  let entries: SharedFileEntry[] = [];
  try {
    const payload = (await metaResponse.json()) as unknown;
    const rawEntries = Array.isArray(payload)
      ? payload
      : payload &&
          typeof payload === "object" &&
          Array.isArray((payload as SharedFileMetadata).entries)
        ? (payload as SharedFileMetadata).entries
        : [];
    entries = rawEntries
      .filter(
        (entry): entry is SharedFileEntry =>
          Boolean(entry) &&
          typeof entry === "object" &&
          typeof (entry as SharedFileEntry).name === "string" &&
          typeof (entry as SharedFileEntry).type === "string" &&
          typeof (entry as SharedFileEntry).size === "number" &&
          typeof (entry as SharedFileEntry).path === "string",
      )
      .slice(0, MAX_SHARED_FILES);

    if (entries.reduce((sum, entry) => sum + entry.size, 0) > MAX_SHARED_TOTAL_BYTES) {
      return [];
    }

    const files: File[] = [];
    for (const entry of entries) {
      if (
        !entry.path.startsWith(`${SHARED_PATH_PREFIX}${token}/`) ||
        !ALLOWED_SHARED_FILE_TYPES.has(entry.type) ||
        entry.size <= 0 ||
        entry.size > MAX_SHARED_FILE_BYTES
      ) {
        continue;
      }
      const response = await cache.match(entry.path);
      if (!response) continue;
      const blob = await response.blob();
      if (blob.size !== entry.size) continue;
      files.push(new File([blob], entry.name, { type: entry.type }));
    }
    return files;
  } finally {
    await Promise.all(entries.map((entry) => cache.delete(entry.path)));
    await cache.delete(metaPath);
  }
}
