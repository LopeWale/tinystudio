import { useState } from "react";
import { Blob } from "node:buffer";
import { createHash, webcrypto } from "node:crypto";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { StudioContext, type StudioRuntime } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { makeDefinition, type ExperimentDefinition } from "./definition";
import { LabPage } from "./index";
import {
  StatisticsPlanning,
  RetainedStatisticsPlanning,
} from "./statistics-planning";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const field = (name: string, label: string, extra = {}) => ({
  name,
  label,
  type: "number",
  required: true,
  ...extra,
});
const catalog = {
  format: "tinystudio.statistics-methods.v1",
  methods: [
    {
      id: "means.independent",
      label: "Independent means",
      description: "Two independent groups.",
      assumptions: ["Independent observations."],
      references: ["noncentral t"],
      fields: [
        field("solve_for", "Solve for", {
          type: "choice",
          choices: [
            { id: "sample_size", name: "Sample size" },
            { id: "power", name: "Power" },
            { id: "effect_size", name: "Minimum detectable effect" },
          ],
        }),
        field("alpha", "Significance level"),
        field("power", "Target power", {
          when: { solve_for: ["sample_size", "effect_size"] },
        }),
        field("n", "Group 1 analysis observations", {
          when: { solve_for: ["power", "effect_size"] },
        }),
        field("delta", "Mean difference", {
          when: { solve_for: ["sample_size", "power"] },
        }),
        field("sd", "Standard deviation"),
        field("unit", "Measurement unit", { type: "string" }),
        field("alternative", "Alternative", {
          type: "choice",
          choices: [
            { id: "two-sided", name: "Two-sided" },
            { id: "greater", name: "Greater" },
            { id: "less", name: "Less" },
          ],
        }),
        field("ratio", "Group 2 / group 1 allocation"),
        field("attrition", "Expected attrition", { required: false }),
      ],
    },
    {
      id: "proportion.precision",
      label: "Proportion precision",
      description: "Normal approximation.",
      assumptions: ["Independent Bernoulli observations."],
      references: ["normal interval"],
      fields: [
        field("solve_for", "Solve for", {
          type: "choice",
          choices: [
            { id: "sample_size", name: "Sample size" },
            { id: "precision", name: "Precision" },
          ],
        }),
        field("alpha", "Significance level"),
        field("expected_proportion", "Expected proportion"),
        field("half_width", "Target half-width", {
          when: { solve_for: ["sample_size"] },
        }),
        field("n", "Analysis observations", {
          when: { solve_for: ["precision"] },
        }),
        field("attrition", "Expected attrition", { required: false }),
      ],
    },
  ],
};
catalog.methods.splice(1, 0, {
  ...catalog.methods[0],
  id: "means.paired",
  label: "Paired means",
  fields: catalog.methods[0].fields
    .filter((item) => item.name !== "ratio")
    .map((item) => ({
      ...item,
      label:
        item.name === "sd"
          ? "Standard deviation of paired differences"
          : item.name === "n"
            ? "Complete pairs"
            : item.label,
    })),
});
function studio(api: any = {}) {
  return {
    api: { get: vi.fn().mockResolvedValue(catalog), ...api },
    scopeKey: "user:project",
    route: "/lab",
    project: "project",
    user: { id: "user" },
    evidence: new EvidenceRegistry(),
  } as unknown as StudioRuntime;
}
function Harness() {
  const [definition, setDefinition] = useState(
    makeDefinition("statistics.plan"),
  );
  const [valid, setValid] = useState(false);
  return (
    <>
      <StatisticsPlanning
        definition={definition}
        onChange={(patch) => setDefinition((old) => ({ ...old, ...patch }))}
        onValidityChange={setValid}
      />
      <button disabled={!valid}>Save definition</button>
      <output data-testid="definition">{JSON.stringify(definition)}</output>
    </>
  );
}
const current = (): ExperimentDefinition =>
  JSON.parse(screen.getByTestId("definition").textContent!);
const edit = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
async function select(label: string, name: string) {
  await userEvent.click(
    screen.getByRole("button", { name: new RegExp(`${label}$`) }),
  );
  await userEvent.click(await screen.findByRole("option", { name }));
}
it("loads declared fields and preserves an empty numeric edit while blocking save", async () => {
  render(
    <StudioContext.Provider value={studio()}>
      <Harness />
    </StudioContext.Provider>,
  );
  await screen.findByLabelText("Target power");
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Save definition" }),
    ).toBeEnabled(),
  );
  edit("Target power", "");
  expect(screen.getByLabelText("Target power")).toHaveValue("");
  expect(
    screen.getByRole("button", { name: "Save definition" }),
  ).toBeDisabled();
  expect(current().parameters.power).toBe(0.8);
  edit("Target power", "0.9");
  expect(current().parameters.power).toBe(0.9);
  expect(screen.getByRole("button", { name: "Save definition" })).toBeEnabled();
});
it("changes calculation goal without carrying an inapplicable target power or inventing n", async () => {
  render(
    <StudioContext.Provider value={studio()}>
      <Harness />
    </StudioContext.Provider>,
  );
  await screen.findByLabelText("Target power");
  await select("Solve for", "Power");
  expect(current().parameters).not.toHaveProperty("power");
  expect(screen.queryByLabelText("Target power")).toBeNull();
  expect(screen.getByLabelText("Group 1 analysis observations")).toHaveValue(
    "",
  );
  expect(
    screen.getByRole("button", { name: "Save definition" }),
  ).toBeDisabled();
  edit("Group 1 analysis observations", "64");
  expect(current().parameters.n).toBe(64);
  expect(screen.getByRole("button", { name: "Save definition" })).toBeEnabled();
});
it("changes design and retains sources/sensitivity while removing fields the new design does not accept", async () => {
  render(
    <StudioContext.Provider value={studio()}>
      <Harness />
    </StudioContext.Provider>,
  );
  await screen.findByLabelText("Target power");
  edit("Input sources (JSON)", '{"sd":"pilot run"}');
  edit("Sensitivity scenarios (JSON)", '[{"alpha":0.01}]');
  await select("Statistical method", "Proportion precision");
  expect(current().parameters).toMatchObject({
    method: "proportion.precision",
    input_sources: { sd: "pilot run" },
    sensitivity: [{ alpha: 0.01 }],
  });
  expect(current().parameters).not.toHaveProperty("delta");
  expect(current().parameters).not.toHaveProperty("power");
  expect(screen.getByLabelText("Expected proportion")).toHaveValue("");
  expect(
    screen.getByRole("button", { name: "Save definition" }),
  ).toBeDisabled();
});
it("keeps malformed sensitivity drafts and rejects non-string input sources", async () => {
  render(
    <StudioContext.Provider value={studio()}>
      <Harness />
    </StudioContext.Provider>,
  );
  await screen.findByLabelText("Target power");
  edit("Sensitivity scenarios (JSON)", '[{"delta":');
  expect(
    screen.getByRole("button", { name: "Save definition" }),
  ).toBeDisabled();
  edit("Sensitivity scenarios (JSON)", '[{"delta":0.3}]');
  edit("Input sources (JSON)", '{"sd":42}');
  expect(
    screen.getByRole("button", { name: "Save definition" }),
  ).toBeDisabled();
  edit("Input sources (JSON)", '{"sd":"pilot"}');
  expect(screen.getByRole("button", { name: "Save definition" })).toBeEnabled();
});
it("blocks empty source notes and empty sensitivity overrides before saving", async () => {
  render(
    <StudioContext.Provider value={studio()}>
      <Harness />
    </StudioContext.Provider>,
  );
  await screen.findByLabelText("Target power");
  edit("Input sources (JSON)", '{"sd":""}');
  expect(
    screen.getByRole("button", { name: "Save definition" }),
  ).toBeDisabled();
  edit("Input sources (JSON)", '{"sd":"pilot"}');
  edit("Sensitivity scenarios (JSON)", "[{}]");
  expect(
    screen.getByRole("button", { name: "Save definition" }),
  ).toBeDisabled();
  edit("Sensitivity scenarios (JSON)", '[{"power":0.9}]');
  expect(screen.getByRole("button", { name: "Save definition" })).toBeEnabled();
});
it("validates sensitivity probabilities and rejects undeclared scenario fields", async () => {
  render(
    <StudioContext.Provider value={studio()}>
      <Harness />
    </StudioContext.Provider>,
  );
  await screen.findByLabelText("Target power");
  edit("Sensitivity scenarios (JSON)", '[{"power":0}]');
  expect(
    screen.getByRole("button", { name: "Save definition" }),
  ).toBeDisabled();
  edit("Sensitivity scenarios (JSON)", '[{"invented_variance":3}]');
  expect(
    screen.getByRole("button", { name: "Save definition" }),
  ).toBeDisabled();
  edit("Sensitivity scenarios (JSON)", '[{"power":0.9}]');
  expect(screen.getByRole("button", { name: "Save definition" })).toBeEnabled();
});
it("blocks mathematically inapplicable power and sample-size inputs before saving", async () => {
  render(
    <StudioContext.Provider value={studio()}>
      <Harness />
    </StudioContext.Provider>,
  );
  await screen.findByLabelText("Target power");
  const save = screen.getByRole("button", { name: "Save definition" });
  edit("Target power", "0.04");
  expect(save).toBeDisabled();
  edit("Target power", "0.8");
  edit("Mean difference", "0");
  expect(save).toBeDisabled();
  edit("Mean difference", "-0.5");
  await select("Alternative", "Greater");
  expect(save).toBeDisabled();
  await select("Alternative", "Two-sided");
  expect(save).toBeEnabled();
  edit("Sensitivity scenarios (JSON)", '[{"power":0.01}]');
  expect(save).toBeDisabled();
  edit("Sensitivity scenarios (JSON)", "[]");
  await select("Solve for", "Power");
  edit("Group 1 analysis observations", "1");
  expect(save).toBeDisabled();
  edit("Group 1 analysis observations", "2");
  expect(save).toBeEnabled();
});
it("requires a probability-scale half-width and permits one observation for fixed-sample precision", async () => {
  render(
    <StudioContext.Provider value={studio()}>
      <Harness />
    </StudioContext.Provider>,
  );
  await screen.findByLabelText("Target power");
  await select("Statistical method", "Proportion precision");
  const save = screen.getByRole("button", { name: "Save definition" });
  edit("Expected proportion", "0.5");
  edit("Target half-width", "1");
  expect(save).toBeDisabled();
  edit("Target half-width", "0.1");
  expect(save).toBeEnabled();
  await select("Solve for", "Precision");
  edit("Analysis observations", "1");
  expect(save).toBeEnabled();
});
it("clears assumptions and source notes when a design changes their statistical meaning", async () => {
  render(
    <StudioContext.Provider value={studio()}>
      <Harness />
    </StudioContext.Provider>,
  );
  await screen.findByLabelText("Target power");
  await select("Solve for", "Power");
  edit("Group 1 analysis observations", "100");
  edit(
    "Input sources (JSON)",
    '{"sd":"common SD pilot","n":"group 1 budget","alpha":"protocol"}',
  );
  edit(
    "Sensitivity scenarios (JSON)",
    '[{"sd":0.8,"n":64,"alpha":0.01,"input_sources":{"sd":"common SD range","n":"group 1 alternative","alpha":"protocol"}}]',
  );
  await select("Statistical method", "Paired means");
  expect(current().parameters).not.toHaveProperty("sd");
  expect(current().parameters).not.toHaveProperty("n");
  expect(current().parameters.input_sources).toEqual({ alpha: "protocol" });
  expect(current().parameters.sensitivity).toEqual([
    { alpha: 0.01, input_sources: { alpha: "protocol" } },
  ]);
  expect(
    screen.getByLabelText("Standard deviation of paired differences"),
  ).toHaveValue("");
  expect(screen.getByLabelText("Complete pairs")).toHaveValue("");
  const save = screen.getByRole("button", { name: "Save definition" });
  expect(save).toBeDisabled();
  edit("Standard deviation of paired differences", "0.7");
  edit("Complete pairs", "20");
  expect(save).toBeEnabled();
  await select("Statistical method", "Independent means");
  expect(current().parameters).not.toHaveProperty("sd");
  expect(current().parameters).not.toHaveProperty("n");
  edit("Standard deviation", "1");
  edit("Group 1 analysis observations", "50");
  edit("Group 2 / group 1 allocation", "1");
  edit("Input sources (JSON)", '{"n":"group 1 budget","alpha":"protocol"}');
  await select("Statistical method", "Proportion precision");
  expect(current().parameters.input_sources).toEqual({ alpha: "protocol" });
});
it("blocks saving when method discovery fails and exposes retry", async () => {
  const get = vi
    .fn()
    .mockRejectedValue(new Error("method service unavailable"));
  render(
    <StudioContext.Provider value={studio({ get })}>
      <Harness />
    </StudioContext.Provider>,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "method service unavailable",
  );
  expect(
    screen.getByRole("button", { name: "Save definition" }),
  ).toBeDisabled();
  get.mockResolvedValue(catalog);
  fireEvent.click(
    screen.getByRole("button", { name: "Retry statistical methods" }),
  );
  await screen.findByLabelText("Target power");
});
it("saves and submits statistical planning through the existing Laboratory workflow", async () => {
  vi.stubGlobal("crypto", webcrypto);
  let saved: any;
  const r = report(),
    bytes = JSON.stringify(r);
  const artifact = {
    id: "stats-report",
    name: "statistics-plan.json",
    role: "statistical_plan",
    sha256: createHash("sha256").update(bytes).digest("hex"),
  };
  const api = {
    get: vi.fn(async (path: string): Promise<any> => {
      if (path === "/api/lab/catalog") return { adapters: [], methods: [] };
      if (path === "/api/lab/statistics/methods") return catalog;
      if (path === "/api/lab/targets") return [{ id: "local", name: "Local" }];
      if (path === "/api/lab/definitions") return saved ? [saved] : [];
      if (path === "/api/lab/runs/stats-run")
        return {
          id: "stats-run",
          definition_id: "saved-plan",
          state: "succeeded",
          spec: { definition: saved.definition },
          result: { artifacts: [artifact] },
        };
      if (path.includes("/events?")) return { events: [], next_after: 0 };
      if (path.includes("/records?")) return { records: [] };
      return [];
    }),
    post: vi.fn(async (path: string, body: any): Promise<any> => {
      if (path === "/api/lab/definitions")
        return (saved = {
          id: "saved-plan",
          name: body.definition.name,
          revision: 1,
          definition: body.definition,
        });
      if (path === "/api/lab/runs")
        return {
          id: "stats-run",
          definition_id: "saved-plan",
          state: "queued",
        };
      return {};
    }),
    download: vi.fn().mockResolvedValue(new Blob([bytes])),
  };
  render(
    <StudioContext.Provider
      value={{ ...studio(api), toast: vi.fn() } as unknown as StudioRuntime}
    >
      <LabPage />
    </StudioContext.Provider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Open experiment" }));
  await select("Experiment engine", "Statistical planning");
  await screen.findByLabelText("Target power");
  edit("Target power", "");
  expect(
    screen.getByRole("button", { name: "Save experiment" }),
  ).toBeDisabled();
  expect(
    screen.getByRole("button", { name: "Check compatibility" }),
  ).toBeDisabled();
  edit("Target power", "0.8");
  fireEvent.click(screen.getByRole("button", { name: "Save experiment" }));
  await waitFor(() => expect(saved.definition.engine).toBe("statistics.plan"));
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Run saved revision" }),
    ).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole("button", { name: "Run saved revision" }));
  await screen.findByRole(
    "region",
    { name: "Statistical planning report" },
    { timeout: 5000 },
  );
  expect(api.post).toHaveBeenCalledWith(
    "/api/lab/runs",
    expect.objectContaining({
      definition_id: "saved-plan",
      revision: 1,
      target_id: "local",
    }),
    expect.any(AbortSignal),
  );
});
function report() {
  return {
    format: "tinystudio.statistics-plan.v1",
    method: {
      id: "means.independent",
      label: "Independent means",
      version: "1",
      assumptions: ["Independent observations."],
      references: ["noncentral t"],
    },
    request: makeDefinition("statistics.plan").parameters,
    request_sha256: "a".repeat(64),
    source_code_sha256: "b".repeat(64),
    runtime: { python: "3.12", scipy: "1.17" },
    records: [
      {
        scenario_id: "primary",
        inputs: { delta: 0.5 },
        result: {
          analysis_n: { group_1: 64, group_2: 64 },
          recruitment_n: { group_1: 72, group_2: 72 },
          total_analysis_n: 128,
          total_recruitment_n: 144,
          achieved_power: 0.801,
          target_power: 0.8,
          effect_size: 0.5,
          expected_half_width: null,
          warnings: [],
          rounding: "Integer ceiling",
        },
      },
      {
        scenario_id: "sensitivity-1",
        inputs: { delta: 0.4 },
        result: {
          analysis_n: { group_1: 100, group_2: 100 },
          recruitment_n: { group_1: 112, group_2: 112 },
          total_analysis_n: 200,
          total_recruitment_n: 224,
          achieved_power: 0.805,
          target_power: 0.8,
          effect_size: 0.4,
          expected_half_width: null,
          warnings: ["Estimated pilot variance"],
          rounding: "Integer ceiling",
        },
      },
    ],
    qualification: { kind: "planning_estimate" },
  };
}
function retained(
  payload: unknown = report(),
  checksum?: string,
  id = "plan-a",
  runtime = studio(),
) {
  const bytes = JSON.stringify(payload);
  const sha256 = checksum ?? createHash("sha256").update(bytes).digest("hex");
  return {
    runtime,
    bytes,
    ui: (
      <StudioContext.Provider value={runtime}>
        <RetainedStatisticsPlanning
          artifacts={[
            {
              id,
              name: "statistics-plan.json",
              role: "statistical_plan",
              sha256,
            },
          ]}
          runId={id}
          scopeKey={runtime.scopeKey}
        />
      </StudioContext.Provider>
    ),
  };
}
it("shows verified primary/sensitivity counts, assumptions and original calculation evidence", async () => {
  vi.stubGlobal("crypto", webcrypto);
  const r = report(),
    evidence = studio({
      download: vi.fn().mockResolvedValue(new Blob([JSON.stringify(r)])),
    });
  render(retained(r, undefined, "plan-a", evidence).ui);
  expect(await screen.findByText(/SHA-256 verified/)).toBeVisible();
  expect(
    screen.getByRole("region", { name: "Statistical planning report" }),
  ).toBeVisible();
  expect(screen.getByText("144")).toBeVisible();
  expect(screen.getByText("sensitivity-1")).toBeVisible();
  expect(screen.getByText("Independent observations.")).toBeVisible();
  expect(
    evidence.evidence
      .snapshot()
      .some(
        (item) =>
          (item.data as any).report?.request_sha256 === r.request_sha256,
      ),
  ).toBe(true);
});
it("refuses corrupt artifact bytes instead of showing plausible counts", async () => {
  vi.stubGlobal("crypto", webcrypto);
  const r = report(),
    runtime = studio({
      download: vi.fn().mockResolvedValue(new Blob([JSON.stringify(r)])),
    });
  render(retained(r, "0".repeat(64), "corrupt", runtime).ui);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "checksum mismatch",
  );
  expect(screen.queryByText("144")).toBeNull();
});
it("refuses a checksummed report whose totals contradict its group counts", async () => {
  vi.stubGlobal("crypto", webcrypto);
  const r = report();
  r.records[0].result.total_analysis_n = 1000;
  const runtime = studio({
    download: vi.fn().mockResolvedValue(new Blob([JSON.stringify(r)])),
  });
  render(retained(r, undefined, "invalid", runtime).ui);
  expect(await screen.findByRole("alert")).toHaveTextContent("invalid");
});
it("masks the previous run immediately and ignores a late response after selection changes", async () => {
  vi.stubGlobal("crypto", webcrypto);
  let resolve!: (value: Blob) => void;
  const pending = new Promise<Blob>((done) => {
    resolve = done;
  });
  const download = vi
    .fn()
    .mockResolvedValueOnce(new Blob([JSON.stringify(report())]))
    .mockReturnValueOnce(pending);
  const runtime = studio({ download });
  const view = render(retained(report(), undefined, "plan-a", runtime).ui);
  await screen.findByText(/SHA-256 verified/);
  view.rerender(retained(report(), undefined, "plan-b", runtime).ui);
  expect(screen.queryByText("144")).toBeNull();
  view.rerender(
    <StudioContext.Provider value={runtime}>
      <RetainedStatisticsPlanning
        artifacts={[]}
        runId="plan-c"
        scopeKey="user:other"
      />
    </StudioContext.Provider>,
  );
  await act(async () => {
    resolve(new Blob([JSON.stringify(report())]));
    await pending;
  });
  expect(screen.queryByText("144")).toBeNull();
  expect(screen.getByText(/No retained statistical plan/)).toBeVisible();
});
