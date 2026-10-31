import { Blob } from "node:buffer";
import { createHash, webcrypto } from "node:crypto";
import { cleanup, render, screen, act } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { StudioContext, type StudioRuntime } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { RetainedBaselineComparison } from "./baseline-comparison";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function report() {
  const task = { operation: "add", left: 2, right: 3 };
  const trace = {
    task,
    policy_version: "a".repeat(64),
    partition: "heldout",
    tool: {
      name: "add",
      arguments: { left: 2, right: 3 },
      result: 5,
      error: null,
    },
    evaluation: { expected: 5, actual: 5, correct: true, tool_error: false },
  };
  return {
    format: "tinystudio.tool-evaluation.v1",
    policy_kind: "autoregressive",
    checkpoint_sha256: "a".repeat(64),
    baseline: "Incoming policy before this attempt's updates (greedy)",
    records: [
      { case_id: "heldout:0", task, expected: 5, initial: trace, final: trace },
    ],
    metrics: {
      cases: 1,
      initial: { evaluated: 1, correct: 1, errors: 0, accuracy: 1 },
      final: { evaluated: 1, correct: 1, errors: 0, accuracy: 1 },
    },
    qualification: {
      status: "completed",
      comparison_complete: true,
      heldout_tasks_used_for_updates: false,
      weights_unchanged: true,
      scope: "Heldout tools",
    },
  };
}
const hash = (r: unknown) =>
  createHash("sha256").update(JSON.stringify(r)).digest("hex");
function view(
  payload: unknown,
  digest: string = hash(payload),
  scope = "p",
  download = vi.fn().mockResolvedValue(new Blob([JSON.stringify(payload)])),
  independent = false,
) {
  return {
    download,
    ui: (
      <StudioContext.Provider
        value={
          {
            api: { download },
            evidence: new EvidenceRegistry(),
            route: "/lab",
            project: scope,
            scopeKey: scope,
          } as unknown as StudioRuntime
        }
      >
        <RetainedBaselineComparison
          artifacts={[
            ...(independent
              ? [
                  {
                    id: "preferred",
                    name: "independent.json",
                    role: "independent_evaluation",
                    sha256: digest,
                  },
                ]
              : []),
            {
              id: "tool/report",
              name: "tool.json",
              role: "heldout_tool_evaluation",
              sha256: digest,
            },
          ]}
          comparison={{ format: "tinystudio.tool-evaluation.v1" }}
          records={[{ loss: 987, step: 1 }]}
          runId="run"
          scopeKey={scope}
          runState="succeeded"
        />
      </StudioContext.Provider>
    ),
  };
}
it("loads verified heldout artifacts without reading training updates", async () => {
  vi.stubGlobal("crypto", webcrypto);
  const v = view(report());
  render(v.ui);
  expect(
    await screen.findByRole("region", { name: "Tool policy comparison" }),
  ).toBeVisible();
  expect(screen.queryByText("987")).toBeNull();
  expect(v.download).toHaveBeenCalledWith(
    "/api/lab/artifacts/tool%2Freport",
    expect.any(AbortSignal),
  );
});
it("prefers independent evaluation over heldout tool role", async () => {
  vi.stubGlobal("crypto", webcrypto);
  const v = view(report(), hash(report()), "p", undefined, true);
  render(v.ui);
  await screen.findByRole("region", { name: "Tool policy comparison" });
  expect(v.download).toHaveBeenCalledTimes(1);
  expect(v.download.mock.calls[0][0]).toBe("/api/lab/artifacts/preferred");
});
it("requires checksum and rejects corrupted bytes", async () => {
  render(view(report(), "").ui);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "requires a retained report SHA-256 checksum",
  );
  cleanup();
  vi.stubGlobal("crypto", webcrypto);
  render(view(report(), "0".repeat(64)).ui);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "checksum mismatch",
  );
});
it("keeps historical unversioned reports unavailable and downloadable", async () => {
  render(view({ initial: [], final: [] }, "").ui);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Historical unversioned tool artifact: comparison unavailable",
  );
  expect(screen.queryByText("987")).toBeNull();
});
it("discards late downloads from a prior project", async () => {
  vi.stubGlobal("crypto", webcrypto);
  let resolveOld!: (b: Blob) => void;
  const download = vi
    .fn()
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveOld = resolve;
        }),
    )
    .mockResolvedValueOnce(new Blob([JSON.stringify(report())]));
  const v = render(view(report(), hash(report()), "old", download).ui);
  v.rerender(view(report(), hash(report()), "new", download).ui);
  await screen.findByRole("region", { name: "Tool policy comparison" });
  await act(async () => resolveOld(new Blob([JSON.stringify({ bad: true })])));
  expect(screen.queryByRole("alert")).toBeNull();
  expect(
    screen.getByRole("region", { name: "Tool policy comparison" }),
  ).toBeVisible();
});
it("does not consume tool comparison training records when the retained source is absent", () => {
  render(
    <RetainedBaselineComparison
      artifacts={[]}
      comparison={report()}
      records={[
        {
          loss: 987,
          task: { operation: "add", left: 2, right: 3 },
          tool: { result: 5 },
        },
      ]}
      runId="run"
      scopeKey="p"
      runState="succeeded"
    />,
  );
  expect(
    screen.getByText(/no retained independent comparison report/),
  ).toBeVisible();
  expect(screen.queryByText("987")).toBeNull();
});
it("surfaces download failure without a training fallback", async () => {
  const download = vi
    .fn()
    .mockRejectedValue(new Error("artifact access denied"));
  render(view(report(), hash(report()), "p", download).ui);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "artifact access denied",
  );
  expect(
    screen.queryByRole("region", { name: "Tool policy comparison" }),
  ).toBeNull();
});
it("loads validated fractional arithmetic whose finite result exceeds the safe integer range", async () => {
  vi.stubGlobal("crypto", webcrypto);
  const r = report();
  const row = r.records[0];
  row.task = { operation: "divide", left: 1, right: 1e-16 };
  row.expected = 1e16;
  for (const trace of [row.initial, row.final]) {
    trace.task = row.task;
    trace.tool.name = "divide";
    trace.tool.arguments = { left: 1, right: 1e-16 };
    trace.tool.result = 1e16;
    trace.evaluation.expected = 1e16;
    trace.evaluation.actual = 1e16;
  }
  render(view(r).ui);
  expect(
    await screen.findByRole("region", { name: "Tool policy comparison" }),
  ).toBeVisible();
  expect(screen.getAllByText("10000000000000000")).toHaveLength(3);
  expect(screen.queryByRole("alert")).toBeNull();
});
