// Only project-owned resources are scoped. Authentication, tasks and Hub remain global.
const PROJECT_PREFIXES = ["/api/overview", "/api/datasets", "/api/datapoints", "/api/augment", "/api/snapshots", "/api/recommend", "/api/jobs", "/api/metrics", "/api/evals", "/api/traces", "/api/stats", "/api/models", "/api/playground", "/api/repositories", "/api/interp", "/api/autopilot", "/api/catalog", "/api/artifacts"];

export class AuthenticationRequiredError extends Error {
  constructor() { super("sign in to continue"); this.name = "AuthenticationRequiredError"; }
}

export function createApiClient({ getProject, getCapture, assertRender, recordApi, onUnauthorized, fetch = globalThis.fetch }) {
  async function request(path, options, capturePage) {
    const pageCapture = capturePage ? getCapture() : null;
    const response = await client.rawFetch(path, options);
    if (response.status === 401 && !path.startsWith("/api/auth/")) { onUnauthorized(); throw new AuthenticationRequiredError(); }
    if (!response.ok) {
      let detail = response.statusText;
      try { const j = await response.json(); detail = j.detail ?? detail; } catch (_) {}
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    const type = response.headers.get("content-type") || "";
    if (!type.includes("json")) return response.text();
    const data = await response.json();
    if (pageCapture) assertRender(pageCapture);
    if (capturePage && (!options.method || options.method === "GET")) recordApi(path, data, pageCapture);
    return data;
  }
  const client = {
    scoped(path) {
      const project = getProject();
      if (project && PROJECT_PREFIXES.some((pre) => path === pre || path.startsWith(pre + "/") || path.startsWith(pre + "?"))) return `/api/p/${encodeURIComponent(project)}` + path.slice(4);
      if ((path === "/api/tasks" || path === "/api/tasks?include_result=false") && project) return `${path}${path.includes("?") ? "&" : "?"}project=${encodeURIComponent(project)}`;
      return path;
    },
    rawFetch(path, options = {}) {
      const headers = { "X-Requested-With": "tinystudio", ...(options.body instanceof FormData ? {} : { "content-type": "application/json" }), ...(options.headers || {}) };
      return fetch(client.scoped(path), { credentials: "same-origin", ...options, headers });
    },
    api: (path, options = {}) => request(path, options, true),
    // Background controllers own scope and cancellation independently of page renders.
    background: (path, options = {}) => request(path, options, false),
    post: (path, body) => client.api(path, { method: "POST", body: body instanceof FormData ? body : JSON.stringify(body ?? {}) }),
    put: (path, body) => client.api(path, { method: "PUT", body: JSON.stringify(body ?? {}) }),
    patch: (path, body) => client.api(path, { method: "PATCH", body: JSON.stringify(body ?? {}) }),
    del: (path) => client.api(path, { method: "DELETE" }),
  };
  return client;
}
