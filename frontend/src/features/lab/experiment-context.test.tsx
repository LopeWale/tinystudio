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
