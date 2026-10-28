import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { StudioContext } from "../src/app/StudioContext";
import { EvidenceRegistry } from "../src/app/evidence";
import { ComponentActionsProvider } from "../src/features/assistant/actions";
import { ComponentActionRegistry } from "../src/controllers/componentActions";
import { WorkspaceBar } from "../src/app/WorkspaceBar";
import { Shell } from "../src/app/App";
import { PlaygroundProvider } from "../src/app/PlaygroundProvider";
import { PlaygroundPage } from "../src/features/workspace/playground";
vi.mock("../src/features/assistant", () => ({
  AssistantProvider: ({ children }: { children: ReactNode }) => children,
  AssistantWidget: () => <button aria-label="Open assistant">Agent</button>,
  AssistantPage: () => null,
  useAssistantDock: () => ({
    expanded: false,
    open: vi.fn(),
    collapse: vi.fn(),
  }),
}));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
  sessionStorage.clear();
});
function runtime() {
  return {
    user: { id: "u", role: "admin" },
    project: "home",
    projects: [{ slug: "home", name: "Home" }],
    scopeKey: "u:home",
    route: "/playground",
    evidence: new EvidenceRegistry(),
    api: {
      get: vi.fn(async (path: string) =>
        path === "/api/models"
          ? [{ id: "model", alias: "Fixture model", status: "up" }]
          : path === "/api/hub/models"
            ? { models: [], token: { configured: false }, free_disk_gb: 1 }
            : [],
      ),
    },
    showTasks: vi.fn(),
    onSessionExpired: vi.fn(),
    toast: vi.fn(),
  } as any;
}
describe("compact workspace interaction boundaries", () => {
  it("uses the sidebar as desktop navigation and opens focused search without automatic page tabs", async () => {
    vi.stubGlobal("matchMedia", () => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    render(
      <StudioContext.Provider value={runtime()}>
        <MemoryRouter initialEntries={["/layout-fixture"]}>
          <Shell />
        </MemoryRouter>
      </StudioContext.Provider>,
    );
    const primary = screen.getByRole("navigation", { name: "Main navigation" });
    expect(primary.querySelector('a[href="/assistant"]')).toBeNull();
    for (const name of [
      "Datasets",
      "Repositories",
      "Laboratory",
      "Fine tuning",
      "Evaluations",
      "Metrics",
      "Interpretability",
      "Models",
      "Playground",
      "Traces",
      "Projects",
      "API keys",
      "Stats",
      "Settings",
      "Users",
    ])
      expect(within(primary).getByRole("link", { name })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Open assistant" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("navigation", { name: "Open work" }),
    ).not.toBeInTheDocument();
    for (const name of ["Pages", "Recent", "Go back", "Go forward"])
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    const trigger = screen.getByRole("button", { name: "Search pages" });
    trigger.focus();
    fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "Find a page" });
    await waitFor(() =>
      expect(within(dialog).getByLabelText("Find a page")).toHaveFocus(),
    );
    expect(within(dialog).queryByRole("link")).not.toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("Find a page"), {
      target: { value: "evalu" },
    });
    expect(
      within(dialog).getByRole("link", { name: "Evaluations" }),
    ).toBeInTheDocument();
    expect(
      within(dialog).queryByRole("link", { name: "Datasets" }),
    ).not.toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("Find a page"), {
      target: { value: "no-such-page" },
    });
    expect(within(dialog).getByRole("status")).toHaveTextContent(
      "No pages match",
    );
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(
      screen.getByRole("navigation", { name: "Main navigation" }),
    ).toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
  it("follows sidebar links and marks the active destination without accumulating page tabs", () => {
    vi.stubGlobal("matchMedia", () => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    const studio = runtime();
    render(
      <StudioContext.Provider value={studio}>
        <MemoryRouter initialEntries={["/datasets"]}>
          <WorkspaceBar theme="light" onTheme={vi.fn()} />
        </MemoryRouter>
      </StudioContext.Provider>,
    );
    const navigation = screen.getByRole("navigation", {
      name: "Main navigation",
    });
    expect(
      within(navigation).getByRole("link", { name: "Datasets" }),
    ).toHaveAttribute("aria-current", "page");
    fireEvent.click(
      within(navigation).getByRole("link", { name: "Repositories" }),
    );
    expect(
      within(navigation).getByRole("link", { name: "Repositories" }),
    ).toHaveAttribute("aria-current", "page");
    expect(
      within(navigation).getByRole("link", { name: "Datasets" }),
    ).not.toHaveAttribute("aria-current");
    fireEvent.click(within(navigation).getByRole("link", { name: "Models" }));
    expect(
      within(navigation).getByRole("link", { name: "Models" }),
    ).toHaveAttribute("aria-current", "page");
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(screen.queryByRole("navigation", { name: "Open work" })).toBeNull();
    expect(
      screen.queryByRole("button", {
        name: /^Close (?:Datasets|Repositories|Models)$/,
      }),
    ).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Tasks" }));
    expect(studio.showTasks).toHaveBeenCalledOnce();
    fireEvent.keyDown(document, { key: "k", metaKey: true });
    expect(
      screen.getByRole("dialog", { name: "Find a page" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("navigation", { name: "Page search results" }),
    ).toBeNull();
  });
  it("keeps mobile destinations reachable and excludes administrative pages for members", async () => {
    vi.stubGlobal("matchMedia", () => ({
      matches: true,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    const studio = runtime();
    studio.user.role = "member";
    render(
      <StudioContext.Provider value={studio}>
        <MemoryRouter initialEntries={["/layout-fixture"]}>
          <Shell />
        </MemoryRouter>
      </StudioContext.Provider>,
    );
    expect(
      screen.queryByRole("navigation", { name: "Main navigation" }),
    ).not.toBeInTheDocument();
    const sections = screen.getByRole("navigation", {
      name: "Workspace sections",
    });
    expect(within(sections).getAllByRole("button")).toHaveLength(4);
    fireEvent.click(
      within(sections).getByRole("button", { name: "Workspace" }),
    );
    let dialog = screen.getByRole("dialog", { name: "Workspace" });
    expect(
      within(dialog).getByRole("link", { name: "Projects" }),
    ).toBeInTheDocument();
    expect(
      within(dialog).queryByRole("link", { name: "Users" }),
    ).not.toBeInTheDocument();
    expect(
      within(dialog).queryByRole("link", { name: "Datasets" }),
    ).not.toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: /Current project/ }),
    ).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.click(within(sections).getByRole("button", { name: "Data" }));
    dialog = screen.getByRole("dialog", { name: "Data" });
    expect(
      within(dialog).getByRole("link", { name: "Datasets" }),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole("link", { name: "Repositories" }),
    ).toBeInTheDocument();
    expect(
      within(dialog).queryByRole("link", { name: "Projects" }),
    ).not.toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "Find a page" }));
    dialog = screen.getByRole("dialog", { name: "Find a page" });
    expect(
      within(dialog).queryByRole("navigation", { name: "Main navigation" }),
    ).not.toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("Find a page"), {
      target: { value: "Models" },
    });
    expect(
      within(dialog).getByRole("link", { name: "Models" }),
    ).toBeInTheDocument();
    expect(
      within(dialog).queryByRole("link", { name: "Datasets" }),
    ).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("link", { name: "Models" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(
        within(sections).getByRole("button", { name: "Explore" }),
      ).toHaveAttribute("data-current", "true"),
    );
    expect(screen.queryByRole("navigation", { name: "Open work" })).toBeNull();
  });
  it("executes registered Playground actions through the same controlled state as visible controls", async () => {
    const registry = new ComponentActionRegistry();
    registry.setScope({ user: "u", project: "home", route: "/playground" });
    render(
      <StudioContext.Provider value={runtime()}>
        <MemoryRouter>
          <ComponentActionsProvider registry={registry}>
            <PlaygroundProvider>
              <PlaygroundPage />
            </PlaygroundProvider>
          </ComponentActionsProvider>
        </MemoryRouter>
      </StudioContext.Provider>,
    );
    await screen.findByRole("option", { name: /Fixture model/ });
    const action = (id: string) =>
      registry.inspect().actions.find((action) => action.id === id)!;
    await act(async () => {
      await registry.execute(action("playground.inspector").ref, "open", false);
    });
    expect(screen.getByLabelText("System prompt")).toBeInTheDocument();
    await act(async () => {
      await registry.execute(
        action("playground.temperature").ref,
        "0.6",
        false,
      );
    });
    expect(screen.getByLabelText("Temperature · 0.6")).toHaveValue("0.6");
    await act(async () => {
      await registry.execute(
        action("playground.inspector").ref,
        "closed",
        false,
      );
    });
    expect(screen.queryByLabelText("System prompt")).not.toBeInTheDocument();
    expect(action("playground.inspector").state).toBe(false);
  });
  it("discloses configuration without clearing the composed prompt or replacing the transcript/composer", async () => {
    render(
      <StudioContext.Provider value={runtime()}>
        <MemoryRouter>
          <PlaygroundProvider>
            <PlaygroundPage />
          </PlaygroundProvider>
        </MemoryRouter>
      </StudioContext.Provider>,
    );
    await screen.findByRole("option", { name: /Fixture model/ });
    const transcript = screen.getByRole("log", {
      name: "Playground conversation",
    });
    const composer = screen.getByLabelText("Message your model");
    fireEvent.change(composer, {
      target: { value: "Keep this prompt while inspecting settings" },
    });
    expect(screen.queryByLabelText("System prompt")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show settings" }));
    expect(screen.getByLabelText("System prompt")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("System prompt"), {
      target: { value: "Use concise answers" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Hide settings" }));
    expect(screen.getByRole("log", { name: "Playground conversation" })).toBe(
      transcript,
    );
    expect(screen.getByLabelText("Message your model")).toBe(composer);
    expect(composer).toHaveValue("Keep this prompt while inspecting settings");
    fireEvent.click(screen.getByRole("button", { name: "Show settings" }));
    expect(screen.getByLabelText("System prompt")).toHaveValue(
      "Use concise answers",
    );
    expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();
  });
});
