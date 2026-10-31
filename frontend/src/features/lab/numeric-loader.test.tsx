import { Blob } from "node:buffer";
import { createHash, webcrypto } from "node:crypto";
import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { StudioContext, type StudioRuntime } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { RetainedBaselineComparison } from "./baseline-comparison";
const chart = vi.hoisted(() => ({ props: {} as any }));
vi.mock("./chart", () => ({
  Chart: (props: any) => {
    chart.props = props;
    return <div aria-label={props.label} />;
  },
}));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function report() {
  return {
    format: "tinystudio.numeric-evaluation.v1",
    task: "regression",
    partition: "validation",
    output_count: 2,
    model_sha256: "a".repeat(64),
    corpus_sha256: "b".repeat(64),
    baseline: {
      kind: "training_mean",
      label: "Training mean",
      fit_source_record_ids: ["t:0", "t:1"],
      fit_case_count: 2,
      constant: [1, 3],
    },
    records: [
      {
        case_id: "v:0",
        source_record_ids: ["v:0"],
        context_source_record_ids: [],
        prediction: [1, 2],
        target: [2, 4],
        baseline: [1, 3],
        raw_target_json: "[2,4]",
      },
    ],
    metrics: {
      cases: 1,
      scalar_values: 2,
      aggregation:
        "Unweighted validation cases and components; units not established",
      model: { mae: 1.5, mse: 2.5 },
      baseline: { mae: 1, mse: 1 },
      per_output: [
        {
          index: 0,
          unit: null,
          model: { mae: 1, mse: 1 },
          baseline: { mae: 1, mse: 1 },
        },
        {
          index: 1,
          unit: null,
          model: { mae: 2, mse: 4 },
          baseline: { mae: 1, mse: 1 },
        },
      ],
    },
    qualification: {
      status: "completed",
      comparison_complete: true,
      evaluation_valid: true,
      validation_only: true,
      scope: "Validation comparison only",
    },
  };
}
function view(
  payload: unknown,
  hash: string | undefined,
  evidence = new EvidenceRegistry(),
  role = "independent_evaluation",
) {
  const bytes = JSON.stringify(payload);
  const download = vi.fn().mockResolvedValue(new Blob([bytes]));
  return {
    download,
    ui: (
      <StudioContext.Provider
        value={
          {
            api: { download },
            route: "/lab",
            scopeKey: "project",
            project: "project",
            evidence,
          } as unknown as StudioRuntime
        }
      >
        <RetainedBaselineComparison
          artifacts={[
            {
              id: "numeric/report",
              name: "numeric-evaluation.json",
              role,
              sha256: hash,
            },
          ]}
          comparison={{ format: "tinystudio.numeric-evaluation.v1" }}
          records={[{ loss: 987, step: 1 }]}
          runId="numeric-run"
          scopeKey="project"
          runState="succeeded"
        />
      </StudioContext.Provider>
    ),
  };
}
it("loads checksum-verified validation cases and links point selection to the retained case, never training updates", async () => {
  vi.stubGlobal("crypto", webcrypto);
  const r = report();
  const evidence = new EvidenceRegistry();
  const result = view(
    r,
    createHash("sha256").update(JSON.stringify(r)).digest("hex"),
    evidence,
  );
  render(result.ui);
  expect(
    await screen.findByText(/SHA-256 verified · 1 validation cases/),
  ).toBeVisible();
  expect(
    screen.getByRole("region", { name: "Numeric baseline comparison" }),
  ).toBeVisible();
  expect(screen.queryByText("987")).toBeNull();
  const point = chart.props.option.series[0].data[0];
  act(() => chart.props.onPoint(point));
  expect(evidence.snapshot()[0].data).toMatchObject({
    run_id: "numeric-run",
    case: r.records[0],
    model_sha256: r.model_sha256,
  });
  expect(result.download).toHaveBeenCalledWith(
    "/api/lab/artifacts/numeric%2Freport",
    expect.any(AbortSignal),
  );
});
it("requires a checksum for numeric reports instead of accepting an unverified artifact", async () => {
  render(view(report(), undefined).ui);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "requires a retained report SHA-256 checksum",
  );
  expect(
    screen.queryByRole("region", { name: "Numeric baseline comparison" }),
  ).toBeNull();
});
it("rejects corrupted bytes before rendering numerical outcomes", async () => {
  vi.stubGlobal("crypto", webcrypto);
  render(view(report(), "0".repeat(64)).ui);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "checksum mismatch",
  );
});
it("keeps historical prediction-only artifacts explicit without synthesizing baselines or reading training records", () => {
  const result = view(
    report(),
    undefined,
    new EvidenceRegistry(),
    "held_out_predictions",
  );
  render(result.ui);
  expect(
    screen.getByText(/no retained independent comparison report/),
  ).toBeVisible();
  expect(result.download).not.toHaveBeenCalled();
  expect(
    screen.queryByRole("region", { name: "Numeric baseline comparison" }),
  ).toBeNull();
});
it("rejects a checksummed report with inconsistent output widths", async () => {
  vi.stubGlobal("crypto", webcrypto);
  const r = report();
  r.records[0].prediction = [1];
  render(
    view(r, createHash("sha256").update(JSON.stringify(r)).digest("hex")).ui,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "records[0].prediction: must contain 2 finite numbers",
  );
  expect(
    screen.queryByRole("region", { name: "Numeric baseline comparison" }),
  ).toBeNull();
});
