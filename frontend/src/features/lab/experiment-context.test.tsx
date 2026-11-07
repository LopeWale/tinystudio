import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StudioContext, type StudioRuntime } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { LabPage } from "./index";
import { makeDefinition } from "./definition";

afterEach(cleanup);

// DESIGN.md, Context and execution contracts: changing definition identity
// must never silently discard edits. Similar names cannot identify a context.
it("preserves an edited experiment until the user resolves a requested switch", async () => {
  const rows = ["first", "second"].map((id) => ({
    id,
    name: `Signal ${id}`,
    revision: 1,
    definition: { ...makeDefinition(), name: `Signal ${id}` },
  }));
  const api = {
    get: vi.fn(async (path: string) => {
      if (path === "/api/lab/definitions") return rows;
      if (path === "/api/lab/catalog") return { adapters: [], methods: [] };
      if (path === "/api/lab/targets") return [{ id: "local", name: "Local" }];
      return [];
    }),
    post: vi.fn(),
    put: vi.fn(),
  };
  const studio = {
    api,
    scopeKey: "context-review",
    project: "review",
    user: { id: "reviewer" },
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
  await user.click(screen.getByRole("button", { name: "Open experiment" }));
  await user.click(screen.getByRole("button", { name: /Saved experiment$/ }));
  await user.click(
    await screen.findByRole("option", { name: "Signal first · r1" }),
  );
  fireEvent.change(screen.getByLabelText("Experiment name"), {
    target: { value: "Keep this unsaved work" },
  });
  await user.click(screen.getByRole("button", { name: /Saved experiment$/ }));
  await user.click(
    await screen.findByRole("option", { name: "Signal second · r1" }),
  );
  // Assert the retained data itself, without prescribing modal copy or styling.
  expect(screen.getByLabelText("Experiment name")).toHaveValue(
    "Keep this unsaved work",
  );
  expect(api.put).not.toHaveBeenCalled();
  expect(api.post).not.toHaveBeenCalled();
});

it("requires an inline choice before New experiment replaces existing edits", async () => {
  const row = {
    id: "existing",
    name: "Existing experiment",
    revision: 1,
    definition: { ...makeDefinition(), name: "Existing experiment" },
  };
  const api = {
    get: vi.fn(async (path: string) => {
      if (path === "/api/lab/definitions") return [row];
      if (path === "/api/lab/catalog") return { adapters: [], methods: [] };
      if (path === "/api/lab/targets") return [{ id: "local", name: "Local" }];
      return [];
    }),
    post: vi.fn(),
    put: vi.fn(),
  };
  const studio = {
    api,
    scopeKey: "new-context-review",
    project: "review",
    user: { id: "reviewer" },
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
  await user.click(screen.getByRole("button", { name: "Open experiment" }));
  await user.click(screen.getByRole("button", { name: /Saved experiment$/ }));
  await user.click(
    await screen.findByRole("option", { name: "Existing experiment · r1" }),
  );
  fireEvent.change(screen.getByLabelText("Experiment name"), {
    target: { value: "Keep these edits" },
  });
  await user.click(screen.getByRole("button", { name: "New experiment" }));
  expect(screen.getByLabelText("Experiment name")).toHaveValue(
    "Keep these edits",
  );
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Keep editing" }));
  expect(screen.getByLabelText("Experiment name")).toHaveValue(
    "Keep these edits",
  );
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "New experiment" }));
  await user.click(
    screen.getByRole("button", { name: "Discard edits and create new" }),
  );
  expect(
    screen.getByRole("dialog", { name: "New experiment" }),
  ).toBeInTheDocument();
  expect(screen.getByLabelText("Experiment name")).toHaveValue(
    makeDefinition().name,
  );
  expect(api.put).not.toHaveBeenCalled();
  expect(api.post).not.toHaveBeenCalled();
});

it.each([
  ["data.pipeline", "saved"],
  ["data.pipeline", "new"],
  ["learning.language", "saved"],
  ["learning.language", "new"],
  ["file", "saved"],
  ["file", "new"],
])(
  "preserves child-owned %s work before a %s replacement",
  async (kind, destination) => {
    const definition = makeDefinition(kind === "file" ? undefined : kind);
    if (kind === "data.pipeline")
      definition.pipeline = [
        { kind: "scale", version: "1", parameters: { factor: 2 } },
      ];
    const rows = [
      { id: "first", name: "Original", revision: 1, definition },
      {
        id: "second",
        name: "Replacement",
        revision: 1,
        definition: { ...makeDefinition(), name: "Replacement" },
      },
    ];
    const api = {
      get: vi.fn(async (path: string) => {
        if (path === "/api/lab/definitions") return rows;
        if (path === "/api/lab/catalog") return { adapters: [], methods: [] };
        if (path === "/api/lab/targets")
          return [{ id: "local", name: "Local" }];
        return [];
      }),
      post: vi.fn(),
      put: vi.fn(),
      upload: vi.fn(),
    };
    render(
      <StudioContext.Provider
        value={
          {
            api,
            scopeKey: `child-${kind}-${destination}`,
            project: "review",
            user: { id: "reviewer" },
            route: "/lab",
            evidence: new EvidenceRegistry(),
            toast: vi.fn(),
          } as unknown as StudioRuntime
        }
      >
        <LabPage />
      </StudioContext.Provider>,
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Open experiment" }));
    await user.click(screen.getByRole("button", { name: /Saved experiment$/ }));
    await user.click(
      await screen.findByRole("option", { name: "Original · r1" }),
    );
    const originalJson = (
      screen.getByLabelText("Experiment definition JSON") as HTMLTextAreaElement
    ).value;
    let field: HTMLInputElement;
    const file = new File(["retained"], "pending.jsonl");
    if (kind === "file") {
      fireEvent.click(screen.getByRole("tab", { name: "Inputs" }));
      field = screen.getByLabelText("Import laboratory input", {
        selector: "input",
      }) as HTMLInputElement;
      fireEvent.change(field, { target: { files: [file] } });
    } else {
      if (kind === "data.pipeline")
        await user.click(
          screen.getByText("Ordered transformations", {
            selector: "summary span",
          }),
        );
      field = screen.getByLabelText(
        kind === "data.pipeline" ? "Stage 1 factor" : "Hidden size",
      ) as HTMLInputElement;
      fireEvent.change(field, { target: { value: "-" } });
      expect(
        screen.getByRole("button", { name: "Save revision" }),
      ).toBeDisabled();
    }
    expect(screen.getByLabelText("Experiment definition JSON")).toHaveValue(
      originalJson,
    );
    const requestReplacement = async () => {
      if (destination === "new")
        await user.click(
          screen.getByRole("button", { name: "New experiment" }),
        );
      else {
        await user.click(
          screen.getByRole("button", { name: /Saved experiment$/ }),
        );
        await user.click(
          await screen.findByRole("option", { name: "Replacement · r1" }),
        );
      }
    };
    await requestReplacement();
    await user.click(screen.getByRole("button", { name: "Keep editing" }));
    expect(
      screen.getByLabelText(
        kind === "file"
          ? "Import laboratory input"
          : kind === "data.pipeline"
            ? "Stage 1 factor"
            : "Hidden size",
        kind === "file" ? { selector: "input" } : undefined,
      ),
    ).toBe(field);
    if (kind === "file") expect(field.files?.[0]).toBe(file);
    else expect(field).toHaveValue("-");
    await requestReplacement();
    await user.click(
      screen.getByRole("button", {
        name:
          destination === "new"
            ? "Discard edits and create new"
            : "Discard edits and open",
      }),
    );
    expect(screen.getByLabelText("Experiment name")).toHaveValue(
      destination === "new" ? makeDefinition().name : "Replacement",
    );
    expect(api.post).not.toHaveBeenCalled();
    expect(api.put).not.toHaveBeenCalled();
    expect(api.upload).not.toHaveBeenCalled();
  },
);
