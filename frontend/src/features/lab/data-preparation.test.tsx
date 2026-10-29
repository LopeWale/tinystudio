import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { StudioContext } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { LabPage } from "./index";
import { DataPreparation } from "./data-preparation";
import { makeDefinition, type ExperimentDefinition } from "./definition";

afterEach(cleanup);
function Harness({
  initial = makeDefinition("data.pipeline"),
}: {
  initial?: ExperimentDefinition;
}) {
  const [definition, setDefinition] = useState(initial);
  const [valid, setValid] = useState(true);
  return (
    <>
      <DataPreparation
        definition={definition}
        onChange={(patch) =>
          setDefinition((current) =>
            JSON.parse(JSON.stringify({ ...current, ...patch })),
          )
        }
        onValidityChange={setValid}
      />
      <output data-testid="definition">{JSON.stringify(definition)}</output>
      <button disabled={!valid}>Save definition</button>
      <textarea
        aria-label="External definition JSON"
        value={JSON.stringify(definition)}
        onChange={(event) => setDefinition(JSON.parse(event.target.value))}
      />
    </>
  );
}
const current = () =>
  JSON.parse(
    screen.getByTestId("definition").textContent!,
  ) as ExperimentDefinition;
const edit = (label: string, value: string) => {
  const field = screen.getByLabelText(label);
  expect(field).toBeVisible();
  fireEvent.change(field, { target: { value } });
};
async function select(label: string, name: string) {
  const user = userEvent.setup();
  await user.click(
    screen.getByRole("button", { name: new RegExp(`${label}$`) }),
  );
  await user.click(await screen.findByRole("option", { name }));
}
async function open(title: string) {
  await userEvent.click(screen.getByText(title, { selector: "summary span" }));
}

describe("recorded data preparation authoring", () => {
  it("starts in preparation only and patches source mapping without touching other axes", () => {
    const definition = makeDefinition("data.pipeline");
    definition.parameters.vendor = { retained: true };
    definition.inputs = [
      {
        artifact_id: "capture",
        sha256: "digest",
        role: "source",
        visibility: "evaluation",
      },
    ];
    definition.learning = {
      initialization: "scratch",
      objective: "causal_lm",
      update_strategy: "adamw",
    };
    render(<Harness initial={definition} />);
    expect(current().parameters).not.toHaveProperty("partition_policy");
    edit("time field", "event_seconds");
    edit("value field", "temperature");
    expect(current()).toMatchObject({
      parameters: {
        schema: { time: "event_seconds", value: "temperature" },
        vendor: { retained: true },
      },
      inputs: definition.inputs,
      learning: definition.learning,
    });
  });
  it("opts into grouped datasets and preserves a typed comma while authoring isolation fields", async () => {
    render(<Harness />);
    await open("Dataset construction");
    await select(
      "Output purpose",
      "Construct train / validation / test dataset",
    );
    expect(current().parameters.partition_policy).toEqual({
      enabled: true,
      strategy: "grouped",
      group_by: ["episode_id"],
      train: 0.6,
      validation: 0.2,
    });
    edit("Isolation fields (comma separated)", "episode_id,");
    expect(
      screen.getByLabelText("Isolation fields (comma separated)"),
    ).toHaveValue("episode_id,");
    edit("Isolation fields (comma separated)", "episode_id, device_id");
    expect(current().parameters.partition_policy).toMatchObject({
      group_by: ["episode_id", "device_id"],
    });
    await select("Output purpose", "Prepare evidence only");
    expect(current().parameters.partition_policy).toMatchObject({
      enabled: false,
      group_by: ["episode_id", "device_id"],
    });
  });
  it("authors qualified temporal coordinates and temporal-only purge policy", async () => {
    render(<Harness />);
    await open("Clock coordinates");
    for (const [label, value] of [
      ["clock domain", "utc"],
      ["clock units", "s"],
      ["clock origin", "unix"],
      ["clock session", "capture-session"],
      ["clock reset id", "segment-1"],
    ])
      edit(label, value);
    await open("Dataset construction");
    await select(
      "Output purpose",
      "Construct train / validation / test dataset",
    );
    await select(
      "Partition strategy",
      "Temporal · qualified comparable clocks",
    );
    edit("Temporal purge (seconds)", "2");
    await select("Temporal boundary action", "purge");
    expect(current().parameters).toMatchObject({
      clock_domain: "utc",
      clock_units: "s",
      clock_origin: "unix",
      clock_session: "capture-session",
      clock_reset_id: "segment-1",
      partition_policy: {
        strategy: "temporal",
        purge_seconds: 2,
        boundary_action: "purge",
      },
    });
    await select("Partition strategy", "Grouped · no cross-clock time claims");
    expect(current().parameters.partition_policy).not.toHaveProperty(
      "purge_seconds",
    );
  });
  it("adds, reorders and removes real stages while preserving unknown stage metadata and options", async () => {
    const definition = makeDefinition("data.pipeline");
    definition.pipeline = [
      {
        kind: "offset",
        version: "1",
        audit: { id: "retained" },
        parameters: { offset: 3, custom: { keep: true } },
      },
    ];
    render(<Harness initial={definition} />);
    await open("Ordered transformations");
    edit("Stage 1 offset", "7");
    await userEvent.click(
      screen.getByRole("button", { name: "Add transformation" }),
    );
    edit("Stage 2 factor", "2");
    await userEvent.click(
      screen.getByRole("button", { name: "Move stage 2 up" }),
    );
    expect(current().pipeline).toEqual([
      { kind: "scale", version: "1", parameters: { factor: 2 } },
      {
        kind: "offset",
        version: "1",
        audit: { id: "retained" },
        parameters: { offset: 7, custom: { keep: true } },
      },
    ]);
    await userEvent.click(
      screen.getByRole("button", { name: "Remove stage 1" }),
    );
    expect(current().pipeline).toEqual([
      {
        kind: "offset",
        version: "1",
        audit: { id: "retained" },
        parameters: { offset: 7, custom: { keep: true } },
      },
    ]);
  });
  it("keeps codec options when a representation kind changes", async () => {
    const definition = makeDefinition("data.pipeline");
    definition.parameters.representations = [
      {
        kind: "numeric_text",
        name: "comparison",
        max_tokens: 256,
        truncation: "prefix",
        vocabulary: ["psi"],
        bins: [0, 10],
      },
    ];
    render(<Harness initial={definition} />);
    await open("Matched representations");
    await select("Codec 1 kind", "engineering state");
    expect(current().parameters.representations).toEqual([
      {
        kind: "engineering_state",
        name: "comparison",
        max_tokens: 256,
        truncation: "prefix",
        vocabulary: ["psi"],
        bins: [0, 10],
      },
    ]);
  });
  it("retains invalid numeric and JSON intermediates, blocks saving, and recovers without losing configuration", async () => {
    const definition = makeDefinition("data.pipeline");
    definition.pipeline = [
      { kind: "scale", parameters: { factor: 2, custom: "retain" } },
    ];
    render(<Harness initial={definition} />);
    await open("Ordered transformations");
    edit("Stage 1 factor", "-");
    expect(screen.getByLabelText("Stage 1 factor")).toHaveValue("-");
    expect(
      screen.getByRole("button", { name: "Save definition" }),
    ).toBeDisabled();
    expect(current().pipeline).toEqual(definition.pipeline);
    edit("Stage 1 factor", "-3");
    expect(
      screen.getByRole("button", { name: "Save definition" }),
    ).toBeEnabled();
    await open("Stage 1 advanced options");
    edit("Stage 1 parameters JSON", '{"factor":');
    expect(screen.getByLabelText("Stage 1 parameters JSON")).toHaveValue(
      '{"factor":',
    );
    expect(
      screen.getByRole("button", { name: "Save definition" }),
    ).toBeDisabled();
    edit("Stage 1 parameters JSON", '{"factor":4,"custom":"retain"}');
    expect(current().pipeline).toEqual([
      { kind: "scale", parameters: { factor: 4, custom: "retain" } },
    ]);
    expect(
      screen.getByRole("button", { name: "Save definition" }),
    ).toBeEnabled();
  });
  it("reads external code-view changes and writes form changes into that same definition", async () => {
    render(<Harness />);
    await open("Ordered transformations");
    const fromCode = {
      ...current(),
      parameters: {
        schema: { time: "t" },
        cleaning: { duplicates: "last" },
        extra: { keep: true },
      },
      pipeline: [{ kind: "offset", parameters: { offset: 9 } }],
    };
    edit("External definition JSON", JSON.stringify(fromCode));
    expect(screen.getByLabelText("time field")).toHaveValue("t");
    expect(screen.getByLabelText("Stage 1 offset")).toHaveValue("9");
    edit("Stage 1 offset", "11");
    const code = JSON.parse(
      (screen.getByLabelText("External definition JSON") as HTMLTextAreaElement)
        .value,
    );
    expect(code).toMatchObject({
      parameters: fromCode.parameters,
      pipeline: [{ kind: "offset", parameters: { offset: 11 } }],
    });
  });
  it("rejects fractions that erase the test partition and allows recovery", async () => {
    render(<Harness />);
    await open("Dataset construction");
    await select(
      "Output purpose",
      "Construct train / validation / test dataset",
    );
    edit("Training fraction", ".8");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "leave a positive test fraction",
    );
    expect(
      screen.getByRole("button", { name: "Save definition" }),
    ).toBeDisabled();
    edit("Training fraction", ".5");
    expect(
      screen.getByRole("button", { name: "Save definition" }),
    ).toBeEnabled();
  });
});

it("wires invalid data edits into the actual Lab Save, preflight and run controls", async () => {
  const definition = makeDefinition("data.pipeline");
  definition.pipeline = [
    { kind: "scale", version: "1", parameters: { factor: 2 } },
  ];
  const saved = {
    id: "prepared-capture",
    name: "Prepared capture",
    revision: 1,
    definition,
  };
  const api = {
    get: vi.fn(async (path: string) => {
      if (path === "/api/lab/definitions") return [saved];
      if (path === "/api/lab/catalog")
        return { adapters: [], domains: [], methods: [] };
      if (path === "/api/lab/targets") return [{ id: "local", name: "Local" }];
      return [];
    }),
    post: vi.fn(),
    put: vi.fn(),
  };
  render(
    <StudioContext.Provider
      value={
        {
          api,
          scopeKey: "data-authoring",
          project: "alpha",
          user: { id: "user" },
          route: "/lab",
          evidence: new EvidenceRegistry(),
          toast: vi.fn(),
        } as any
      }
    >
      <LabPage />
    </StudioContext.Provider>,
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Open experiment" }),
  );
  await userEvent.click(
    await screen.findByRole("button", { name: /Saved experiment/ }),
  );
  await userEvent.click(
    await screen.findByRole("option", { name: "Prepared capture · r1" }),
  );
  expect(
    screen.getByRole("button", { name: "Run saved revision" }),
  ).toBeEnabled();
  await open("Ordered transformations");
  edit("Stage 1 factor", "-");
  const invalidFactor = screen.getByLabelText("Stage 1 factor");
  await userEvent.click(screen.getByRole("button", { name: "Close dialog" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  await userEvent.click(
    screen.getByRole("button", { name: "Edit experiment" }),
  );
  expect(screen.getByLabelText("Stage 1 factor")).toBe(invalidFactor);
  expect(invalidFactor).toHaveValue("-");
  for (const name of [
    "Save revision",
    "Check compatibility",
    "Run saved revision",
  ])
    expect(screen.getByRole("button", { name })).toBeDisabled();
  expect(api.post).not.toHaveBeenCalled();
  expect(api.put).not.toHaveBeenCalled();
  edit("Stage 1 factor", "2");
  expect(
    screen.getByRole("button", { name: "Run saved revision" }),
  ).toBeEnabled();
});

it("removing an invalid stage does not give its draft to an equal-valued surviving stage", async () => {
  const definition = makeDefinition("data.pipeline");
  definition.pipeline = [
    { kind: "scale", parameters: { factor: 1, custom: "delete" } },
    { kind: "scale", parameters: { factor: 1, custom: "survive" } },
  ];
  render(<Harness initial={definition} />);
  await open("Ordered transformations");
  edit("Stage 1 factor", "-");
  expect(
    screen.getByRole("button", { name: "Save definition" }),
  ).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Remove stage 1" }));
  expect(screen.getByLabelText("Stage 1 factor")).toHaveValue("1");
  expect(screen.getByRole("button", { name: "Save definition" })).toBeEnabled();
  expect(current().pipeline).toEqual([
    { kind: "scale", parameters: { factor: 1, custom: "survive" } },
  ]);
});

it("removing an earlier stage retains an equal-valued survivor's own invalid draft and gate", async () => {
  const definition = makeDefinition("data.pipeline");
  definition.pipeline = [
    { kind: "scale", parameters: { factor: 1, custom: "delete" } },
    { kind: "scale", parameters: { factor: 1, custom: "survive" } },
  ];
  render(<Harness initial={definition} />);
  await open("Ordered transformations");
  edit("Stage 2 factor", "-");
  await userEvent.click(screen.getByRole("button", { name: "Remove stage 1" }));
  expect(screen.getByLabelText("Stage 1 factor")).toHaveValue("-");
  expect(
    screen.getByRole("button", { name: "Save definition" }),
  ).toBeDisabled();
  expect(current().pipeline).toEqual([
    { kind: "scale", parameters: { factor: 1, custom: "survive" } },
  ]);
  edit("Stage 1 factor", "3");
  expect(screen.getByRole("button", { name: "Save definition" })).toBeEnabled();
  expect(current().pipeline).toEqual([
    { kind: "scale", parameters: { factor: 3, custom: "survive" } },
  ]);
});

it("moves equal-valued stages with their own invalid drafts through serialized parent updates", async () => {
  const definition = makeDefinition("data.pipeline");
  definition.pipeline = [
    { kind: "scale", parameters: { factor: 1, custom: "first" } },
    { kind: "scale", parameters: { factor: 1, custom: "second" } },
  ];
  render(<Harness initial={definition} />);
  await open("Ordered transformations");
  edit("Stage 2 factor", "-");
  edit("time field", "event_seconds");
  expect(screen.getByLabelText("Stage 2 factor")).toHaveValue("-");
  await userEvent.click(
    screen.getByRole("button", { name: "Move stage 2 up" }),
  );
  expect(screen.getByLabelText("Stage 1 factor")).toHaveValue("-");
  expect(screen.getByLabelText("Stage 2 factor")).toHaveValue("1");
  expect(
    screen.getByRole("button", { name: "Save definition" }),
  ).toBeDisabled();
  expect(current().pipeline).toEqual([
    { kind: "scale", parameters: { factor: 1, custom: "second" } },
    { kind: "scale", parameters: { factor: 1, custom: "first" } },
  ]);
  edit("Stage 1 factor", "4");
  expect(screen.getByRole("button", { name: "Save definition" })).toBeEnabled();
  expect(current().pipeline).toEqual([
    { kind: "scale", parameters: { factor: 4, custom: "second" } },
    { kind: "scale", parameters: { factor: 1, custom: "first" } },
  ]);
});

it("discards retired stage drafts and validation when code view replaces the pipeline", async () => {
  const definition = makeDefinition("data.pipeline");
  definition.pipeline = [
    { kind: "scale", parameters: { factor: 1, custom: "original" } },
  ];
  render(<Harness initial={definition} />);
  await open("Ordered transformations");
  edit("Stage 1 factor", "-");
  expect(
    screen.getByRole("button", { name: "Save definition" }),
  ).toBeDisabled();
  const replacement = {
    ...current(),
    pipeline: [
      { kind: "scale", parameters: { factor: 1, custom: "replacement" } },
    ],
  };
  edit("External definition JSON", JSON.stringify(replacement));
  expect(screen.getByLabelText("Stage 1 factor")).toHaveValue("1");
  expect(screen.getByRole("button", { name: "Save definition" })).toBeEnabled();
  expect(current()).toEqual(replacement);
});

it("initially shows source mapping and keeps secondary preparation modules compact", async () => {
  const definition = makeDefinition("data.pipeline");
  definition.pipeline = [{ kind: "scale", parameters: { factor: 1 } }];
  render(<Harness initial={definition} />);
  const disclosure = (title: string) =>
    screen.getByText(title, { selector: "summary span" }).closest("details")!;
  expect(disclosure("Source field mapping")).toHaveAttribute("open");
  expect(screen.getByLabelText("time field")).toBeVisible();
  for (const title of [
    "Clock coordinates",
    "Cleaning and replay",
    "Ordered transformations",
    "Dataset construction",
    "Matched representations",
    "Annotations and advanced acquisition",
  ]) {
    expect(disclosure(title)).not.toHaveAttribute("open");
    expect(screen.getByText(title, { selector: "summary span" })).toBeVisible();
  }
  expect(screen.getByLabelText("Stage 1 factor")).not.toBeVisible();
  await open("Ordered transformations");
  edit("Stage 1 factor", "-");
  await open("Ordered transformations");
  expect(screen.getByLabelText("Stage 1 factor")).not.toBeVisible();
  expect(
    screen.getByRole("button", { name: "Save definition" }),
  ).toBeDisabled();
  await open("Ordered transformations");
  expect(screen.getByLabelText("Stage 1 factor")).toHaveValue("-");
  expect(screen.getByLabelText("Stage 1 factor")).toBeVisible();
});
