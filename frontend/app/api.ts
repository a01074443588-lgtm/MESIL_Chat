export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export function apiBase(): string {
  const configured = process.env.NEXT_PUBLIC_API_URL?.trim().replace(/\/$/, "");
  if (configured) return configured;
  if (typeof window === "undefined") return "http://127.0.0.1:8000";
  if (["3000", "3001", "3100"].includes(window.location.port)) {
    return `${window.location.protocol}//${window.location.hostname}:8000`;
  }
  return window.location.origin;
}

export function websocketUrl(): string {
  return `${apiBase().replace(/^http/, "ws")}/api/ws`;
}

export async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const hasFormData =
    typeof FormData !== "undefined" && options.body instanceof FormData;
  const response = await fetch(`${apiBase()}${path}`, {
    ...options,
    credentials: "include",
    headers: {
      ...(options.body && !hasFormData ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
  });
  if (!response.ok) {
    let message = `요청을 처리하지 못했습니다. (${response.status})`;
    try {
      const payload = (await response.json()) as { detail?: string };
      if (payload.detail) message = payload.detail;
    } catch {
      // 텍스트가 아닌 오류 응답은 상태코드 안내를 사용합니다.
    }
    if (
      ![
        "/api/auth/login",
        "/api/auth/logout",
        "/api/auth/me",
        "/api/auth/reviewer-session",
      ].includes(path) &&
      response.status === 401
    ) {
      window.dispatchEvent(
        new CustomEvent("smcodi:auth-error", {
          detail: { status: response.status, message },
        }),
      );
    }
    throw new ApiError(message, response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function apiUpload<T>(
  path: string,
  body: FormData,
  onProgress?: (percent: number) => void,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", `${apiBase()}${path}`);
    request.withCredentials = true;

    request.upload.addEventListener("progress", (event) => {
      if (!event.lengthComputable) return;
      const percent = Math.min(100, Math.round((event.loaded / event.total) * 100));
      onProgress?.(percent);
    });

    request.addEventListener("load", () => {
      let payload: unknown;
      try {
        payload = request.responseText ? JSON.parse(request.responseText) : undefined;
      } catch {
        payload = undefined;
      }

      if (request.status < 200 || request.status >= 300) {
        const detail =
          payload &&
          typeof payload === "object" &&
          "detail" in payload &&
          typeof payload.detail === "string"
            ? payload.detail
            : `요청을 처리하지 못했습니다. (${request.status})`;

        if (
          ![
            "/api/auth/login",
            "/api/auth/logout",
            "/api/auth/me",
            "/api/auth/reviewer-session",
          ].includes(path) &&
          request.status === 401
        ) {
          window.dispatchEvent(
            new CustomEvent("smcodi:auth-error", {
              detail: { status: request.status, message: detail },
            }),
          );
        }
        reject(new ApiError(detail, request.status));
        return;
      }

      onProgress?.(100);
      resolve((request.status === 204 ? undefined : payload) as T);
    });

    request.addEventListener("error", () => {
      reject(
        new ApiError(
          "파일 전송 중 연결이 끊겼습니다. 네트워크를 확인한 뒤 다시 보내 주세요.",
          0,
        ),
      );
    });

    request.send(body);
  });
}
