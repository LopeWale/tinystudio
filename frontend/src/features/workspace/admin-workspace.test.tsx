import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { StudioContext } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { SettingsPage } from "./settings";
import { ProjectsPage } from "./projects";
import { UsersPage } from "./users";
import { KeysPage } from "./keys";
beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const specs = [
  {
    key: "KEY",
    label: "Provider key",
    group: "openai",
    secret: true,
    set: true,
    source: "database",
  },
  {
    key: "MODEL",
    label: "Helper model",
    group: "helper",
    secret: false,
    set: true,
    value: "original",
    source: "database",
  },
];
const cfg = {
  inference: { mode: "local" },
  device: "cpu",
  dtype: "fp32",
  data_dir: "/data",
};
function context(api: any) {
  return {
    api,
    scopeKey: "user:project",
    project: "project",
    user: { id: "self", email: "admin@local", role: "admin" },
    route: "/settings",
    evidence: new EvidenceRegistry(),
    refreshOverview: vi.fn(),
    refreshProjects: vi.fn(),
    selectProject: vi.fn(),
    navigate: vi.fn(),
    toast: vi.fn(),
    confirm: vi.fn(async () => true),
  } as any;
}
function mount(child: React.ReactNode, s: any) {
  return render(
    <StudioContext.Provider value={s}>
      <MemoryRouter>{child}</MemoryRouter>
    </StudioContext.Provider>,
  );
}
describe("administration workspaces", () => {
  it("shows one provider form, retains edits across sections, and resets the full draft", async () => {
    const api = {
      get: vi.fn(async (path: string) =>
        path === "/api/settings" ? { settings: specs } : cfg,
      ),
    };
    mount(<SettingsPage />, context(api));
    const key = await screen.findByLabelText("Provider key", {
      selector: "input",
    });
    expect(
      screen.queryByLabelText("Helper model", { selector: "input" }),
    ).toBeNull();
    fireEvent.change(key, { target: { value: "temporary-secret" } });
    fireEvent.click(screen.getByRole("button", { name: /Helper LLM/ }));
    expect(
      screen.queryByLabelText("Provider key", { selector: "input" }),
    ).toBeNull();
    fireEvent.change(
      screen.getByLabelText("Helper model", { selector: "input" }),
      {
        target: { value: "replacement" },
      },
    );
    fireEvent.click(
      screen.getByRole("button", { name: /OpenAI \(assistant\)/ }),
    );
    expect(
      (
        screen.getByLabelText("Provider key", {
          selector: "input",
        }) as HTMLInputElement
      ).value,
    ).toBe("temporary-secret");
    expect(screen.getByText("2 unsaved changes across providers")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Reset draft" }));
    expect(
      (
        screen.getByLabelText("Provider key", {
          selector: "input",
        }) as HTMLInputElement
      ).value,
    ).toBe("");
    fireEvent.click(screen.getByRole("button", { name: /Helper LLM/ }));
    expect(
      (
        screen.getByLabelText("Helper model", {
          selector: "input",
        }) as HTMLInputElement
      ).value,
    ).toBe("original");
    expect(
      (
        screen.getByRole("button", {
          name: "Save changes",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
  });
  it("preserves an unsaved provider draft after a failed save", async () => {
    const api = {
      get: vi.fn(async (path: string) =>
        path === "/api/settings" ? { settings: specs } : cfg,
      ),
      put: vi.fn(async () => {
        throw Error("Save refused");
      }),
    };
    const s = context(api);
    mount(<SettingsPage />, s);
    fireEvent.change(
      await screen.findByLabelText("Provider key", { selector: "input" }),
      {
        target: { value: "keep-this-draft" },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await screen.findByText("Save refused");
    expect(
      (
        screen.getByLabelText("Provider key", {
          selector: "input",
        }) as HTMLInputElement
      ).value,
    ).toBe("keep-this-draft");
    expect(screen.getByText("1 unsaved change across providers")).toBeTruthy();
    expect(JSON.stringify(s.evidence.snapshot())).not.toContain(
      "keep-this-draft",
    );
  });
  it("inspects projects independently of switching the active project", async () => {
    const s = context({
      get: vi.fn(async () => [
        {
          slug: "project",
          name: "Current",
          datasets: 2,
          jobs: 3,
          goal: "Current goal",
        },
        { slug: "next", name: "Next", datasets: 0, jobs: 0, goal: "Next goal" },
      ]),
    });
    mount(<ProjectsPage />, s);
    fireEvent.click(
      await within(screen.getByRole("table", { name: "Projects" })).findByRole(
        "button",
        { name: "Next" },
      ),
    );
    expect(screen.getByText("Next goal")).toBeTruthy();
    expect(s.selectProject).not.toHaveBeenCalled();
    fireEvent.change(
      screen.getByRole("searchbox", { name: "Find a project" }),
      { target: { value: "Current" } },
    );
    expect(screen.getByText("Next goal")).toBeTruthy();
    expect(
      within(screen.getByRole("table", { name: "Projects" })).queryByRole(
        "button",
        { name: "Next" },
      ),
    ).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Open project" }));
    await waitFor(() => expect(s.selectProject).toHaveBeenCalledWith("next"));
  });
  it("filters deployment accounts without changing account roles", async () => {
    const api = {
      get: vi.fn(async () => [
        { id: "self", email: "owner@test", name: "Owner", role: "admin" },
        { id: "other", email: "member@test", name: "Member", role: "member" },
      ]),
      patch: vi.fn(),
    };
    mount(<UsersPage />, context(api));
    await screen.findByText("owner@test");
    fireEvent.click(screen.getByRole("button", { name: /Account role/ }));
    fireEvent.click(screen.getByRole("option", { name: "Members" }));
    expect(screen.queryByText("owner@test")).toBeNull();
    expect(screen.getByText("member@test")).toBeTruthy();
    expect(api.patch).not.toHaveBeenCalled();
  });
  it("keeps SDK instructions on demand while filtering credentials", async () => {
    mount(
      <KeysPage />,
      context({
        get: vi.fn(async () => [
          { id: "a", name: "Production", prefix: "ts-prod" },
          { id: "b", name: "Development", prefix: "ts-dev" },
        ]),
      }),
    );
    await screen.findByText("Production");
    expect(screen.queryByRole("dialog", { name: "Using your key" })).toBeNull();
    fireEvent.change(
      screen.getByRole("searchbox", { name: "Find an API key" }),
      { target: { value: "Development" } },
    );
    expect(screen.queryByText("Production")).toBeNull();
    expect(screen.getByText("Development")).toBeTruthy();
    fireEvent.click(screen.getByText("Using your key"));
    expect(screen.getByRole("dialog", { name: "Using your key" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Close dialog" }));
    expect(
      (
        screen.getByRole("searchbox", {
          name: "Find an API key",
        }) as HTMLInputElement
      ).value,
    ).toBe("Development");
  });
});

// Empty results are a recoverable filter state, not an empty account directory.
describe("directory filter recovery", () => {
  it.each([
    {
      surface: "projects",
      component: <ProjectsPage />,
      rows: [{ slug: "project", name: "Current", datasets: 2, jobs: 3 }],
      search: "Find a project",
      record: "Current",
      empty: "No projects match this search.",
      clear: "Clear search",
    },
    {
      surface: "keys",
      component: <KeysPage />,
      rows: [{ id: "a", name: "Production", prefix: "ts-prod" }],
      search: "Find an API key",
      record: "Production",
      empty: "No API keys match this search.",
      clear: "Clear search",
    },
    {
      surface: "users",
      component: <UsersPage />,
      rows: [
        { id: "other", email: "member@test", name: "Member", role: "member" },
      ],
      search: "Find a user",
      record: "member@test",
      empty: "No users match these filters.",
      clear: "Clear filters",
    },
  ])(
    "restores the $surface directory without changing server data",
    async ({ component, rows, search, record, empty, clear }) => {
      const api = {
        get: vi.fn(async () => rows),
        patch: vi.fn(),
        del: vi.fn(),
      };
      const s = context(api);
      mount(component, s);
      const directory = await screen.findByRole("table");
      expect(within(directory).getByText(record)).toBeTruthy();
      fireEvent.change(screen.getByRole("searchbox", { name: search }), {
        target: { value: "missing-record" },
      });
      expect(within(directory).queryByText(record)).toBeNull();
      expect(screen.getByText(empty)).toBeTruthy();
      fireEvent.click(screen.getByRole("button", { name: clear }));
      expect(within(directory).getByText(record)).toBeTruthy();
      expect(screen.queryByText(empty)).toBeNull();
      expect(api.patch).not.toHaveBeenCalled();
      expect(api.del).not.toHaveBeenCalled();
      expect(s.selectProject).not.toHaveBeenCalled();
    },
  );
});
