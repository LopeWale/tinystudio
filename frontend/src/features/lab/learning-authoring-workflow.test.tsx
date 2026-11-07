import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StudioContext, type StudioRuntime } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { LabPage } from "./index";
import { makeDefinition, type SavedDefinition } from "./definition";

beforeEach(() =>
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  ),
);
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function workspace(initialDefinitions: SavedDefinition[] = []) {
  let saved: SavedDefinition | undefined;
  const api = {
    get: vi.fn(async (path: string): Promise<unknown> => {
      if (path === "/api/lab/definitions")
        return saved ? [saved] : initialDefinitions;
      if (path === "/api/lab/catalog") return { adapters: [], methods: [] };
      if (path === "/api/lab/targets")
        return [{ id: "local", name: "Local computer" }];
      if (path === "/api/lab/inputs")
        return [
          {
            id: "numeric-source",
            name: "numeric-records.jsonl",
            sha256: "a".repeat(64),
            media_type: "application/jsonl",
            size: 512,
          },
        ];
      if (path === "/api/lab/runs/submitted")
        return { id: "submitted", state: "succeeded", definition_id: "saved" };
      if (path.includes("/events?")) return { events: [], next_after: 0 };
      if (path.includes("/records?")) return { records: [] };
      return [];
    }),
    post: vi.fn(async (path: string, body: any): Promise<unknown> => {
      if (path === "/api/lab/definitions") {
        saved = {
          id: "saved",
          revision: 1,
          name: body.definition.name,
          definition: structuredClone(body.definition),
        };
        return saved;
      }
      if (path === "/api/lab/preflight")
        return { compatible: true, checks: [] };
      if (path === "/api/lab/runs") return { id: "submitted", state: "queued" };
      throw new Error("Unexpected mutation: " + path);
    }),
    put: vi.fn(),
  };
  const studio = {
    api,
    scopeKey: "learning-authoring",
    project: "learning-authoring",
    user: { id: "user" },
    route: "/lab",
    evidence: new EvidenceRegistry(),
    toast: vi.fn(),
  } as unknown as StudioRuntime;
  render(
    <StudioContext.Provider value={studio}>
      <LabPage />
    </StudioContext.Provider>,
  );
  const user = userEvent.setup();
  const choose = async (label: string, option: string) => {
    await user.click(
      screen.getByRole("button", { name: new RegExp(label + "$") }),
    );
    await user.click(await screen.findByRole("option", { name: option }));
  };
  const edit = (label: string, value: string) =>
    fireEvent.change(screen.getByRole("textbox", { name: label }), {
      target: { value },
    });
  return { api, user, choose, edit, saved: () => saved! };
}

it("authors control settings, saves, checks the selected runtime and submits the saved revision without editing JSON", async () => {
  const { api, user, choose, edit, saved } = workspace();
  await user.click(screen.getByRole("button", { name: "New experiment" }));
  await choose("Experiment engine", "Control policy learning");
  await choose("Environment runtime", "TorchRL EnvBase");
  edit("Target position", "-0.25");
  edit("Episode horizon", "12");
  edit("Agent count", "2");
  edit("Distance reward weight", "0");
  edit("Action cost", "0.125");
  edit("Evaluation seeds JSON", "[11, 12]");
  await user.click(screen.getByRole("button", { name: "Save experiment" }));
  await screen.findByRole("button", { name: "Save revision" });
  expect(saved().definition).toMatchObject({
    engine: "learning.control_rl",
    learning: { objective: "ppo" },
    parameters: {
      environment: { kind: "control", runtime: "torchrl" },
      target: -0.25,
      horizon: 12,
      agents: 2,
      reward: { distance_weight: 0, action_cost: 0.125 },
      evaluation: { seeds: [11, 12] },
    },
  });
  await user.click(screen.getByRole("button", { name: "Check compatibility" }));
  await waitFor(() =>
    expect(api.post).toHaveBeenCalledWith(
      "/api/lab/preflight",
      { definition: saved().definition, target_id: "local" },
      expect.anything(),
    ),
  );
  await user.click(screen.getByRole("button", { name: "Run saved revision" }));
  await waitFor(() =>
    expect(api.post).toHaveBeenCalledWith(
      "/api/lab/runs",
      expect.objectContaining({
        definition_id: "saved",
        revision: 1,
        target_id: "local",
      }),
      expect.anything(),
    ),
  );
});

it.each(
  [
    ["Control policy learning", "Policy training steps"],
    ["Time-series learning", "Training steps"],
    ["Multimodal learning", "Training steps"],
  ].flatMap(([engine, label]) =>
    ["edit", "tab", "configure"].map((entry) => ({ engine, label, entry })),
  ),
)(
  "keeps an incomplete $engine field through dismissal and $entry reopening, blocking stale-revision submission",
  async ({ engine, label, entry }) => {
    const { api, user, choose, edit } = workspace();
    await user.click(screen.getByRole("button", { name: "New experiment" }));
    await choose("Experiment engine", engine);
    await user.click(screen.getByRole("button", { name: "Save experiment" }));
    await screen.findByRole("button", { name: "Save revision" });
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Run saved revision" }),
      ).toBeEnabled(),
    );
    edit(label, "");
    for (const name of [
      "Save revision",
      "Check compatibility",
      "Run saved revision",
    ])
      expect(screen.getByRole("button", { name })).toBeDisabled();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    if (entry === "tab")
      fireEvent.click(screen.getByRole("tab", { name: "Experiment" }));
    else
      fireEvent.click(
        screen.getByRole("button", {
          name:
            entry === "configure" ? "Configure experiment" : "Edit experiment",
        }),
      );
    expect(screen.getByRole("textbox", { name: label })).toHaveValue("");
    expect(
      screen.getByRole("button", { name: "Save revision" }),
    ).toBeDisabled();
    edit(label, "9");
    expect(screen.getByRole("button", { name: "Save revision" })).toBeEnabled();
    expect(
      screen.getByRole("button", { name: "Run saved revision" }),
    ).toBeDisabled();
    expect(api.post.mock.calls.map(([path]) => path)).toEqual([
      "/api/lab/definitions",
    ]);
  },
);

it.each(["forecast", "classification"])(
  "authors a %s model, attaches immutable evidence and submits its saved revision through the application",
  async (kind) => {
    const { api, user, choose, edit, saved } = workspace();
    await user.click(screen.getByRole("button", { name: "New experiment" }));
    await choose(
      "Experiment engine",
      kind === "forecast" ? "Time-series learning" : "Multimodal learning",
    );
    if (kind === "forecast") {
      await choose("Model family", "transformer");
      edit("Hidden size", "8");
      edit("Attention heads", "4");
      edit("Context length", "4");
      edit("Forecast horizon", "2");
      edit("Target feature index", "1");
    } else {
      await choose("Learning objective", "classification");
      edit("Target key", "outcome");
    }
    edit("Training steps", "7");
    screen.getByRole("tab", { name: "Inputs" }).focus();
    await user.keyboard("{Enter}");
    await user.click(
      await screen.findByRole("button", {
        name: "Attach numeric-records.jsonl",
      }),
    );
    await user.click(screen.getByRole("button", { name: "Save experiment" }));
    await screen.findByRole("button", { name: "Save revision" });
    expect(saved().definition.inputs).toEqual([
      {
        artifact_id: "numeric-source",
        sha256: "a".repeat(64),
        role: "source",
        visibility: "model",
      },
    ]);
    expect(saved().definition.parameters.steps).toBe(7);
    if (kind === "forecast") {
      expect(saved().definition.learning).toMatchObject({
        family: "transformer",
        architecture: { hidden_size: 8, heads: 4, context: 4 },
      });
      expect(saved().definition.parameters).toMatchObject({
        horizon: 2,
        target_feature: 1,
      });
    } else {
      expect(saved().definition.learning).toMatchObject({
        objective: "classification",
      });
      expect(saved().definition.parameters.target_key).toBe("outcome");
    }
    await user.click(
      screen.getByRole("button", { name: "Check compatibility" }),
    );
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        "/api/lab/preflight",
        { definition: saved().definition, target_id: "local" },
        expect.anything(),
      ),
    );
    await user.click(
      screen.getByRole("button", { name: "Run saved revision" }),
    );
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        "/api/lab/runs",
        expect.objectContaining({
          definition_id: "saved",
          revision: 1,
          target_id: "local",
        }),
        expect.anything(),
      ),
    );
  },
);

it.each([
  {
    engine: "Autoregressive language PPO / GRPO",
    field: "tasks",
    value: [{ operation: "add", left: -2.5, right: 3 }],
  },
  {
    engine: "Lexical / semantic / hybrid retrieval",
    field: "queries",
    value: ["pressure", "voltage"],
  },
])(
  "preserves incomplete $field JSON and submits only explicitly saved settings",
  async ({ engine, field, value }) => {
    const { api, user, choose, edit, saved } = workspace();
    await user.click(screen.getByRole("button", { name: "New experiment" }));
    await choose("Experiment engine", engine);
    await user.click(screen.getByRole("button", { name: "Save experiment" }));
    await screen.findByRole("button", { name: "Save revision" });
    edit(field, "[");
    expect(screen.getByRole("textbox", { name: field })).toHaveValue("[");
    for (const name of [
      "Save revision",
      "Check compatibility",
      "Run saved revision",
    ])
      expect(screen.getByRole("button", { name })).toBeDisabled();
    await user.keyboard("{Escape}");
    fireEvent.click(screen.getByRole("tab", { name: "Experiment" }));
    expect(screen.getByRole("textbox", { name: field })).toHaveValue("[");
    edit(field, JSON.stringify(value));
    expect(screen.getByRole("button", { name: "Save revision" })).toBeEnabled();
    expect(
      screen.getByRole("button", { name: "Run saved revision" }),
    ).toBeDisabled();
    expect(saved().definition.parameters[field]).not.toEqual(value);
    expect(api.post.mock.calls.map(([path]) => path)).toEqual([
      "/api/lab/definitions",
    ]);
  },
);

it("discards an incomplete generic field when explicitly opening a different saved definition with the same values", async () => {
  const definitions = ["First queries", "Second queries"].map(
    (name, index) => ({
      id: `queries-${index}`,
      name,
      revision: 1,
      definition: { ...makeDefinition("retrieval.index"), name },
    }),
  );
  const { user, choose, edit, api } = workspace(definitions);
  await user.click(
    screen.getByRole("button", { name: "Configure experiment" }),
  );
  await choose("Saved experiment", "First queries · r1");
  edit("queries", "[");
  await choose("Saved experiment", "Second queries · r1");
  await user.click(
    screen.getByRole("button", { name: "Discard edits and open" }),
  );
  expect(screen.getByRole("textbox", { name: "Experiment name" })).toHaveValue(
    "Second queries",
  );
  expect(screen.getByRole("textbox", { name: "queries" })).toHaveValue(
    JSON.stringify(["pressure"], null, 2),
  );
  expect(screen.getByRole("button", { name: "Save revision" })).toBeEnabled();
  expect(api.post).not.toHaveBeenCalled();
});

it("shows demonstrations as required in Inputs after choosing offline DQN from a retained object selector", async () => {
  const definition = makeDefinition("learning.control_rl");
  definition.learning = { ...definition.learning, objective: { kind: "ppo" } };
  const { user, choose } = workspace([
    {
      id: "object-policy",
      name: definition.name,
      revision: 1,
      definition,
    },
  ]);
  await user.click(
    screen.getByRole("button", { name: "Configure experiment" }),
  );
  await choose("Saved experiment", `${definition.name} · r1`);
  await choose("Control objective", "Offline DQN · retained transitions");
  screen.getByRole("tab", { name: "Inputs" }).focus();
  await user.keyboard("{Enter}");
  expect(
    screen.getByText("demonstrations", { selector: "code" }).closest("li"),
  ).toHaveTextContent("Required");
  expect(screen.getByRole("button", { name: "Save revision" })).toBeEnabled();
});
