import { afterEach, expect, it, vi } from "vitest";
import { createApiClient } from "../../api/client";
import { scientificJson } from "../../api/scientific-json";
import { WorkspaceScope } from "../../controllers/workspaceScope";
import {
  retainAnalysisSnapshot,
  validateAnalysisReport,
} from "./study-analysis-api";

afterEach(() => vi.useRealTimers());
const source = {
  study_id: "study/1",
  revision: 2,
  through_seq: 3,
  protocol_digest: "a".repeat(64),
  sha256: "b".repeat(64),
};
function client(fetcher: typeof fetch) {
  const scope = new WorkspaceScope();
  scope.setIdentity("researcher:alpha");
  return createApiClient({
    scope,
    getProject: () => "alpha",
    onUnauthorized: vi.fn(),
    fetcher,
  });
}
const response = (value: unknown) =>
  new Response(scientificJson.stringify(value), {
    headers: { "content-type": "application/json" },
  });

it("polls the retained snapshot operation using the scoped HTTP client and the exact boundary", async () => {
  vi.useFakeTimers();
  const fetcher = vi.fn(async (path: RequestInfo | URL, init?: RequestInit) =>
    String(path).includes("/operations/")
      ? response({
          id: "operation/1",
          state: "succeeded",
          kind: "data_bridge",
          result: {
            artifact: {
              id: "snapshot-1",
              name: "study.jsonl",
              sha256: "b".repeat(64),
            },
            source,
          },
        })
      : response({ id: "operation/1", state: "queued", kind: "data_bridge" }),
  ) as typeof fetch;
  const state = vi.fn();
  const pending = retainAnalysisSnapshot(
    client(fetcher),
    "study/1",
    { revision: 2, through_seq: 3 },
    "request-1",
    new AbortController().signal,
    state,
  );
  await vi.waitFor(() =>
    expect(state).toHaveBeenCalledWith(
      expect.objectContaining({ state: "queued" }),
    ),
  );
  await vi.advanceTimersByTimeAsync(1200);
  expect(await pending).toMatchObject({
    artifact: { id: "snapshot-1" },
    source,
  });
  expect(fetcher).toHaveBeenNthCalledWith(
    1,
    "/api/p/alpha/lab/studies/study%2F1/analysis-inputs",
    expect.objectContaining({
      method: "POST",
      body: '{"revision":2,"through_seq":3,"idempotency_key":"request-1"}',
    }),
  );
  expect(fetcher).toHaveBeenNthCalledWith(
    2,
    "/api/p/alpha/lab/operations/operation%2F1",
    expect.objectContaining({ method: "GET" }),
  );
});

it("rejects an operation result from a different study boundary", async () => {
  const api = client(
    vi.fn(async () =>
      response({
        id: "operation-1",
        state: "succeeded",
        kind: "data_bridge",
        result: {
          artifact: {
            id: "snapshot-1",
            name: "study.jsonl",
            sha256: "b".repeat(64),
          },
          source: { ...source, revision: 1 },
        },
      }),
    ) as typeof fetch,
  );
  await expect(
    retainAnalysisSnapshot(
      api,
      "study/1",
      { revision: 2, through_seq: 3 },
      "request-1",
      new AbortController().signal,
      vi.fn(),
    ),
  ).rejects.toThrow(/invalid study boundary/);
});

function emptyReport(): any {
  return {
    format: "tinystudio.study-analysis.v1",
    source,
    request: { method: "means.welch", confidence: 0.8 },
    method: {
      id: "means.welch",
      label: "Welch means",
      version: "1.0.0",
      assumptions: [],
      references: [],
    },
    selection: {
      counts: {
        source_records: 3,
        selected_records: 0,
        used_records: 0,
        group_a: 0,
        group_b: 0,
      },
      diagnostics: [],
    },
    result: {
      estimate: null,
      counts: { group_a: 0, group_b: 0 },
      standard_error: null,
      degrees_of_freedom: null,
      interval: {
        confidence: 0.8,
        lower: null,
        upper: null,
        lower_unbounded: false,
        upper_unbounded: false,
      },
      diagnostics: [
        {
          code: "insufficient_observations",
          message:
            "No defined estimate or interval for the selected observations.",
        },
      ],
    },
    qualification: { kind: "unqualified" },
  };
}
it("accepts retained undefined inference with zero counts and rejects contradictory provenance and interval claims", () => {
  expect(() => validateAnalysisReport(emptyReport())).not.toThrow();
  const wrongCounts = emptyReport();
  wrongCounts.result.counts.group_a = 2;
  expect(() => validateAnalysisReport(wrongCounts)).toThrow(/counts/);
  const wrongBoundary = emptyReport();
  wrongBoundary.selection.counts.source_records = 4;
  expect(() => validateAnalysisReport(wrongBoundary)).toThrow(
    /selection counts/,
  );
  const wrongMethod = emptyReport();
  wrongMethod.request.method = "custom.python";
  expect(() => validateAnalysisReport(wrongMethod)).toThrow(
    /envelope|provenance/,
  );
  const wrongInterval = emptyReport();
  wrongInterval.result.interval.lower = 2;
  wrongInterval.result.interval.lower_unbounded = true;
  expect(() => validateAnalysisReport(wrongInterval)).toThrow(/interval/);
  const wrongConfidence = emptyReport();
  wrongConfidence.result.interval.confidence = 0.95;
  expect(() => validateAnalysisReport(wrongConfidence)).toThrow(/interval/);
  const nonfinite = emptyReport();
  nonfinite.result.estimate = Infinity;
  expect(() => validateAnalysisReport(nonfinite)).toThrow(/invalid/);
});

it("accepts finite custom JSON without inventing builtin estimates or a confidence interval", () => {
  const custom = emptyReport();
  custom.request.method = "custom.python";
  custom.method.id = "custom.python";
  custom.result = {
    custom_output: { parameters: { offset: -0 }, unavailable: null },
    counts: { selected_records: 0 },
  };
  expect(() => validateAnalysisReport(custom)).not.toThrow();
  custom.result.custom_output.parameters.offset = NaN;
  expect(() => validateAnalysisReport(custom)).toThrow(/invalid/);
});
