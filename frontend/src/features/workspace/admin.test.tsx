import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { StudioContext } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { SettingsPage, settingsChanges, type SettingSpec } from "./settings";
import { KeysPage } from "./keys";
import { ProjectsPage } from "./projects";
import { UsersPage } from "./users";
import { StatsPage } from "./stats";
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
function runtime(api: any, role = "admin") {
  return {
    api,
    project: "fixture",
    scopeKey: "u:fixture",
    user: { id: "self", role },
    route: "/settings",
    evidence: new EvidenceRegistry(),
    overview: {},
    toast: vi.fn(),
    confirm: vi.fn(async () => true),
    navigate: vi.fn(),
    selectProject: vi.fn(),
    refreshProjects: vi.fn(async () => {}),
    refreshOverview: vi.fn(async () => {}),
  } as any;
}
function wrap(child: any, s: any) {
  return render(
    <StudioContext.Provider value={s}>
      <MemoryRouter>{child}</MemoryRouter>
    </StudioContext.Provider>,
  );
}
const specs: SettingSpec[] = [
  {
    key: "API_SECRET",
    label: "Provider key",
    group: "openai",
    secret: true,
    set: true,
    source: "database",
  },
  {
    key: "BASE_URL",
    label: "Base URL",
    group: "helper",
    secret: false,
    set: true,
    value: "https://provider.example/v1",
    source: "environment",
  },
  {
    key: "MODEL",
    label: "Model",
    group: "helper",
    secret: false,
    set: false,
    value: null,
    source: "default",
  },
];
const cfg = {
  database_url: "postgresql://hidden@db",
  redis_url: "redis://queue",
  qdrant_url: "http://vectors",
  database_version: "17",
  inference: { mode: "in-process" },
  device: "cpu",
  dtype: "float32",
  data_dir: "/data",
};
describe("saved settings semantics", () => {
  it("does not overwrite hidden secrets or unchanged inherited values", () => {
    expect(settingsChanges(specs, {}, {})).toEqual({});
    expect(
      settingsChanges(
        specs,
        { API_SECRET: "", BASE_URL: "https://provider.example/v1" },
        {},
      ),
    ).toEqual({});
  });
  it("clear wins over replacement and blank nonsecret removes saved override", () => {
    expect(
      settingsChanges(
        specs,
        { API_SECRET: "replacement", BASE_URL: "" },
        { API_SECRET: true },
      ),
    ).toEqual({ API_SECRET: null, BASE_URL: null });
    expect(settingsChanges(specs, { API_SECRET: "new-key" }, {})).toEqual({
      API_SECRET: "new-key",
    });
  });
  it("shows source and configured state; saves only explicit changes and invalidates assistant models", async () => {
    const api = {
        get: vi.fn(async (p: string) =>
          p === "/api/settings" ? { settings: specs } : cfg,
        ),
        put: vi.fn(async () => ({})),
      },
      s = runtime(api),
      listener = vi.fn();
    window.addEventListener("tinystudio:assistant-models-changed", listener);
    wrap(<SettingsPage />, s);
    await screen.findByLabelText("Provider key", { selector: "input" });
    expect(screen.getByText("database · configured")).toBeTruthy();
    expect(
      (
        screen.getByLabelText("Provider key", {
          selector: "input",
        }) as HTMLInputElement
      ).value,
    ).toBe("");
    fireEvent.change(
      screen.getByLabelText("Provider key", { selector: "input" }),
      {
        target: { value: "new-key" },
      },
    );
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Clear saved Provider key" }),
    );
    fireEvent.click(screen.getByRole("button", { name: /Helper LLM/ }));
    fireEvent.change(screen.getByRole("textbox", { name: "Base URL" }), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() =>
      expect(api.put).toHaveBeenCalledWith("/api/settings", {
        values: { API_SECRET: null, BASE_URL: null },
      }),
    );
    expect(listener).toHaveBeenCalledOnce();
    expect(JSON.stringify(s.evidence.snapshot())).not.toContain("new-key");
    window.removeEventListener("tinystudio:assistant-models-changed", listener);
  });
  it("connection testing is explicit and presents actual server failure", async () => {
    const api = {
        get: vi.fn(async (p: string) =>
          p === "/api/settings" ? { settings: specs } : cfg,
        ),
        post: vi.fn(async () => ({
          ok: false,
          detail: "No saved credentials",
        })),
      },
      s = runtime(api);
    wrap(<SettingsPage />, s);
    await screen.findByRole("button", { name: "Test openai connection" });
    expect(api.post).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole("button", { name: "Test openai connection" }),
    );
    await screen.findByText("failed · No saved credentials");
    expect(api.post).toHaveBeenCalledWith("/api/settings/test", {
      what: "openai",
    });
  });
  it("members cannot read deployment settings or admin accounts", async () => {
    const api = { get: vi.fn() },
      s = runtime(api, "member");
    const view = wrap(<SettingsPage />, s);
    expect(screen.getByRole("alert").textContent).toContain("Administrator");
    expect(api.get).not.toHaveBeenCalled();
    view.unmount();
    wrap(<UsersPage />, s);
    expect(screen.getByRole("alert").textContent).toContain("Administrator");
    expect(api.get).not.toHaveBeenCalled();
  });
});
describe("API key ownership and one-time secret", () => {
  it("displays actual returned secret once, never registers it as evidence, and clears it on dismissal", async () => {
    const api = {
        get: vi.fn(async () => []),
        post: vi.fn(async () => ({
          id: "k",
          key: "ts-fixture-ONE-TIME",
          prefix: "ts-fixture",
        })),
      },
      s = runtime(api);
    wrap(<KeysPage />, s);
    await screen.findByText(
      "No keys yet. Create a key for /v1 or /api; revoke access at any time.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Create API key" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "client" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Create", exact: true } as any),
    );
    await screen.findByText("ts-fixture-ONE-TIME");
    expect(api.post).toHaveBeenCalledWith("/api/keys", { name: "client" });
    expect(JSON.stringify(s.evidence.snapshot())).not.toContain(
      "ts-fixture-ONE-TIME",
    );
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(screen.queryByText("ts-fixture-ONE-TIME")).toBeNull();
  });
  it("does not revoke when confirmation is declined", async () => {
    const api = {
        get: vi.fn(async () => [
          { id: "k1", name: "client", prefix: "ts-abc" },
        ]),
        del: vi.fn(),
      },
      s = runtime(api);
    s.confirm.mockResolvedValue(false);
    wrap(<KeysPage />, s);
    fireEvent.click(
      await screen.findByRole("button", { name: "Revoke client" }),
    );
    await waitFor(() => expect(s.confirm).toHaveBeenCalled());
    expect(api.del).not.toHaveBeenCalled();
  });
});
describe("project lifecycle", () => {
  it("creates, refreshes available projects, selects returned slug and opens datasets", async () => {
    const api = {
        get: vi.fn(async () => []),
        post: vi.fn(async () => ({ slug: "new-project" })),
      },
      s = runtime(api);
    wrap(<ProjectsPage />, s);
    fireEvent.click(screen.getByRole("button", { name: "New project" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: " New project " },
    });
    fireEvent.change(
      screen.getByRole("textbox", {
        name: "Goal (what the model should become good at)",
      }),
      { target: { value: " Answer questions " } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Create project" }));
    await waitFor(() =>
      expect(s.selectProject).toHaveBeenCalledWith("new-project"),
    );
    expect(api.post).toHaveBeenCalledWith("/api/projects", {
      name: "New project",
      goal: "Answer questions",
    });
    expect(s.refreshProjects).toHaveBeenCalled();
    expect(s.navigate).toHaveBeenCalledWith("#/datasets");
  });
  it("offers no last-project deletion and no member deletion", async () => {
    const api = {
        get: vi.fn(async () => [
          { name: "Only project", slug: "fixture", datasets: 0, jobs: 0 },
        ]),
      },
      s = runtime(api);
    wrap(<ProjectsPage />, s);
    await screen.findByRole("button", { name: "Only project" });
    expect(
      screen.queryByRole("button", { name: "Delete Only project" }),
    ).toBeNull();
  });
});
describe("user administration", () => {
  const users = [
    {
      id: "self",
      email: "admin@example.test",
      name: "Owner",
      role: "admin",
      disabled: false,
    },
    {
      id: "other",
      email: "member@example.test",
      name: "Member",
      role: "member",
      disabled: false,
    },
  ];
  it("protects self role/disable/delete while allowing administrator operations on other accounts", async () => {
    const api = {
        get: vi.fn(async () => users),
        patch: vi.fn(async () => ({})),
      },
      s = runtime(api);
    wrap(<UsersPage />, s);
    await screen.findByRole("combobox", {
      name: "Role for admin@example.test",
    });
    expect(
      (
        screen.getByRole("combobox", {
          name: "Role for admin@example.test",
        }) as HTMLSelectElement
      ).disabled,
    ).toBe(true);
    expect(
      screen.queryByRole("button", { name: "Delete admin@example.test" }),
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Disable admin@example.test" }),
    ).toBeNull();
    fireEvent.change(
      screen.getByRole("combobox", { name: "Role for member@example.test" }),
      { target: { value: "admin" } },
    );
    await waitFor(() =>
      expect(api.patch).toHaveBeenCalledWith("/api/users/other", {
        role: "admin",
      }),
    );
  });
  it("creates users with chosen role and rejects too-short password without submission", async () => {
    const api = {
        get: vi.fn(async () => users),
        post: vi.fn(async () => ({})),
      },
      s = runtime(api);
    wrap(<UsersPage />, s);
    fireEvent.click(screen.getByRole("button", { name: "Add user" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Email" }), {
      target: { value: "new@example.test" },
    });
    fireEvent.change(screen.getByLabelText("Password (8+ characters)"), {
      target: { value: "short" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Create", exact: true } as any),
    );
    await screen.findAllByText("Password must have at least 8 characters");
    expect(api.post).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Password (8+ characters)"), {
      target: { value: "fixture-long-password" },
    });
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Give administrator access" }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Create", exact: true } as any),
    );
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith("/api/users", {
        email: "new@example.test",
        name: "",
        password: "fixture-long-password",
        role: "admin",
      }),
    );
    expect(JSON.stringify(s.evidence.snapshot())).not.toContain(
      "fixture-long-password",
    );
  });
});
describe("actual aggregate statistics", () => {
  it("retains token total and selected day identity without fabricating request drilldown", async () => {
    const api = {
        get: vi.fn(async () => ({
          datasets: 2,
          datapoints: 100,
          snapshots: 1,
          jobs: 1,
          experiments: { succeeded: 3 },
          training_seconds: 180,
          evaluations: 4,
          requests: 5,
          prompt_tokens: 8,
          completion_tokens: 13,
          scored_traces: 2,
          requests_per_day: [
            ["2026-10-20", 2],
            ["2026-10-21", 3],
          ],
          requests_per_model: [["fixture/model", 5]],
          models_loaded: ["fixture/model"],
        })),
      },
      s = runtime(api);
    wrap(<StatsPage />, s);
    await screen.findByText("21");
    fireEvent.click(
      screen.getByRole("button", { name: "2026-10-21: 3 requests" }),
    );
    expect(screen.getByRole("status").textContent).toContain(
      "2026-10-21: 3 requests",
    );
    expect(screen.getByRole("status").textContent).toContain(
      "no per-day request drilldown",
    );
  });
});
