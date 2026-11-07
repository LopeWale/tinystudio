import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StudioContext, type StudioRuntime } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { LabPage } from "./index";
import { makeDefinition, type SavedDefinition } from "./definition";

afterEach(cleanup);

function mount() {
  const saved: SavedDefinition = {
    id: "saved-signal",
    name: "Retained voltage experiment",
    revision: 3,
    definition: {
      ...makeDefinition(),
      name: "Retained voltage experiment",
    },
  };
  const api = {
    get: vi.fn(async (path: string) => {
      if (path === "/api/lab/definitions") return [saved];
      if (path === "/api/lab/catalog") return { adapters: [], methods: [] };
      if (path === "/api/lab/targets") return [{ id: "local", name: "Local" }];
      return [];
    }),
    post: vi.fn(),
    put: vi.fn(),
  };
  const studio = {
    api,
    scopeKey: "experiment-navigation",
    project: "experiment-navigation",
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
  return { api, saved };
}

it("opens Configure experiment in the Experiment page and loads a saved definition inline", async () => {
  const { api, saved } = mount();
  const user = userEvent.setup();
  expect(screen.getByRole("tab", { name: "Runs" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(screen.getByRole("tab", { name: "Experiment" })).toHaveAttribute(
    "aria-selected",
    "false",
  );
  expect(screen.getByRole("tab", { name: "Capabilities" })).toBeVisible();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

  await user.click(
    screen.getByRole("button", { name: "Configure experiment" }),
  );

  expect(screen.getByRole("tab", { name: "Experiment" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  const workspace = screen.getByRole("tabpanel", {
    name: "Experiment",
  });
  expect(workspace).toBeVisible();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  await user.click(
    within(workspace).getByRole("button", { name: /Saved experiment$/ }),
  );
  await user.click(
    await screen.findByRole("option", {
      name: `${saved.name} · r${saved.revision}`,
    }),
  );

  expect(
    within(workspace).getByRole("textbox", { name: "Experiment name" }),
  ).toHaveValue(saved.name);
  const name = within(workspace).getByRole("textbox", {
    name: "Experiment name",
  });
  await user.clear(name);
  expect(name).toBeVisible();
  expect(screen.getByRole("button", { name: "Save revision" })).toBeDisabled();
  await user.type(name, "Retained unsaved name");
  await user.click(screen.getByRole("tab", { name: /^Runs$/ }));
  expect(screen.getByRole("heading", { name: "Laboratory" })).toBeVisible();
  expect(
    screen.getByText(
      "Author portable experiments, execute a saved revision, and inspect retained evidence.",
    ),
  ).toBeVisible();
  await user.click(screen.getByRole("tab", { name: /^Experiment$/ }));
  expect(screen.getByRole("textbox", { name: "Experiment name" })).toHaveValue(
    "Retained unsaved name",
  );
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(api.post).not.toHaveBeenCalled();
  expect(api.put).not.toHaveBeenCalled();
});

it("keeps New experiment in a dialog with a fresh editor and no saved experiment picker", async () => {
  mount();
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "New experiment" }));

  const dialog = screen.getByRole("dialog");
  expect(dialog).toBeVisible();
  expect(
    within(dialog).getByRole("textbox", { name: "Experiment name" }),
  ).toHaveValue("Synthetic signal");
  expect(
    within(dialog).getByRole("button", { name: "Save experiment" }),
  ).toBeEnabled();
  expect(
    within(dialog).queryByRole("button", { name: /Saved experiment$/ }),
  ).not.toBeInTheDocument();
  expect(
    within(dialog).queryByText("Saved experiment", { exact: true }),
  ).not.toBeInTheDocument();
});

it("lets the user keep an unsaved definition before opening a saved experiment", async () => {
  const { api, saved } = mount();
  const user = userEvent.setup();
  await user.click(screen.getByRole("tab", { name: "Experiment" }));
  const workspace = within(
    screen.getByRole("tabpanel", { name: "Experiment" }),
  );
  const name = workspace.getByRole("textbox", { name: "Experiment name" });
  await user.clear(name);
  await user.type(name, "Bench calibration");
  const chooseSaved = async () => {
    await user.click(
      workspace.getByRole("button", { name: /Saved experiment$/ }),
    );
    await user.click(
      await screen.findByRole("option", {
        name: `${saved.name} · r${saved.revision}`,
      }),
    );
  };
  await chooseSaved();
  await user.click(workspace.getByRole("button", { name: "Keep editing" }));
  expect(name).toHaveValue("Bench calibration");
  await chooseSaved();
  await user.click(
    workspace.getByRole("button", { name: "Discard edits and open" }),
  );
  expect(
    workspace.getByRole("textbox", { name: "Experiment name" }),
  ).toHaveValue(saved.name);
  expect(api.post).not.toHaveBeenCalled();
  expect(api.put).not.toHaveBeenCalled();
});
