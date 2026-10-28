import { WorkspaceScope } from "../controllers/workspaceScope";
export interface ApiClient {
  download(path: string, signal?: AbortSignal): Promise<Blob>;
  get<T = any>(path: string, signal?: AbortSignal): Promise<T>;
  post<T = any>(path: string, body?: unknown, signal?: AbortSignal): Promise<T>;
  put<T = any>(path: string, body?: unknown, signal?: AbortSignal): Promise<T>;
  patch<T = any>(
    path: string,
    body?: unknown,
    signal?: AbortSignal,
  ): Promise<T>;
  del<T = any>(path: string, signal?: AbortSignal): Promise<T>;
  upload<T = any>(
    path: string,
    body: FormData | File,
    options?: {
      method?: string;
      signal?: AbortSignal;
      onProgress?: (value: { loaded: number; total?: number }) => void;
      headers?: Record<string, string>;
      external?: boolean;
    },
  ): Promise<T>;
}
const PREFIXES = [
  "overview",
  "datasets",
  "datapoints",
  "augment",
  "snapshots",
  "recommend",
  "jobs",
  "metrics",
  "evals",
  "traces",
  "stats",
  "models",
  "playground",
  "repositories",
  "interp",
  "autopilot",
  "catalog",
  "artifacts",
  "lab",
];
export function scopedPath(path: string, project: string) {
  if (
    project &&
    PREFIXES.some(
      (p) =>
        path === `/api/${p}` ||
        path.startsWith(`/api/${p}/`) ||
        path.startsWith(`/api/${p}?`),
    )
  )
    return `/api/p/${encodeURIComponent(project)}` + path.slice(4);
  if (
    project &&
    /^\/api\/tasks(?:\?|$)/.test(path) &&
    !/[?&]project=/.test(path)
  )
    return (
      path +
      (path.includes("?") ? "&" : "?") +
      "project=" +
      encodeURIComponent(project)
    );
  return path;
}
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public detail: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
export function createApiClient({
  scope,
  getProject,
  onUnauthorized,
  getUser,
  fetcher = globalThis.fetch,
}: {
  scope: WorkspaceScope;
  getProject: () => string;
  onUnauthorized: () => void;
  getUser?: () => unknown;
  fetcher?: typeof fetch;
}): ApiClient {
  async function request<T>(
    path: string,
    method: string,
    body?: unknown,
    signal?: AbortSignal,
    binary = false,
  ): Promise<T> {
    const lease = scope.capture(signal);
    try {
      const response = await fetcher(scopedPath(path, getProject()), {
        method,
        signal: lease.signal,
        credentials: "same-origin",
        headers: {
          "X-Requested-With": "tinystudio",
          ...(getUser?.() != null
            ? { "X-Conversation-Owner": String(getUser()) }
            : {}),
          ...(body instanceof FormData
            ? {}
            : { "content-type": "application/json" }),
        },
        body:
          body === undefined
            ? undefined
            : body instanceof FormData
              ? body
              : JSON.stringify(body),
      });
      lease.assert();
      const content =
        binary && response.ok
          ? await response.blob()
          : response.status === 204
            ? null
            : response.headers.get("content-type")?.includes("json")
              ? await response.json()
              : await response.text();
      lease.assert();
      if (!response.ok) {
        if (response.status === 401 && !path.startsWith("/api/auth/"))
          onUnauthorized();
        const detail = content?.detail ?? content ?? response.statusText;
        throw new ApiError(
          typeof detail === "string" ? detail : JSON.stringify(detail),
          response.status,
          detail,
        );
      }
      return content as T;
    } finally {
      lease.release();
    }
  }
  return {
    download: (p, s) => request<Blob>(p, "GET", undefined, s, true),
    get: (p, s) => request(p, "GET", undefined, s),
    post: (p, b, s) => request(p, "POST", b ?? {}, s),
    put: (p, b, s) => request(p, "PUT", b ?? {}, s),
    patch: (p, b, s) => request(p, "PATCH", b ?? {}, s),
    del: (p, s) => request(p, "DELETE", undefined, s),
    upload: <T>(
      path: string,
      body: FormData | File,
      options: Parameters<ApiClient["upload"]>[2] = {},
    ) =>
      new Promise<T>((resolve, reject) => {
        const lease = scope.capture(options.signal),
          xhr = new XMLHttpRequest();
        const cleanup = () => {
          lease.signal.removeEventListener("abort", abort);
          lease.release();
        };
        const abort = () => xhr.abort();
        lease.signal.addEventListener("abort", abort, { once: true });
        xhr.open(
          options.method ?? "POST",
          options.external ? path : scopedPath(path, getProject()),
        );
        xhr.withCredentials = false;
        if (!options.external) {
          xhr.setRequestHeader("X-Requested-With", "tinystudio");
          if (getUser?.() != null)
            xhr.setRequestHeader("X-Conversation-Owner", String(getUser()));
        }
        for (const [name, value] of Object.entries(options.headers ?? {}))
          xhr.setRequestHeader(name, value);
        xhr.upload.onprogress = (e) =>
          options.onProgress?.({
            loaded: e.loaded,
            total: e.lengthComputable ? e.total : undefined,
          });
        xhr.onabort = () => {
          cleanup();
          reject(new DOMException("Upload cancelled", "AbortError"));
        };
        xhr.onerror = () => {
          cleanup();
          reject(new Error("Upload connection failed"));
        };
        xhr.onload = () => {
          try {
            lease.assert();
            let data: any = xhr.responseText;
            try {
              data = JSON.parse(data);
            } catch {}
            if (xhr.status < 200 || xhr.status >= 300) {
              if (xhr.status === 401 && !options.external) onUnauthorized();
              throw new ApiError(
                data?.detail ?? `Upload failed (${xhr.status})`,
                xhr.status,
                data,
              );
            }
            resolve(data as T);
          } catch (e) {
            reject(e);
          } finally {
            cleanup();
          }
        };
        if (lease.signal.aborted) {
          cleanup();
          reject(new DOMException("Upload cancelled", "AbortError"));
          return;
        }
        xhr.send(body);
      }),
  };
}
