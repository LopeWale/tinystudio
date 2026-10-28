import { previewQuality } from "./workspace-preview-quality";
import type { ApiClient } from "../api/client";
import {
  isWorkspacePreviewReadPath,
  previewDatasets,
  previewEvaluations,
  previewJobs,
  previewReads,
  previewRecords,
} from "./workspace-preview-fixtures";
export const previewRefusal =
  "Illustrative preview: server requests, mutations and model execution are disabled.";
/** Explicit transport injection for direct-stream controllers. No network fallback. */
export const denyPreviewNetwork: typeof fetch = async () => {
  throw new Error(previewRefusal);
};
function refuse(): never {
  throw new Error(previewRefusal);
}
/** A closed read allowlist. Unknown GETs also fail; no fetch/XHR transport exists. */
export function createWorkspacePreviewApi(): ApiClient {
  return {
    async get<T>(path: string, signal?: AbortSignal): Promise<T> {
      if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
      if (
        !path.startsWith("/api/") ||
        path.includes("#") ||
        path.includes("\\")
      )
        refuse();
      const [pathname, search = ""] = path.split("?");
      const params = new URLSearchParams(search);
      let value: unknown;
      if (isWorkspacePreviewReadPath(pathname)) {
        value = previewReads[pathname];
        if (pathname === "/api/traces") {
          const offset = Math.max(0, Number(params.get("offset")) || 0);
          const limit = Math.max(1, Number(params.get("limit")) || 200);
          value = (value as unknown[]).slice(offset, offset + limit);
        }
      } else if (
        /^\/api\/p\/illustrative-(?:support|research)\/assistant\/conversation$/.test(
          pathname,
        )
      ) {
        value = {
          revision: 1,
          history: [],
          provider: "openai",
          model: "sample-helper",
        };
      } else {
        const dataset = previewDatasets.find(
          (item) =>
            pathname === `/api/datasets/${encodeURIComponent(item.name)}`,
        );
        const job = previewJobs.find(
          (item) =>
            pathname === `/api/jobs/${encodeURIComponent(item.job.name)}`,
        );
        const evaluation = previewEvaluations.find(
          (item) =>
            pathname === `/api/evals/${encodeURIComponent(item.summary.name)}`,
        );
        const qualityDataset = previewDatasets.find(
          (item) =>
            pathname ===
            `/api/datasets/${encodeURIComponent(item.name)}/quality`,
        );
        if (qualityDataset) value = previewQuality;
        else if (dataset) {
          const bucket = params.get("bucket");
          const rows = previewRecords.filter(
            (record) => !bucket || record.bucket === bucket,
          );
          const offset = Math.max(0, Number(params.get("offset")) || 0);
          const limit = Math.max(1, Number(params.get("limit")) || 15);
          value = {
            meta: { buckets: { training: 2, validation: 1, uncategorized: 0 } },
            total: previewRecords.length,
            rows: rows.slice(offset, offset + limit),
          };
        } else if (job) value = job;
        else if (evaluation) value = evaluation;
        else
          throw new Error(
            `Illustrative preview: no local read fixture for ${pathname}. No server request was sent.`,
          );
      }
      return structuredClone(value) as T;
    },
    download: async () => refuse(),
    post: async () => refuse(),
    put: async () => refuse(),
    patch: async () => refuse(),
    del: async () => refuse(),
    upload: async () => refuse(),
  };
}
