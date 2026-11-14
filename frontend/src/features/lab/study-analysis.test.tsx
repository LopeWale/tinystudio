import { createHash, webcrypto } from "node:crypto";
import { Blob } from "node:buffer";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StudioContext, type StudioRuntime } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { createApiClient } from "../../api/client";
import { scientificJson } from "../../api/scientific-json";
import { WorkspaceScope } from "../../controllers/workspaceScope";
import type { LabRun } from "./definition";
import { StudyAnalysisPanel, StudyAnalysisResult } from "./study-analysis";
import { Studies } from "./studies";

beforeEach(() => {
  vi.stubGlobal("Blob", Blob);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const hash = (text: string) => createHash("sha256").update(text).digest("hex");
const study = {
  id: "study-1",
  revision: 3,
  last_sequence: 12,
  study: { independent_unit: "A separately assigned device" },
};
const source = {
  study_id: "study-1",
  revision: 3,
  through_seq: 12,
  protocol_digest: "a".repeat(64),
  sha256: "b".repeat(64),
};
const catalog = {
  format: "tinystudio.analysis-methods.v1",
  methods: [
    {
      id: "means.welch",
      version: "1.0.0",
      label: "Welch independent means",
      description: "Group A minus group B",
      assumptions: ["Researcher justifies independent units."],
      references: ["https://example.org/welch"],
      dependencies: ["scipy"],
    },
    {
      id: "means.paired",
      version: "1.0.0",
      label: "Paired means",
      description: "Aligned pairs",
      assumptions: ["Different pairs are independent."],
      references: [],
      dependencies: ["scipy"],
    },
  ],
};
type Call = { path: string; method: string; body?: any; raw?: string };
function fixture(
  overrides?: (
    call: Call,
    signal?: AbortSignal | null,
  ) => unknown | Promise<unknown>,
) {
  const calls: Call[] = [];
  let project = "alpha";
  const scope = new WorkspaceScope();
  scope.setIdentity("researcher:alpha");
  const fetcher = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const call: Call = {
        path: String(input),
        method: init?.method ?? "GET",
        raw: typeof init?.body === "string" ? init.body : undefined,
      };
      if (call.raw) call.body = scientificJson.parse(call.raw);
      calls.push(call);
      const override = await overrides?.(call, init?.signal);
      let value: unknown = override;
      if (value === undefined) {
        if (call.path.endsWith("/statistics/analysis-methods")) value = catalog;
        else if (call.path.endsWith("/targets"))
          value = [{ id: "runner-1", name: "Research runner" }];
        else if (call.path.endsWith("/inputs"))
          value = [
            { id: "code-1", name: "clustered.py", sha256: "e".repeat(64) },
          ];
        else if (call.path.endsWith("/analysis-inputs"))
          value = {
            id: "snapshot-op",
            state: "succeeded",
            kind: "study.analysis-input",
            result: {
              artifact: {
                id: "snapshot-1",
                name: "study.jsonl",
                sha256: "b".repeat(64),
              },
              source: {
                ...source,
                study_id: call.path.includes("study-2") ? "study-2" : "study-1",
                revision: call.body.revision,
                through_seq: call.body.through_seq,
              },
            },
          };
        else if (call.path.endsWith("/statistics/analyses"))
          value = {
            id: "analysis-1",
            revision: 1,
            name: call.body.name,
            definition: {
              schema_version: 1,
              name: call.body.name,
              engine: "statistics.analyze",
              parameters: call.body.parameters,
              execution: call.body.execution,
              inputs: [
                {
                  artifact_id: call.body.snapshot_artifact_id,
                  sha256: "b".repeat(64),
                  role: "study_snapshot",
                },
              ],
            },
          };
        else if (call.path.endsWith("/preflight"))
          value = {
            compatible: true,
            checks: [
              {
                code: "engine",
                passed: true,
                message: "Target can execute retained analysis.",
              },
            ],
          };
        else if (call.path.endsWith("/runs") && call.method === "POST")
          value = {
            id: "run-1",
            state: "queued",
            definition_id: "analysis-1",
            result: null,
          };
        else if (call.path.endsWith("/runs/run-1"))
          value = {
            id: "run-1",
            state: "queued",
            definition_id: "analysis-1",
            result: null,
          };
        else if (call.path.endsWith("/analysis-results"))
          value = { id: "linked-record", seq: 13 };
        else
          throw new Error(
            `Unexpected HTTP request ${call.method} ${call.path}`,
          );
      }
      if (value instanceof Response) return value;
      return new Response(scientificJson.stringify(value), {
        headers: { "content-type": "application/json" },
      });
    },
  ) as typeof fetch;
  const api = createApiClient({
    scope,
    getProject: () => project,
    onUnauthorized: vi.fn(),
    fetcher,
  });
  const runtime = (name = project): StudioRuntime =>
    ({
      api,
      scopeKey: `researcher:${name}`,
      project: name,
      user: { id: "researcher" },
      route: "/lab",
      evidence: new EvidenceRegistry(),
      toast: vi.fn(),
    }) as unknown as StudioRuntime;
  return {
    calls,
    runtime,
    api,
    changeProject(name: string) {
      project = name;
      scope.setIdentity(`researcher:${name}`);
    },
  };
}
function trackingFixture(
  overrides?: (
    call: Call,
    signal?: AbortSignal | null,
  ) => unknown | Promise<unknown>,
) {
  return fixture((call, signal) => {
    const path = call.path.replace("/api/p/alpha", "/api");
    const document = (name: string, revision: number) => ({
      schema_version: 1,
      name,
      description: `Protocol ${revision}`,
      hypothesis: "",
      independent_unit: "A declared device",
      status: "draft",
      conditions: [],
      outcomes: [],
      sampling_plan: {},
      stopping_rules: {},
      metadata: {},
    });
    if (path === "/api/lab/studies")
      return ["Thermal study", "Other study"].map((name, index) => ({
        id: `study-${index + 1}`,
        name,
        status: "draft",
        revision: 2,
        record_count: 12,
      }));
    const match = path.match(
      /^\/api\/lab\/studies\/(study-[12])(?:\?revision=(\d+))?$/,
    );
    if (match) {
      const revision = Number(match[2] ?? 2);
      return {
        id: match[1],
        revision,
        head_revision: 2,
        study: document(
          match[1] === "study-1" ? "Thermal study" : "Other study",
          revision,
        ),
        digest: "a".repeat(64),
        author_id: "researcher",
        recorded_at: "2026-11-13T12:00:00Z",
        summary: { records: 12, declared_unit_ids: 12 },
        last_sequence: 12,
      };
    }
    if (path.endsWith("/revisions"))
      return [1, 2].map((revision) => ({
        revision,
        name: "Retained protocol",
        status: "draft",
        author_id: "researcher",
        digest: "a".repeat(64),
        recorded_at: "2026-11-13T12:00:00Z",
      }));
    if (path.includes("/records?"))
      return { records: [], next_after: 0, latest_seq: 12, has_more: false };
    return overrides?.(call, signal);
  });
}

it.each(["study", "revision"] as const)(
  "guards analysis-only drafts and retained snapshot associations during %s navigation",
  async (destination) => {
    const f = trackingFixture();
    render(
      <StudioContext.Provider value={f.runtime()}>
        <Studies />
      </StudioContext.Provider>,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Open study Thermal study" }),
    );
    await screen.findByRole("button", { name: "Open protocol revision 1" });
    edit("Analysis name", "Unfinished analysis choices");
    await choose("Analysis method", "Welch independent means");
    edit("Confidence probability", ".");
    const navigate = () =>
      click(
        destination === "study"
          ? "Open study Other study"
          : "Open protocol revision 1",
      );
    const keep =
      destination === "study"
        ? "Keep current study draft"
        : "Keep current protocol draft";
    const discard =
      destination === "study"
        ? "Discard draft and open study"
        : "Discard draft and open protocol revision";
    navigate();
    expect(screen.getByRole("button", { name: keep })).toBeVisible();
    expect(screen.getByLabelText("Analysis name")).toHaveValue(
      "Unfinished analysis choices",
    );
    click(keep);
    click("Retain analysis snapshot");
    await screen.findByText(/Snapshot retained/);
    navigate();
    expect(screen.getByRole("button", { name: keep })).toBeVisible();
    expect(screen.getByLabelText("Analysis name")).toHaveValue(
      "Unfinished analysis choices",
    );
    expect(screen.getByLabelText("Confidence probability")).toHaveValue(".");
    expect(screen.getByText(/Snapshot retained/)).toBeVisible();
    click(keep);
    expect(screen.queryByRole("button", { name: discard })).toBeNull();
    navigate();
    click(discard);
    if (destination === "study")
      await waitFor(() =>
        expect(screen.getByLabelText("Study name")).toHaveValue("Other study"),
      );
    else await screen.findByText(/Pinned protocol revision 1/);
    expect(screen.getByLabelText("Analysis name")).toHaveValue("");
    expect(screen.queryByText(/Snapshot retained/)).toBeNull();
    expect(
      f.calls.filter((call) => call.method === "POST").map((call) => call.path),
    ).toEqual(["/api/p/alpha/lab/studies/study-1/analysis-inputs"]);
  },
);

it("guards an analysis snapshot request before any form choice is edited", async () => {
  let finish!: (value: unknown) => void;
  const pending = new Promise((resolve) => {
    finish = resolve;
  });
  const f = trackingFixture((call) =>
    call.path.endsWith("/analysis-inputs") ? pending : undefined,
  );
  render(
    <StudioContext.Provider value={f.runtime()}>
      <Studies />
    </StudioContext.Provider>,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "Open study Thermal study" }),
  );
  await screen.findByRole("button", { name: "Open protocol revision 1" });
  click("Retain analysis snapshot");
  await waitFor(() =>
    expect(f.calls.some((call) => call.path.endsWith("/analysis-inputs"))).toBe(
      true,
    ),
  );
  click("Open protocol revision 1");
  expect(
    screen.getByRole("button", { name: "Keep current protocol draft" }),
  ).toBeVisible();
  click("Keep current protocol draft");
  await act(async () =>
    finish({
      id: "snapshot-op",
      state: "succeeded",
      kind: "study.analysis-input",
      result: {
        artifact: {
          id: "snapshot-1",
          name: "study.jsonl",
          sha256: "b".repeat(64),
        },
        source: { ...source, revision: 2 },
      },
    }),
  );
  await screen.findByText(/Snapshot retained/);
  click("Open study Other study");
  expect(
    screen.getByRole("button", { name: "Keep current study draft" }),
  ).toBeVisible();
  click("Keep current study draft");
  expect(screen.getByText(/Snapshot retained/)).toBeVisible();
});
const edit = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
const click = (name: string) =>
  fireEvent.click(screen.getByRole("button", { name }));
async function choose(label: string, name: string) {
  await userEvent.click(
    screen.getByRole("button", { name: new RegExp(`${label}$`) }),
  );
  await userEvent.click(await screen.findByRole("option", { name }));
}
async function authorBuiltin() {
  await choose("Analysis method", "Welch independent means");
  edit("Analysis name", "Retained temperature contrast");
  await choose("Record inclusion", "All records through the chosen boundary");
  edit("Confidence probability", "0.91");
  await choose("Interval alternative", "Two-sided");
  edit("Condition A value", "control");
  edit("Condition B value", "heated");
  edit("Outcome path", "/values/temperature");
  edit("Condition path", "/condition");
  edit("Independent-unit path", "/unit_id");
  await choose("Outcome scale", "Named measurement unit");
  edit("Outcome measurement unit", "degrees C");
  await choose(
    "Repeated measurements per unit and condition",
    "Stop and require a decision",
  );
  await choose(
    "Missing outcomes or unit identifiers",
    "Stop and require a decision",
  );
}

it("walks a researcher from an explicit boundary and observations to a saved checked analysis run", async () => {
  const f = fixture(),
    onRunCreated = vi.fn();
  render(
    <StudioContext.Provider value={f.runtime()}>
      <StudyAnalysisPanel study={study} onRunCreated={onRunCreated} />
    </StudioContext.Provider>,
  );
  expect(screen.getByLabelText("Protocol revision for analysis")).toHaveValue(
    "3",
  );
  expect(screen.getByLabelText("Last included ledger sequence")).toHaveValue(
    "12",
  );
  expect(screen.getByRole("button", { name: "Save analysis" })).toBeDisabled();
  expect(
    screen.getByText(/ledger corrections remain bookkeeping/i),
  ).toBeVisible();
  await authorBuiltin();
  click("Retain analysis snapshot");
  await screen.findByText(/Snapshot retained/);
  click("Save analysis");
  await screen.findByText(/Saved analysis revision 1/);
  await choose("Analysis execution target", "Research runner");
  click("Check analysis compatibility");
  await screen.findByText("Target can execute retained analysis.");
  click("Run saved analysis");
  await waitFor(() =>
    expect(onRunCreated).toHaveBeenCalledWith(
      expect.objectContaining({ id: "run-1" }),
    ),
  );
  const authored = f.calls.find((call) =>
    call.path.endsWith("/statistics/analyses"),
  )!;
  expect(authored.path).toBe("/api/p/alpha/lab/statistics/analyses");
  expect(authored.body).toMatchObject({
    snapshot_artifact_id: "snapshot-1",
    parameters: {
      method: "means.welch",
      confidence: 0.91,
      repeated: "error",
      missing: "error",
      groups: ["control", "heated"],
    },
  });
  expect(authored.body.parameters).not.toHaveProperty("n");
  expect(
    f.calls.find((call) => call.path.endsWith("/analysis-inputs"))!.body,
  ).toMatchObject({ revision: 3, through_seq: 12 });
}, 15000);

it("retries uncertain run admission with the same key and uses a fresh key for an intentional next run", async () => {
  let submissions = 0;
  const f = fixture((call) => {
      if (!call.path.endsWith("/runs") || call.method !== "POST")
        return undefined;
      submissions++;
      if (submissions === 1)
        return new Response(
          '{"detail":"Run admission response unavailable; retry the same request"}',
          { status: 503, headers: { "content-type": "application/json" } },
        );
      return {
        id: submissions === 2 ? "run-1" : "run-2",
        state: "succeeded",
        definition_id: "analysis-1",
        result: null,
      };
    }),
    onRunCreated = vi.fn();
  render(
    <StudioContext.Provider value={f.runtime()}>
      <StudyAnalysisPanel study={study} onRunCreated={onRunCreated} />
    </StudioContext.Provider>,
  );
  await authorBuiltin();
  click("Retain analysis snapshot");
  await screen.findByText(/Snapshot retained/);
  click("Save analysis");
  await screen.findByText(/Saved analysis revision 1/);
  await choose("Analysis execution target", "Research runner");
  click("Check analysis compatibility");
  await screen.findByText("Target can execute retained analysis.");
  click("Run saved analysis");
  await screen.findByRole("alert");
  expect(onRunCreated).not.toHaveBeenCalled();
  click("Run saved analysis");
  await waitFor(() => expect(onRunCreated).toHaveBeenCalledTimes(1));
  const admitted = f.calls.filter(
    (call) => call.path.endsWith("/runs") && call.method === "POST",
  );
  expect(admitted[0].body.idempotency_key).toBe(
    admitted[1].body.idempotency_key,
  );
  edit("Analysis name", "Unsubmitted edit");
  expect(screen.getByText("Analysis run run-1: succeeded")).toBeVisible();
  edit("Analysis name", "Retained temperature contrast");
  click("Run another saved analysis");
  await waitFor(() => expect(onRunCreated).toHaveBeenCalledTimes(2));
  const next = f.calls
    .filter((call) => call.path.endsWith("/runs") && call.method === "POST")
    .at(-1)!;
  expect(next.body.idempotency_key).not.toBe(admitted[1].body.idempotency_key);
  expect(next.body).toMatchObject({
    definition_id: "analysis-1",
    revision: 1,
    target_id: "runner-1",
  });
  expect(onRunCreated).toHaveBeenLastCalledWith(
    expect.objectContaining({ id: "run-2" }),
  );
}, 15000);

it("keeps invalid drafts, confidence and independent-unit mapping when the analysis method changes", async () => {
  const f = fixture();
  render(
    <StudioContext.Provider value={f.runtime()}>
      <StudyAnalysisPanel study={study} />
    </StudioContext.Provider>,
  );
  await authorBuiltin();
  click("Retain analysis snapshot");
  await screen.findByText(/Snapshot retained/);
  edit("Confidence probability", "");
  expect(screen.getByLabelText("Confidence probability")).toHaveValue("");
  expect(screen.getByRole("button", { name: "Save analysis" })).toBeDisabled();
  edit("Confidence probability", "0.93");
  await choose("Condition value format", "JSON values (advanced)");
  edit("Condition A value (JSON)", "9007199254740993");
  edit("Condition B value (JSON)", '"heated"');
  expect(screen.getByLabelText("Condition A value (JSON)")).toHaveValue(
    "9007199254740993",
  );
  expect(screen.getByRole("button", { name: "Save analysis" })).toBeDisabled();
  edit("Condition A value (JSON)", "-0.0");
  await choose("Analysis method", "Paired means");
  expect(screen.getByLabelText("Confidence probability")).toHaveValue("0.93");
  expect(screen.getByLabelText("Independent-unit path")).toHaveValue(
    "/unit_id",
  );
  expect(screen.getByText(/same declared unit identifier/i)).toBeVisible();
  expect(screen.getByRole("button", { name: "Save analysis" })).toBeDisabled();
  await choose(
    "Incomplete pairs",
    "Omit incomplete pairs and retain diagnostics",
  );
  click("Save analysis");
  await screen.findByText(/Saved analysis revision 1/);
  const authored = f.calls.find((call) =>
    call.path.endsWith("/statistics/analyses"),
  )!;
  expect(authored.raw).toContain('"groups":[-0.0,"heated"]');
  edit("Analysis name", "Changed contrast");
  expect(
    screen.getByRole("button", { name: "Run saved analysis" }),
  ).toBeDisabled();
}, 15000);

it("accepts a named versioned custom Python method and preserves authored numeric JSON", async () => {
  const f = fixture();
  render(
    <StudioContext.Provider value={f.runtime()}>
      <StudyAnalysisPanel study={study} />
    </StudioContext.Provider>,
  );
  await choose("Analysis method", "Custom Python analysis");
  edit("Analysis name", "Site-adjusted model");
  await choose("Record inclusion", "Only specified record IDs");
  edit("Included record IDs (one per line)", "old-record\ncorrection");
  edit("Custom method name", "Site-aware model");
  edit("Custom method version", "v2.3");
  await choose("Python source-code artifact", "clustered.py");
  edit("Custom method options (JSON)", '{"offset":-0.0,"large_float":1e30}');
  expect(screen.getByText(/operator.*native execution grant/i)).toBeVisible();
  expect(screen.queryByLabelText("Confidence probability")).toBeNull();
  click("Retain analysis snapshot");
  await screen.findByText(/Snapshot retained/);
  click("Save analysis");
  await screen.findByText(/Saved analysis revision 1/);
  const authored = f.calls.find((call) =>
    call.path.endsWith("/statistics/analyses"),
  )!;
  expect(authored.body).toMatchObject({
    custom_code_artifact_id: "code-1",
    parameters: {
      method: "custom.python",
      custom_method: { name: "Site-aware model", version: "v2.3" },
      selection: { record_ids: ["old-record", "correction"] },
    },
  });
  expect(authored.raw).toContain('"offset":-0.0');
  expect(authored.raw).toContain('"large_float":1e+30');
  expect(authored.body.parameters).not.toHaveProperty("repeated");
});

it("aborts and ignores a late snapshot response when study or project ownership changes", async () => {
  let finish!: (value: unknown) => void;
  const pending = new Promise((resolve) => {
    finish = resolve;
  });
  const f = fixture((call) =>
    call.path.endsWith("/studies/study-1/analysis-inputs")
      ? pending
      : undefined,
  );
  const ui = (item = study) => (
    <StudioContext.Provider value={f.runtime()}>
      <StudyAnalysisPanel study={item} />
    </StudioContext.Provider>
  );
  const view = render(ui());
  click("Retain analysis snapshot");
  await waitFor(() =>
    expect(f.calls.some((call) => call.path.endsWith("/analysis-inputs"))).toBe(
      true,
    ),
  );
  view.rerender(ui({ ...study, id: "study-2" }));
  expect(screen.getByLabelText("Analysis name")).toHaveValue("");
  await act(async () =>
    finish({
      id: "late",
      state: "succeeded",
      kind: "snapshot",
      result: {
        artifact: {
          id: "old-snapshot",
          name: "old.jsonl",
          sha256: "b".repeat(64),
        },
        source,
      },
    }),
  );
  expect(screen.queryByText(/Snapshot retained/)).toBeNull();
  click("Retain analysis snapshot");
  await screen.findByText(/Snapshot retained/);
  f.changeProject("beta");
  view.rerender(ui({ ...study, id: "study-2" }));
  expect(screen.queryByText(/Snapshot retained/)).toBeNull();
  expect(screen.getByRole("button", { name: "Save analysis" })).toBeDisabled();
});

it("adopts fresh same-study history before boundary selection and explicitly advances a captured boundary", async () => {
  const f = fixture(),
    value = f.runtime();
  const ui = (revision: number, last_sequence: number) => (
    <StudioContext.Provider value={value}>
      <StudyAnalysisPanel study={{ ...study, revision, last_sequence }} />
    </StudioContext.Provider>
  );
  const view = render(ui(1, 0));
  edit("Analysis name", "Keep my authored contrast");
  await choose("Analysis method", "Welch independent means");
  edit("Confidence probability", "0.87");
  view.rerender(ui(1, 4));
  expect(screen.getByLabelText("Last included ledger sequence")).toHaveValue(
    "4",
  );
  view.rerender(ui(2, 4));
  expect(screen.getByLabelText("Protocol revision for analysis")).toHaveValue(
    "2",
  );
  expect(screen.getByLabelText("Analysis name")).toHaveValue(
    "Keep my authored contrast",
  );
  expect(screen.getByLabelText("Confidence probability")).toHaveValue("0.87");
  click("Retain analysis snapshot");
  await screen.findByText(
    /Snapshot retained.*protocol revision 2, ledger sequence 4/,
  );
  view.rerender(ui(3, 7));
  expect(screen.getByLabelText("Protocol revision for analysis")).toHaveValue(
    "2",
  );
  expect(screen.getByLabelText("Last included ledger sequence")).toHaveValue(
    "4",
  );
  expect(
    screen.getByText(
      /Snapshot retained.*protocol revision 2, ledger sequence 4/,
    ),
  ).toBeVisible();
  click("Use current retained boundary");
  expect(screen.getByLabelText("Protocol revision for analysis")).toHaveValue(
    "3",
  );
  expect(screen.getByLabelText("Last included ledger sequence")).toHaveValue(
    "7",
  );
  expect(screen.queryByText(/Snapshot retained/)).toBeNull();
  expect(screen.getByLabelText("Analysis name")).toHaveValue(
    "Keep my authored contrast",
  );
  expect(screen.getByLabelText("Confidence probability")).toHaveValue("0.87");
  expect(
    f.calls.filter((call) => call.path.endsWith("/analysis-inputs")),
  ).toHaveLength(1);
  click("Retain analysis snapshot");
  await screen.findByText(
    /Snapshot retained.*protocol revision 3, ledger sequence 7/,
  );
  expect(
    f.calls.filter((call) => call.path.endsWith("/analysis-inputs")).at(-1)!
      .body,
  ).toMatchObject({ revision: 3, through_seq: 7 });
});

it("retains explicitly edited and invalid boundaries across same-study protocol and ledger refreshes", async () => {
  const f = fixture(),
    value = f.runtime();
  const ui = (revision: number, last_sequence: number) => (
    <StudioContext.Provider value={value}>
      <StudyAnalysisPanel study={{ ...study, revision, last_sequence }} />
    </StudioContext.Provider>
  );
  const view = render(ui(2, 4));
  edit("Protocol revision for analysis", "1");
  edit("Last included ledger sequence", "2");
  view.rerender(ui(3, 8));
  expect(screen.getByLabelText("Protocol revision for analysis")).toHaveValue(
    "1",
  );
  expect(screen.getByLabelText("Last included ledger sequence")).toHaveValue(
    "2",
  );
  edit("Last included ledger sequence", "");
  view.rerender(ui(4, 10));
  expect(screen.getByLabelText("Last included ledger sequence")).toHaveValue(
    "",
  );
  expect(
    screen.getByRole("button", { name: "Retain analysis snapshot" }),
  ).toBeDisabled();
  click("Use current retained boundary");
  expect(screen.getByLabelText("Protocol revision for analysis")).toHaveValue(
    "4",
  );
  expect(screen.getByLabelText("Last included ledger sequence")).toHaveValue(
    "10",
  );
  view.rerender(ui(5, 11));
  expect(screen.getByLabelText("Protocol revision for analysis")).toHaveValue(
    "4",
  );
  expect(screen.getByLabelText("Last included ledger sequence")).toHaveValue(
    "10",
  );
});

it("uses the current retained head while a historical protocol remains displayed in Study tracking", async () => {
  const document = (revision: number) => ({
    schema_version: 1,
    name: "Thermal study",
    description: `Protocol ${revision}`,
    hypothesis: "",
    independent_unit:
      revision === 1 ? "Original device unit" : "Revised device unit",
    status: "draft",
    conditions: [],
    outcomes: [],
    sampling_plan: {},
    stopping_rules: {},
    metadata: {},
  });
  const envelope = (revision: number) => ({
    id: "study-1",
    revision,
    head_revision: 2,
    study: document(revision),
    digest: "a".repeat(64),
    author_id: "researcher",
    recorded_at: "2026-11-13T12:00:00Z",
    summary: { records: 5, declared_unit_ids: 5 },
    last_sequence: 5,
  });
  const f = fixture((call) => {
    const path = call.path.replace("/api/p/alpha", "/api");
    if (path === "/api/lab/studies")
      return [
        {
          id: "study-1",
          name: "Thermal study",
          status: "draft",
          revision: 2,
          record_count: 5,
        },
      ];
    if (path === "/api/lab/studies/study-1") return envelope(2);
    if (path === "/api/lab/studies/study-1?revision=1") return envelope(1);
    if (path.endsWith("/revisions"))
      return [1, 2].map((revision) => ({
        revision,
        name: "Thermal study",
        status: "draft",
        author_id: "researcher",
        digest: "a".repeat(64),
        recorded_at: "2026-11-13T12:00:00Z",
      }));
    if (path.includes("/records?"))
      return {
        records: Array.from({ length: 5 }, (_, index) => ({
          id: `record-${index + 1}`,
          study_id: "study-1",
          seq: index + 1,
          revision: index < 2 ? 1 : 2,
          kind: "observation",
          protocol_digest: "a".repeat(64),
          digest: "c".repeat(64),
          data: { value: index },
          recorded_at: "2026-11-13T12:00:00Z",
          observed_at: null,
          author_id: "researcher",
          run_references: [],
          artifact_references: [],
        })),
        next_after: 5,
        latest_seq: 5,
        has_more: false,
      };
    if (
      path.endsWith("/analysis-inputs") &&
      call.body.revision < 2 &&
      call.body.through_seq > 2
    )
      return new Response(
        '{"detail":"boundary omits a protocol revision referenced by its records"}',
        { status: 409, headers: { "content-type": "application/json" } },
      );
    return undefined;
  });
  render(
    <StudioContext.Provider value={f.runtime()}>
      <Studies />
    </StudioContext.Provider>,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "Open study Thermal study" }),
  );
  await screen.findByRole("button", { name: "Open protocol revision 1" });
  click("Open protocol revision 1");
  await screen.findByText(/Pinned protocol revision 1/);
  expect(screen.getByLabelText("Protocol revision for analysis")).toHaveValue(
    "2",
  );
  expect(screen.getByLabelText("Last included ledger sequence")).toHaveValue(
    "5",
  );
  expect(screen.getByText(/Viewed protocol revision 1/)).toBeVisible();
  click("Retain analysis snapshot");
  await screen.findByText(
    /Snapshot retained.*protocol revision 2, ledger sequence 5/,
  );
  expect(
    f.calls.find((call) => call.path.endsWith("/analysis-inputs"))!.body,
  ).toMatchObject({ revision: 2, through_seq: 5 });
  expect(screen.getByLabelText("Description")).toHaveValue("Protocol 1");
});

it("keeps a captured historical snapshot while allowing an explicit advance to a newer retained head", async () => {
  const f = fixture(),
    value = f.runtime();
  const ui = (revision: number, last_sequence: number) => (
    <StudioContext.Provider value={value}>
      <StudyAnalysisPanel
        study={{ ...study, revision: 1, last_sequence: 2 }}
        currentHead={{ revision, last_sequence }}
      />
    </StudioContext.Provider>
  );
  const view = render(ui(1, 2));
  edit("Analysis name", "Historical contrast");
  click("Retain analysis snapshot");
  await screen.findByText(
    /Snapshot retained.*protocol revision 1, ledger sequence 2/,
  );
  view.rerender(ui(2, 5));
  expect(screen.getByLabelText("Protocol revision for analysis")).toHaveValue(
    "1",
  );
  expect(screen.getByLabelText("Last included ledger sequence")).toHaveValue(
    "2",
  );
  expect(
    screen.getByText(
      /Snapshot retained.*protocol revision 1, ledger sequence 2/,
    ),
  ).toBeVisible();
  // A header reload can temporarily expose the older pinned envelope again.
  view.rerender(ui(1, 2));
  expect(
    screen.getByText(
      /Current retained boundary: protocol revision 2, ledger sequence 5/,
    ),
  ).toBeVisible();
  edit("Protocol revision for analysis", "2");
  expect(
    screen.getByRole("button", { name: "Retain analysis snapshot" }),
  ).toBeEnabled();
  click("Use current retained boundary");
  expect(screen.getByLabelText("Protocol revision for analysis")).toHaveValue(
    "2",
  );
  expect(screen.getByLabelText("Last included ledger sequence")).toHaveValue(
    "5",
  );
  expect(screen.queryByText(/Snapshot retained/)).toBeNull();
  expect(screen.getByLabelText("Analysis name")).toHaveValue(
    "Historical contrast",
  );
  click("Retain analysis snapshot");
  await screen.findByText(
    /Snapshot retained.*protocol revision 2, ledger sequence 5/,
  );
});

function report() {
  return {
    format: "tinystudio.study-analysis.v1",
    source,
    request: {
      method: "means.welch",
      confidence: 0.91,
      groups: ["control", "heated"],
    },
    method: {
      id: "means.welch",
      version: "1.0.0",
      label: "Welch independent means",
      assumptions: ["Researcher justifies independent units."],
      references: [],
    },
    result: {
      estimate: 2.25,
      counts: { group_a: 3, group_b: 4 },
      standard_error: 0.5,
      degrees_of_freedom: 4.3,
      interval: {
        confidence: 0.91,
        lower: 1.1,
        upper: null,
        lower_unbounded: false,
        upper_unbounded: true,
      },
      diagnostics: [
        {
          code: "overlap",
          message:
            "Unit identifiers overlap; independence requires researcher justification.",
        },
      ],
    },
    selection: {
      counts: {
        source_records: 12,
        selected_records: 8,
        used_records: 7,
        group_a: 3,
        group_b: 4,
      },
      diagnostics: [
        {
          code: "included_corrections",
          message:
            "Both original and correcting records were explicitly included.",
        },
      ],
    },
    qualification: { kind: "unqualified" },
  };
}
function resultRun(
  payload: unknown = report(),
  id = "result-1",
  checksum?: string,
): LabRun {
  return {
    id,
    definition_id: "analysis-1",
    state: "succeeded",
    result: {
      artifacts: [
        {
          id: `artifact-${id}`,
          name: "analysis.json",
          role: "statistical_analysis",
          sha256: checksum ?? hash(scientificJson.stringify(payload)),
        },
      ],
    },
  };
}
function artifactResponse(payload: unknown) {
  return new Response(scientificJson.stringify(payload), {
    headers: { "content-type": "application/json" },
  });
}

it("renders verified estimates, distinct unbounded/undefined interval limits, selection and provenance and links the completed result", async () => {
  vi.stubGlobal("crypto", webcrypto);
  const payload = report();
  const f = fixture((call) =>
      call.path.includes("/artifacts/") ? artifactResponse(payload) : undefined,
    ),
    linked = vi.fn();
  render(
    <StudioContext.Provider value={f.runtime()}>
      <StudyAnalysisResult run={resultRun(payload)} onResultLinked={linked} />
    </StudioContext.Provider>,
  );
  expect(await screen.findByText(/SHA-256 verified/)).toBeVisible();
  expect(screen.getByText("2.25")).toBeVisible();
  expect(screen.getByText(/Upper limit: Unbounded/)).toBeVisible();
  expect(
    screen.getByText(
      "Both original and correcting records were explicitly included.",
    ),
  ).toBeVisible();
  expect(
    screen.getByText(/Protocol revision 3.*ledger sequence 12/),
  ).toBeVisible();
  edit("Result reference notes", "Reviewed with protocol deviations.");
  click("Append completed result reference to study");
  await screen.findByText(/Result reference appended.*13/);
  expect(linked).toHaveBeenCalledOnce();
  expect(
    f.calls.find((call) => call.path.endsWith("/analysis-results"))!.body,
  ).toMatchObject({
    run_id: "result-1",
    notes: "Reviewed with protocol deviations.",
  });
});

it("shows an undefined interval with its retained diagnosis without fabricating limits", async () => {
  vi.stubGlobal("crypto", webcrypto);
  const payload = report();
  payload.result.interval.lower = null as any;
  payload.result.interval.upper_unbounded = false;
  payload.result.diagnostics = [
    {
      code: "zero_variance",
      message: "Variance is zero; the interval is undefined.",
    },
  ];
  const f = fixture((call) =>
    call.path.includes("/artifacts/") ? artifactResponse(payload) : undefined,
  );
  render(
    <StudioContext.Provider value={f.runtime()}>
      <StudyAnalysisResult run={resultRun(payload)} />
    </StudioContext.Provider>,
  );
  expect(await screen.findByText(/Lower limit: Undefined/)).toBeVisible();
  expect(screen.getByText(/Upper limit: Undefined/)).toBeVisible();
  expect(
    screen.getByText("Variance is zero; the interval is undefined."),
  ).toBeVisible();
});

it("rejects corrupt reports and masks a previous result immediately when ownership changes", async () => {
  vi.stubGlobal("crypto", webcrypto);
  let finish!: (value: unknown) => void;
  const pending = new Promise((resolve) => {
    finish = resolve;
  });
  const f = fixture((call) =>
    call.path.endsWith("artifact-late")
      ? pending
      : call.path.includes("/artifacts/")
        ? artifactResponse(report())
        : undefined,
  );
  const ui = (run: LabRun) => (
    <StudioContext.Provider value={f.runtime()}>
      <StudyAnalysisResult run={run} />
    </StudioContext.Provider>
  );
  const view = render(ui(resultRun(report())));
  await screen.findByText("2.25");
  view.rerender(ui(resultRun(report(), "late")));
  expect(screen.queryByText("2.25")).toBeNull();
  view.rerender(ui(resultRun(report(), "corrupt", "0".repeat(64))));
  await screen.findByText(/checksum mismatch/);
  await act(async () => finish(artifactResponse(report())));
  expect(screen.queryByText("2.25")).toBeNull();
  expect(
    screen.queryByRole("button", {
      name: "Append completed result reference to study",
    }),
  ).toBeNull();
});

it("labels custom retained outputs without imposing a builtin interval schema", async () => {
  vi.stubGlobal("crypto", webcrypto);
  const payload: any = report();
  payload.method = {
    id: "custom.python",
    name: "Cluster-aware model",
    version: "2.3",
    assumptions: [],
    references: [],
  };
  payload.request = { method: "custom.python" };
  payload.result = {
    custom_output: { estimate: -0, site_effect: 1e30 },
    counts: { selected_records: 8 },
  };
  const f = fixture((call) =>
    call.path.includes("/artifacts/") ? artifactResponse(payload) : undefined,
  );
  render(
    <StudioContext.Provider value={f.runtime()}>
      <StudyAnalysisResult run={resultRun(payload)} />
    </StudioContext.Provider>,
  );
  expect(
    await screen.findByText(/Researcher-supplied custom result/),
  ).toBeVisible();
  expect(
    screen.getByLabelText("Custom method retained output"),
  ).toHaveTextContent('"estimate": -0.0');
  expect(screen.queryByText(/Lower limit:/)).toBeNull();
});
