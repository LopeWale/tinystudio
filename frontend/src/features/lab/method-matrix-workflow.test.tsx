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
import type { ExperimentDefinition } from "./definition";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
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

it("authors, saves, preflights and submits a method matrix through the actual Laboratory dialog", async () => {
  let saved:
    | {
        id: string;
        revision: number;
        name: string;
        definition: ExperimentDefinition;
      }
    | undefined;
  const artifact = {
    id: "cases",
    name: "heldout-cases.json",
    sha256: "a".repeat(64),
    size: 512,
    media_type: "application/json",
  };
  const api = {
    get: vi.fn(async (path: string): Promise<unknown> => {
      if (path === "/api/lab/definitions") return saved ? [saved] : [];
      if (path === "/api/lab/inputs") return [artifact];
      if (path === "/api/lab/catalog") return { adapters: [], methods: [] };
      if (path === "/api/lab/targets")
        return [{ id: "local", name: "Local computer" }];
      if (path === "/api/lab/runs/run")
        return { id: "run", state: "succeeded", definition_id: "saved" };
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
          definition: JSON.parse(JSON.stringify(body.definition)),
        };
        return saved;
      }
      if (path === "/api/lab/preflight")
        return { compatible: true, checks: [] };
      if (path === "/api/lab/runs") return { id: "run", state: "queued" };
      throw new Error("Unexpected mutation: " + path);
    }),
    put: vi.fn(),
  };
  const runtime = {
    api,
    scopeKey: "matrix-workspace",
    project: "matrix-workspace",
    user: { id: "user" },
    route: "/lab",
    evidence: new EvidenceRegistry(),
    toast: vi.fn(),
  } as unknown as StudioRuntime;
  render(
    <StudioContext.Provider value={runtime}>
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
  await user.click(screen.getByRole("button", { name: "New experiment" }));
  await choose(
    "Experiment engine",
    "Independent evaluation / paired comparison",
  );
  await choose("Evaluation operation", "Compare methods on shared cases");
  for (const [label, intercept] of [
    ["Physical formula", "0.25000000000000000001"],
    ["Alternative", "1"],
  ]) {
    await user.click(
      screen.getByRole("button", { name: "Add conventional formula" }),
    );
    fireEvent.change(screen.getByRole("textbox", { name: "Method label" }), {
      target: { value: label },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Intercept" }), {
      target: { value: intercept },
    });
    await user.click(screen.getByRole("button", { name: "Add feature" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Feature 1 name" }), {
      target: { value: "reading" },
    });
    fireEvent.change(
      screen.getByRole("textbox", { name: "Feature 1 coefficient" }),
      { target: { value: "-2" } },
    );
  }
  await choose("Comparison baseline", "Physical formula");
  // jsdom has no pointer geometry for the segmented track; use its real keyboard activation.
  screen.getByRole("tab", { name: "Inputs" }).focus();
  await user.keyboard("{Enter}");
  await choose("Input role", "cases");
  await user.click(
    await screen.findByRole("button", { name: "Attach heldout-cases.json" }),
  );
  screen.getByRole("tab", { name: "Execution" }).focus();
  await user.keyboard("{Enter}");
  await choose("Execution target", "Local computer");
  await user.click(screen.getByRole("button", { name: "Save experiment" }));
  await screen.findByRole("button", { name: "Save revision" });
  expect(saved!.definition.parameters).toEqual({
    operation: "matrix",
    max_new_tokens: 16,
    device: "cpu",
    baseline_method: "affine-1",
    methods: [
      {
        id: "affine-1",
        label: "Physical formula",
        kind: "affine",
        coefficients: { reading: "-2" },
        intercept: "0.25000000000000000001",
      },
      {
        id: "affine-2",
        label: "Alternative",
        kind: "affine",
        coefficients: { reading: "-2" },
        intercept: "1",
      },
    ],
  });
  expect(saved!.definition.inputs).toEqual([
    {
      artifact_id: "cases",
      sha256: artifact.sha256,
      role: "cases",
      visibility: "evaluation",
    },
  ]);
  await user.click(screen.getByRole("button", { name: "Check compatibility" }));
  await waitFor(() =>
    expect(api.post).toHaveBeenCalledWith(
      "/api/lab/preflight",
      expect.objectContaining({
        definition: saved!.definition,
        target_id: "local",
      }),
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
