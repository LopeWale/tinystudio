import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { StudioContext } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { ModelsPage } from "./models";

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
function setup(role = "admin", hubFailure = false) {
  const get = vi.fn(async (path: string): Promise<any> => {
    if (hubFailure && path === "/api/hub/models")
      throw new Error("Hub cache unavailable");
    return path === "/api/hub/models"
      ? {
          models: [
            { id: "org/cached", size_gb: 1.2, transformers_loadable: true },
          ],
          token: { configured: false },
          free_disk_gb: 10,
        }
      : [];
  });
  const studio = {
    api: { get, post: vi.fn(async () => ({})), del: vi.fn() },
    project: "fixture",
    scopeKey: "user:fixture",
    user: { id: "user", role },
    route: "/models",
    evidence: new EvidenceRegistry(),
    refreshOverview: vi.fn(async () => {}),
    confirm: vi.fn(async () => true),
    toast: vi.fn(),
    trackTask: vi.fn(),
  } as any;
  const tree = () => (
    <StudioContext.Provider value={studio}>
      <MemoryRouter>
        <ModelsPage />
      </MemoryRouter>
    </StudioContext.Provider>
  );
  const view = render(tree());
  fireEvent.click(
    screen.getByRole("button", { name: "Discover & manage Hub models" }),
  );
  return {
    studio,
    get,
    user: userEvent.setup(),
    view,
    tree,
    dialog: () =>
      screen.getByRole("dialog", { name: "Discover & manage Hub models" }),
  };
}
describe("Models Hub task workspace", () => {
  it("opens discovery alone, retains drafts across sections and dialog reopen, and navigates by keyboard", async () => {
    const { user, dialog } = setup();
    const hub = within(dialog());
    expect(
      hub.getByRole("heading", { name: "Discover models" }),
    ).toBeInTheDocument();
    expect(hub.queryByLabelText(/^Hub access token/)).toBeNull();
    expect(hub.queryByRole("table", { name: "Downloaded models" })).toBeNull();
    await user.type(
      hub.getByRole("textbox", { name: "Search the Hugging Face Hub" }),
      "Qwen",
    );
    const discover = hub.getByRole("tab", { name: "Discover" });
    discover.focus();
    await user.keyboard("{ArrowDown}");
    expect(hub.getByRole("tab", { name: /Downloaded/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(
      await hub.findByRole("table", { name: "Downloaded models" }),
    ).toBeInTheDocument();
    await user.keyboard("{ArrowDown}");
    await user.type(hub.getByLabelText(/^Hub access token/), "hf_draft");
    fireEvent.click(hub.getByRole("tab", { name: "Discover" }));
    expect(
      hub.getByRole("textbox", { name: "Search the Hugging Face Hub" }),
    ).toHaveValue("Qwen");
    fireEvent.click(hub.getByRole("tab", { name: "Connection" }));
    expect(hub.getByLabelText(/^Hub access token/)).toHaveValue("hf_draft");
    fireEvent.click(hub.getByRole("button", { name: "Close dialog" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Discover & manage Hub models" }),
    );
    expect(
      within(dialog()).getByRole("tab", { name: "Connection" }),
    ).toHaveAttribute("aria-selected", "true");
    expect(within(dialog()).getByLabelText(/^Hub access token/)).toHaveValue(
      "hf_draft",
    );
  });
  it("keeps search request inputs and download submission intact, with real activity in its own pane", async () => {
    const { studio, get, dialog, user } = setup();
    get.mockImplementation(async (path: string) =>
      path.startsWith("/api/hub/search?")
        ? [{ id: "org/result", params_b: 1, transformers_loadable: true }]
        : path === "/api/hub/models"
          ? { models: [], token: { configured: false } }
          : [],
    );
    studio.api.post.mockResolvedValue({
      task: {
        id: "download-1",
        kind: "download",
        state: "succeeded",
        log_tail: ["Downloaded successfully"],
      },
    });
    const hub = within(dialog());
    await user.type(
      hub.getByRole("textbox", { name: "Search the Hugging Face Hub" }),
      " org/model ",
    );
    fireEvent.click(hub.getByRole("button", { name: "Search" }));
    const download = await hub.findByRole("button", { name: "Download" });
    expect(get).toHaveBeenCalledWith(
      "/api/hub/search?q=org%2Fmodel&limit=15&task=text-generation&max_params_b=2",
      expect.any(AbortSignal),
    );
    fireEvent.click(download);
    expect(studio.api.post).toHaveBeenCalledWith("/api/hub/download", {
      repo_id: "org/result",
    });
    expect(hub.queryByText("Downloaded successfully")).toBeNull();
    fireEvent.click(hub.getByRole("tab", { name: /Activity/ }));
    expect(await hub.findByText("Downloaded successfully")).toBeInTheDocument();
  });
  it("displays async failures inside the modal even after changing panes", async () => {
    const { get, dialog } = setup();
    let rejectSearch!: (reason: Error) => void;
    get.mockImplementation((path: string) =>
      path.startsWith("/api/hub/search?")
        ? new Promise((_, reject) => {
            rejectSearch = reject;
          })
        : Promise.resolve([]),
    );
    const hub = within(dialog());
    fireEvent.click(hub.getByRole("button", { name: "Search" }));
    fireEvent.click(hub.getByRole("tab", { name: "Connection" }));
    rejectSearch(new Error("Hub search unavailable"));
    expect(await hub.findByRole("alert")).toHaveTextContent(
      "Hub search unavailable",
    );
  });
  it("discards a previous project's late search response even if its transport ignores abort", async () => {
    const { get, studio, view, tree, dialog } = setup();
    let resolveSearch!: (rows: any[]) => void;
    get.mockImplementation((path: string) =>
      path.startsWith("/api/hub/search?")
        ? new Promise((resolve) => {
            resolveSearch = resolve;
          })
        : Promise.resolve(
            path === "/api/hub/models"
              ? { models: [], token: { configured: false } }
              : [],
          ),
    );
    fireEvent.click(within(dialog()).getByRole("button", { name: "Search" }));
    studio.scopeKey = "user:next";
    studio.project = "next";
    view.rerender(tree());
    fireEvent.click(
      screen.getByRole("button", { name: "Discover & manage Hub models" }),
    );
    await act(async () => {
      resolveSearch([{ id: "old/late-result", transformers_loadable: true }]);
    });
    await vi.waitFor(() =>
      expect(
        within(dialog()).getByText(/Find your next model/),
      ).toBeInTheDocument(),
    );
    expect(within(dialog()).queryByText("old/late-result")).toBeNull();
    expect(
      within(dialog()).getByRole("button", { name: "Search" }),
    ).not.toBeDisabled();
  });
  it("explains observed offline search failures and retains the original error in technical details", async () => {
    const { get, dialog } = setup();
    get.mockImplementation(async (path: string) => {
      if (path.startsWith("/api/hub/search?"))
        throw new Error(
          "OfflineModeIsEnabled: cannot reach https://huggingface.co/api/models; HF_HUB_OFFLINE=1",
        );
      return [];
    });
    const hub = within(dialog());
    fireEvent.click(hub.getByRole("button", { name: "Search" }));
    expect(await hub.findByRole("alert")).toHaveTextContent(
      "Hub search is unavailable while the server is offline.",
    );
    expect(hub.getByRole("alert")).not.toHaveTextContent("HF_HUB_OFFLINE");
    expect(
      hub.getByText("Technical details").closest("details"),
    ).not.toHaveAttribute("open");
    expect(hub.getByText(/OfflineModeIsEnabled:/)).toBeInTheDocument();
  });
  it("keeps failed server status unknown instead of reporting zero models or an absent token", async () => {
    const { dialog } = setup("admin", true);
    const hub = within(dialog());
    expect(await hub.findByRole("alert")).toHaveTextContent(
      "Hub cache unavailable",
    );
    expect(hub.getByRole("tab", { name: "Downloaded" })).toBeInTheDocument();
    fireEvent.click(hub.getByRole("tab", { name: "Connection" }));
    expect(hub.getByText("Connection status unavailable")).toBeInTheDocument();
    expect(hub.queryByText("No token configured")).toBeNull();
  });
  it("preserves the administrator token boundary and resets drafts at a project scope change", async () => {
    const { studio, dialog, tree, view, user } = setup("member");
    const hub = within(dialog());
    fireEvent.click(hub.getByRole("tab", { name: "Connection" }));
    expect(hub.queryByLabelText(/^Hub access token/)).toBeNull();
    expect(hub.getByText(/Ask an administrator/)).toBeInTheDocument();
    fireEvent.click(hub.getByRole("tab", { name: "Discover" }));
    await user.type(
      hub.getByRole("textbox", { name: "Search the Hugging Face Hub" }),
      "old-project",
    );
    studio.scopeKey = "user:second";
    studio.project = "second";
    view.rerender(tree());
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(
      screen.getByRole("button", { name: "Discover & manage Hub models" }),
    );
    expect(
      within(dialog()).getByRole("textbox", {
        name: "Search the Hugging Face Hub",
      }),
    ).toHaveValue("");
  });
});

async function startQueuedDownload() {
  const context = setup();
  const baseGet = context.get.getMockImplementation()!;
  context.get.mockImplementation(async (path: string) =>
    path.startsWith("/api/hub/search?")
      ? [{ id: "org/download", transformers_loadable: true }]
      : baseGet(path),
  );
  context.studio.api.post.mockResolvedValue({
    task: { id: "task-1", kind: "download", state: "queued", log_tail: [] },
  });
  await act(async () =>
    fireEvent.click(
      within(context.dialog()).getByRole("button", { name: "Search" }),
    ),
  );
  vi.useFakeTimers();
  await act(async () =>
    fireEvent.click(
      within(context.dialog()).getByRole("button", { name: "Download" }),
    ),
  );
  fireEvent.click(
    within(context.dialog()).getByRole("tab", { name: /Activity/ }),
  );
  return context;
}

describe("Hub task lifecycle and recovery", () => {
  afterEach(() => vi.useRealTimers());
  it("polls queued to running to succeeded and refreshes inventory only on terminal transition", async () => {
    const { get, studio, dialog } = await startQueuedDownload();
    const baseGet = get.getMockImplementation()!;
    let polls = 0;
    get.mockImplementation(async (path: string) =>
      path.startsWith("/api/tasks/")
        ? {
            id: "task-1",
            kind: "download",
            state: ++polls === 1 ? "running" : "succeeded",
            log_tail: [polls === 1 ? "Fetching weights" : "Download complete"],
          }
        : baseGet(path),
    );
    expect(within(dialog()).getByText("queued")).toBeInTheDocument();
    const inventoryReads = get.mock.calls.filter(
      ([path]) => path === "/api/models",
    ).length;
    await act(async () => vi.advanceTimersByTimeAsync(2500));
    expect(within(dialog()).getByText("running")).toBeInTheDocument();
    expect(studio.refreshOverview).not.toHaveBeenCalled();
    await act(async () => vi.advanceTimersByTimeAsync(2500));
    expect(within(dialog()).getByText("succeeded")).toBeInTheDocument();
    expect(
      within(dialog()).queryByRole("button", { name: "Stop task" }),
    ).toBeNull();
    expect(studio.refreshOverview).toHaveBeenCalledTimes(1);
    expect(
      get.mock.calls.filter(([path]) => path === "/api/models"),
    ).toHaveLength(inventoryReads + 1);
    expect(get).toHaveBeenCalledWith(
      "/api/tasks/task-1?include_result=false",
      expect.any(AbortSignal),
    );
    await act(async () => vi.advanceTimersByTimeAsync(5000));
    expect(polls).toBe(2);
  });
  it("clears a recovered poll error while preserving an unrelated search failure", async () => {
    const { get, dialog } = await startQueuedDownload();
    const baseGet = get.getMockImplementation()!;
    let polls = 0;
    get.mockImplementation(async (path: string) => {
      if (path.startsWith("/api/tasks/")) {
        if (++polls === 1)
          throw new Error("Task status temporarily unavailable");
        return {
          id: "task-1",
          kind: "download",
          state: "running",
          log_tail: [],
        };
      }
      if (path.startsWith("/api/hub/search?"))
        throw new Error("Separate search failure");
      return baseGet(path);
    });
    await act(async () => vi.advanceTimersByTimeAsync(2500));
    expect(within(dialog()).getByRole("alert")).toHaveTextContent(
      "Task status temporarily unavailable",
    );
    fireEvent.click(within(dialog()).getByRole("tab", { name: "Discover" }));
    await act(async () =>
      fireEvent.click(within(dialog()).getByRole("button", { name: "Search" })),
    );
    expect(within(dialog()).getAllByRole("alert")).toHaveLength(2);
    await act(async () => vi.advanceTimersByTimeAsync(2500));
    expect(within(dialog()).getByRole("alert")).toHaveTextContent(
      "Separate search failure",
    );
    expect(
      within(dialog()).queryByText("Task status temporarily unavailable"),
    ).toBeNull();
  });
  it("clears poll failure and stops the previous project's polling when the scope changes", async () => {
    const { get, studio, view, tree, dialog } = await startQueuedDownload();
    const baseGet = get.getMockImplementation()!;
    let polls = 0;
    get.mockImplementation(async (path: string) => {
      if (path.startsWith("/api/tasks/")) {
        polls++;
        throw new Error("Old project poll failed");
      }
      return baseGet(path);
    });
    await act(async () => vi.advanceTimersByTimeAsync(2500));
    expect(within(dialog()).getByRole("alert")).toHaveTextContent(
      "Old project poll failed",
    );
    studio.scopeKey = "user:next";
    studio.project = "next";
    view.rerender(tree());
    fireEvent.click(
      screen.getByRole("button", { name: "Discover & manage Hub models" }),
    );
    expect(within(dialog()).queryByRole("alert")).toBeNull();
    await act(async () => vi.advanceTimersByTimeAsync(5000));
    expect(polls).toBe(1);
  });
  it("clears a poll failure when a successful stop resolves the final active task, preserving unrelated errors", async () => {
    const { get, studio, dialog } = await startQueuedDownload();
    const baseGet = get.getMockImplementation()!;
    get.mockImplementation(async (path: string) => {
      if (path === "/api/tasks/task-1?include_result=false")
        throw new Error("Poll unavailable before stop");
      if (path === "/api/tasks/task-1")
        return {
          id: "task-1",
          kind: "download",
          state: "stopped",
          log_tail: [],
        };
      if (path.startsWith("/api/hub/search?"))
        throw new Error("Independent search failure");
      return baseGet(path);
    });
    await act(async () => vi.advanceTimersByTimeAsync(2500));
    expect(within(dialog()).getByRole("alert")).toHaveTextContent(
      "Poll unavailable before stop",
    );
    let resolveStop!: (result: any) => void;
    studio.api.post.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveStop = resolve;
        }),
    );
    fireEvent.click(
      within(dialog()).getByRole("button", { name: "Stop task" }),
    );
    fireEvent.click(within(dialog()).getByRole("tab", { name: "Discover" }));
    await act(async () =>
      fireEvent.click(within(dialog()).getByRole("button", { name: "Search" })),
    );
    expect(within(dialog()).getAllByRole("alert")).toHaveLength(2);
    fireEvent.click(within(dialog()).getByRole("tab", { name: /Activity/ }));
    await act(async () => resolveStop({}));
    expect(within(dialog()).getByText("stopped")).toBeInTheDocument();
    expect(within(dialog()).getByRole("alert")).toHaveTextContent(
      "Independent search failure",
    );
    expect(
      within(dialog()).queryByText("Poll unavailable before stop"),
    ).toBeNull();
    const taskReads = get.mock.calls.filter(([path]) =>
      path.startsWith("/api/tasks/"),
    ).length;
    await act(async () => vi.advanceTimersByTimeAsync(5000));
    expect(
      get.mock.calls.filter(([path]) => path.startsWith("/api/tasks/")),
    ).toHaveLength(taskReads);
  });
  it.each([false, true])(
    "handles stop request failure=%s without inventing a terminal state",
    async (failure) => {
      const { get, studio, dialog } = await startQueuedDownload();
      const baseGet = get.getMockImplementation()!;
      get.mockImplementation(async (path: string) =>
        path === "/api/tasks/task-1"
          ? {
              id: "task-1",
              kind: "download",
              state: "stopped",
              log_tail: ["Stopped by user"],
            }
          : baseGet(path),
      );
      studio.api.post.mockImplementation(async () => {
        if (failure) throw new Error("Stop request denied");
        return {};
      });
      await act(async () =>
        fireEvent.click(
          within(dialog()).getByRole("button", { name: "Stop task" }),
        ),
      );
      expect(studio.api.post).toHaveBeenCalledWith(
        "/api/tasks/task-1/stop",
        {},
      );
      if (failure) {
        expect(within(dialog()).getByRole("alert")).toHaveTextContent(
          "Stop request denied",
        );
        expect(within(dialog()).getByText("queued")).toBeInTheDocument();
        expect(
          within(dialog()).getByRole("button", { name: "Stop task" }),
        ).toBeEnabled();
        expect(get).not.toHaveBeenCalledWith("/api/tasks/task-1");
      } else {
        expect(within(dialog()).getByText("stopped")).toBeInTheDocument();
        expect(
          within(dialog()).queryByRole("button", { name: "Stop task" }),
        ).toBeNull();
        expect(get).toHaveBeenCalledWith("/api/tasks/task-1");
      }
    },
  );
  it.each([false, true])(
    "discards pending download completion failure=%s after scope changes",
    async (failure) => {
      const { get, studio, view, tree, dialog } = setup();
      const baseGet = get.getMockImplementation()!;
      get.mockImplementation(async (path: string) =>
        path.startsWith("/api/hub/search?")
          ? [{ id: "old/model", transformers_loadable: true }]
          : baseGet(path),
      );
      let resolveDownload!: (value: any) => void;
      let rejectDownload!: (reason: Error) => void;
      studio.api.post.mockImplementation(
        () =>
          new Promise((resolve, reject) => {
            resolveDownload = resolve;
            rejectDownload = reject;
          }),
      );
      await act(async () =>
        fireEvent.click(
          within(dialog()).getByRole("button", { name: "Search" }),
        ),
      );
      fireEvent.click(
        within(dialog()).getByRole("button", { name: "Download" }),
      );
      studio.scopeKey = "user:next";
      studio.project = "next";
      view.rerender(tree());
      fireEvent.click(
        screen.getByRole("button", { name: "Discover & manage Hub models" }),
      );
      await act(async () => {
        if (failure)
          rejectDownload(new Error("Previous project rejected download"));
        else
          resolveDownload({
            task: { id: "old-task", kind: "download", state: "queued" },
          });
      });
      fireEvent.click(within(dialog()).getByRole("tab", { name: /Activity/ }));
      expect(
        within(dialog()).getByText(/No Hub activity yet/),
      ).toBeInTheDocument();
      expect(within(dialog()).queryByRole("alert")).toBeNull();
      expect(studio.trackTask).not.toHaveBeenCalled();
      expect(studio.toast).not.toHaveBeenCalled();
    },
  );
});
