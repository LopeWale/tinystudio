import { afterEach, expect, it, vi } from "vitest";
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
import { ComponentActionRegistry } from "../../controllers/componentActions";
import { ComponentActionsProvider } from "../assistant/actions";
import { LabPage } from "./index";

afterEach(cleanup);

function mount() {
  const api = {
    get: vi.fn(async (path: string) => {
      if (path === "/api/lab/catalog") return { adapters: [], methods: [] };
      if (path === "/api/lab/targets") return [{ id: "local", name: "Local" }];
      if (path === "/api/repositories") return [{ name: "engineering" }];
      return [];
    }),
    post: vi.fn(),
    put: vi.fn(),
    upload: vi.fn(),
  };
  const studio = {
    api,
    scopeKey: "user:alpha",
    project: "alpha",
    user: { id: "user" },
    route: "/lab",
    evidence: new EvidenceRegistry(),
    toast: vi.fn(),
  } as unknown as StudioRuntime;
  const registry = new ComponentActionRegistry(async () => true);
  registry.setScope({ user: "user", project: "alpha", route: "/lab" });
  render(
    <StudioContext.Provider value={studio}>
      <ComponentActionsProvider registry={registry}>
        <LabPage />
      </ComponentActionsProvider>
    </StudioContext.Provider>,
  );
  const action = () => {
    const capture = registry
      .inspect()
      .actions.find((item) => item.id === "lab.repository-capture");
    expect(capture).toBeDefined();
    return capture!;
  };
  return { api, registry, action };
}

async function selectRepository() {
  const user = userEvent.setup();
  fireEvent.click(screen.getByRole("tab", { name: "Inputs" }));
  await user.click(
    screen.getByText("Capture an existing repository", {
      selector: "summary span",
    }),
  );
  await user.click(
    await screen.findByRole("button", { name: /Repository to capture$/ }),
  );
  await user.click(await screen.findByRole("option", { name: "engineering" }));
  return screen.getByRole("button", { name: /Repository to capture$/ });
}

it("revokes capture through actual authoring and workspace tab transitions while retaining repository selection", async () => {
  const { api, registry, action } = mount();
  fireEvent.click(screen.getByRole("tab", { name: "Experiment" }));
  const selected = await selectRepository();
  await waitFor(() =>
    expect(action()).toMatchObject({
      disabled: false,
      state: { repository: "engineering" },
    }),
  );
  const ref = action().ref;

  fireEvent.click(screen.getByRole("tab", { name: "Parameters" }));
  expect(action().disabled).toBe(true);
  await expect(registry.execute(ref, undefined, true)).rejects.toThrow(
    "currently disabled",
  );
  fireEvent.click(screen.getByRole("tab", { name: "Inputs" }));
  expect(screen.getByRole("button", { name: /Repository to capture$/ })).toBe(
    selected,
  );
  expect(action()).toMatchObject({
    disabled: false,
    state: { repository: "engineering" },
  });

  fireEvent.click(screen.getByRole("tab", { name: "Runs" }));
  expect(action().disabled).toBe(true);
  await expect(registry.execute(ref, undefined, true)).rejects.toThrow(
    "currently disabled",
  );
  fireEvent.click(screen.getByRole("tab", { name: "Experiment" }));
  expect(screen.getByRole("button", { name: /Repository to capture$/ })).toBe(
    selected,
  );
  expect(action()).toMatchObject({
    ref,
    disabled: false,
    state: { repository: "engineering" },
  });
  expect(api.post).not.toHaveBeenCalled();
  expect(api.put).not.toHaveBeenCalled();
});

it("revokes capture when New experiment closes and restores the mounted selection on resume", async () => {
  const { api, registry, action } = mount();
  fireEvent.click(screen.getByRole("button", { name: "New experiment" }));
  const selected = await selectRepository();
  await waitFor(() => expect(action().disabled).toBe(false));
  const ref = action().ref;
  const fileInput = screen.getByLabelText("Import laboratory input", {
    selector: "input",
  });
  const file = new File(["fixture"], "retained-input.jsonl");
  fireEvent.change(fileInput, { target: { files: [file] } });

  fireEvent.click(screen.getByRole("button", { name: "Close dialog" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(action().disabled).toBe(true);
  await expect(registry.execute(ref, undefined, true)).rejects.toThrow(
    "currently disabled",
  );
  fireEvent.click(
    screen.getByRole("button", {
      name: /^(Open experiment|Resume draft|Edit experiment)$/,
    }),
  );
  expect(screen.getByRole("dialog")).toBeVisible();
  expect(screen.getByRole("button", { name: /Repository to capture$/ })).toBe(
    selected,
  );
  expect(action()).toMatchObject({
    ref,
    disabled: false,
    state: { repository: "engineering" },
  });
  expect(
    screen.getByLabelText("Import laboratory input", { selector: "input" }),
  ).toBe(fileInput);
  expect((fileInput as HTMLInputElement).files?.[0]).toBe(file);
  expect(api.post).not.toHaveBeenCalled();
  expect(api.put).not.toHaveBeenCalled();
  expect(api.upload).not.toHaveBeenCalled();
});
