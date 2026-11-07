import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { StudioContext } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { DatasetPage } from "./datasets";
import { previewQuality } from "../../catalog/workspace-preview-quality";
import { ComponentActionRegistry } from "../../controllers/componentActions";
import { ComponentActionsProvider } from "../assistant/actions";
afterEach(cleanup);
function deferred<T = any>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
function mount(
  profileGet = vi.fn(async () => structuredClone(previewQuality)),
  post: any = vi.fn(async () => ({
    before: 3,
    after: 2,
    removed: { drop_empty: 1 },
  })),
  confirm = vi.fn(async () => true),
) {
  const api = {
    get: vi.fn(async (path: string) =>
      path.endsWith("/quality")
        ? profileGet()
        : path.startsWith("/api/tasks/")
          ? { id: "clean", state: "succeeded" }
          : path === "/api/snapshots"
            ? []
            : { meta: { buckets: {} }, total: 3, rows: [] },
    ),
    post,
  };
  const studio = {
    api,
    project: "p",
    scopeKey: "u:p",
    user: { id: "u" },
    route: "/datasets/example",
    evidence: new EvidenceRegistry(),
    overview: { tree: { datasets: [] } },
    toast: vi.fn(),
    trackTask: vi.fn(),
    showTasks: vi.fn(),
    refreshOverview: vi.fn(async () => {}),
    confirm,
  } as any;
  const actions = new ComponentActionRegistry(confirm);
  actions.setScope({ user: "u", project: "p", route: "/datasets/example" });
  const tree = (value: any) => (
    <StudioContext.Provider value={value}>
      <ComponentActionsProvider registry={actions}>
        <MemoryRouter initialEntries={["/datasets/example"]}>
          <Routes>
            <Route path="/datasets/:name" element={<DatasetPage />} />
          </Routes>
        </MemoryRouter>
      </ComponentActionsProvider>
    </StudioContext.Provider>
  );
  const view = render(tree(studio));
  return {
    ...view,
    studio,
    api,
    profileGet,
    actions,
    changeProject: () =>
      view.rerender(tree({ ...studio, project: "other", scopeKey: "u:other" })),
  };
}
async function openQuality() {
  fireEvent.click(
    await screen.findByRole("button", { name: "Quality", exact: true } as any),
  );
  await screen.findByText("Conversation structure");
}
function cleaning() {
  fireEvent.click(screen.getByRole("tab", { name: "Cleaning" }));
}
describe("dataset quality task panes", () => {
  it("only allows assistant distribution controls in their visible pane and revokes them on close", async () => {
    const { actions, api } = mount();
    await openQuality();
    const action = (id: string) =>
      actions.inspect().actions.find((item) => item.id === id)!;
    const ids = [
      "dataset.quality.distribution",
      "dataset.quality.presentation",
      "dataset.quality.composition",
    ];
    for (const id of ids) expect(action(id).disabled).toBe(true);
    await expect(
      actions.execute(action(ids[0]).ref, "ratings", false),
    ).rejects.toThrow("disabled");
    await act(async () => {
      await actions.execute(
        action("dataset.quality.view").ref,
        "distributions",
        false,
      );
    });
    for (const id of ids) expect(action(id).disabled).toBe(false);
    await act(async () => {
      await actions.execute(action(ids[0]).ref, "ratings", false);
    });
    expect(screen.getByRole("radio", { name: "Ratings" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    cleaning();
    for (const id of ids) expect(action(id).disabled).toBe(true);
    fireEvent.click(screen.getByRole("tab", { name: "Distributions" }));
    expect(action(ids[0]).state).toBe("ratings");
    const ref = action(ids[0]).ref;
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(
      actions
        .inspect()
        .actions.filter((item) => item.id.startsWith("dataset.quality.")),
    ).toEqual([]);
    await expect(actions.execute(ref, "user_chars", false)).rejects.toThrow(
      "stale",
    );
    expect(api.post).not.toHaveBeenCalled();
  });
  it("rejects contradictory and invalid limits before confirmation and recovers when corrected", async () => {
    const confirm = vi.fn(async () => true);
    const { api } = mount(undefined, undefined, confirm);
    await openQuality();
    cleaning();
    const apply = screen.getByRole("button", { name: "Apply cleaning" });
    const minimum = screen.getByRole("spinbutton", { name: "Min total chars" });
    const maximum = screen.getByRole("spinbutton", { name: "Max total chars" });
    const rating = screen.getByRole("spinbutton", {
      name: "Min rating (0–10)",
    });
    fireEvent.change(minimum, { target: { value: "10" } });
    fireEvent.change(maximum, { target: { value: "5" } });
    expect(apply).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Minimum characters cannot exceed maximum characters",
    );
    fireEvent.change(maximum, { target: { value: "20" } });
    expect(apply).toBeEnabled();
    for (const value of ["-1", "1.5"]) {
      fireEvent.change(minimum, { target: { value } });
      expect(apply).toBeDisabled();
    }
    fireEvent.change(minimum, { target: { value: "0" } });
    fireEvent.change(rating, { target: { value: "11" } });
    expect(apply).toBeDisabled();
    fireEvent.click(apply);
    expect(confirm).not.toHaveBeenCalled();
    expect(api.post).not.toHaveBeenCalled();
    fireEvent.change(rating, { target: { value: "7.5" } });
    expect(apply).toBeEnabled();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keeps Apply unavailable without a chosen operation and treats zero as an explicit threshold", async () => {
    const { api } = mount();
    await openQuality();
    cleaning();
    const apply = screen.getByRole("button", { name: "Apply cleaning" });
    expect(apply).toBeDisabled();
    fireEvent.change(
      screen.getByRole("textbox", { name: "Drop labels (comma separated)" }),
      { target: { value: " , , " } },
    );
    expect(apply).toBeDisabled();
    fireEvent.change(
      screen.getByRole("spinbutton", { name: "Min total chars" }),
      { target: { value: "0" } },
    );
    expect(apply).toBeEnabled();
    expect(screen.getByText(/1 selected/)).toBeVisible();
    fireEvent.change(
      screen.getByRole("spinbutton", { name: "Min total chars" }),
      { target: { value: "" } },
    );
    expect(apply).toBeDisabled();
    expect(api.post).not.toHaveBeenCalled();
  });
  it("keeps each distribution and its exact counts in one region without mixing overlapping labels", async () => {
    const { api } = mount();
    await openQuality();
    fireEvent.click(screen.getByRole("tab", { name: "Distributions" }));
    expect(screen.getAllByRole("img")).toHaveLength(1);
    fireEvent.click(screen.getByRole("radio", { name: "Responses" }));
    expect(
      screen.getByRole("img", { name: "Assistant response length (chars)" }),
    ).toBeVisible();
    fireEvent.click(screen.getByRole("radio", { name: "Bin counts" }));
    expect(screen.queryByRole("img")).toBeNull();
    const table = screen.getByRole("table", {
      name: "Assistant response length (chars) bin counts",
    });
    expect(within(table).getAllByRole("row")).toHaveLength(
      previewQuality.answer_chars.histogram.counts.length + 1,
    );
    fireEvent.click(screen.getByRole("radio", { name: "Ratings" }));
    expect(
      screen.getByRole("table", { name: "Curation ratings bin counts" }),
    ).toBeVisible();
    fireEvent.click(screen.getByRole("radio", { name: "Chart" }));
    expect(screen.getByRole("img", { name: "Curation ratings" })).toBeVisible();
    fireEvent.click(screen.getByRole("radio", { name: "Labels" }));
    expect(
      screen.getByText(/Counts overlap and must not be added/),
    ).toBeVisible();
    expect(screen.getByRole("table", { name: "Dataset labels" })).toBeVisible();
    fireEvent.click(screen.getByRole("tab", { name: "Overview" }));
    fireEvent.click(screen.getByRole("tab", { name: "Distributions" }));
    expect(screen.getByRole("radio", { name: "Ratings" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Labels" })).toBeChecked();
    expect(api.post).not.toHaveBeenCalled();
  });

  it("uses keyboard-linked panes, retains cleaning drafts and does not mutate while navigating", async () => {
    const { api } = mount();
    await openQuality();
    expect(screen.getByRole("tabpanel", { name: "Overview" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Apply cleaning" })).toBeNull();
    const tab = screen.getByRole("tab", { name: "Overview" });
    tab.focus();
    fireEvent.keyDown(tab, { key: "ArrowRight" });
    const distributions = screen.getByRole("tabpanel", {
      name: "Distributions",
    });
    expect(distributions.id).toBe(
      screen
        .getByRole("tab", { name: "Distributions" })
        .getAttribute("aria-controls"),
    );
    expect(
      screen.getByRole("img", { name: "User message length (chars)" }),
    ).toBeVisible();
    cleaning();
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Remove empty rows" }),
    );
    fireEvent.change(
      screen.getByRole("spinbutton", { name: /Min total chars/ }),
      { target: { value: "0" } },
    );
    fireEvent.change(
      screen.getByRole("textbox", { name: "Drop labels (comma separated)" }),
      { target: { value: " low, junk , " } },
    );
    fireEvent.click(screen.getByRole("tab", { name: "Overview" }));
    cleaning();
    expect(
      screen.getByRole("checkbox", { name: "Remove empty rows" }),
    ).toBeChecked();
    expect(
      screen.getByRole("spinbutton", { name: /Min total chars/ }),
    ).toHaveValue(0);
    expect(api.post).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Apply cleaning" }));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith("/api/datasets/example/clean", {
        drop_empty: true,
        min_total_chars: 0,
        max_total_chars: null,
        min_rating: null,
        drop_labels: ["low", "junk"],
      }),
    );
    expect(await screen.findByText(/Profile captured before/)).toBeVisible();
    fireEvent.click(screen.getByRole("tab", { name: "Overview" }));
    expect(screen.getByText(/Profile captured before/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Refresh profile" }));
    await screen.findByText("Conversation structure");
    expect(screen.queryByText(/Profile captured before/)).toBeNull();
  });
  it("keeps cancelled cleaning unchanged and prevents repeated submissions during confirmation", async () => {
    const confirm = deferred<boolean>();
    const { api } = mount(
      undefined,
      undefined,
      vi.fn(() => confirm.promise),
    );
    await openQuality();
    cleaning();
    fireEvent.click(screen.getByRole("checkbox", { name: "Trim whitespace" }));
    const apply = screen.getByRole("button", { name: "Apply cleaning" });
    apply.focus();
    fireEvent.click(apply);
    fireEvent.click(apply);
    expect(api.post).not.toHaveBeenCalled();
    expect(apply).toBeDisabled();
    await act(async () => confirm.resolve(false));
    expect(apply).toBeEnabled();
    expect(apply).toHaveFocus();
    expect(screen.queryByText(/Profile captured before/)).toBeNull();
    expect(api.post).not.toHaveBeenCalled();
  });
  it.each([false, true])(
    "qualifies queued cleaning and refreshes only after terminal status (envelope=%s)",
    async (envelope) => {
      const { api, profileGet } = mount(
        undefined,
        vi.fn(async () => {
          const task = {
            id: "clean",
            kind: "dataset.clean",
            state: "queued",
            args: {},
            result: null,
            error: null,
            project_id: "p",
            created: "2026-10-28",
          };
          return envelope ? { queued: true, task } : task;
        }),
      );
      const get = api.get;
      get.mockImplementation(async (path: string) =>
        path === "/api/tasks/clean"
          ? { id: "clean", state: "running" }
          : path.endsWith("/quality")
            ? profileGet()
            : path === "/api/snapshots"
              ? []
              : path.startsWith("/api/datasets/example?")
                ? { meta: { buckets: {} }, total: 3, rows: [] }
                : Promise.reject(Error(`Unexpected request: ${path}`)),
      );
      await openQuality();
      cleaning();
      fireEvent.click(
        screen.getByRole("checkbox", { name: "Remove empty rows" }),
      );
      fireEvent.click(screen.getByRole("button", { name: "Apply cleaning" }));
      await screen.findByText(/Cleaning queued as a background task/);
      expect(
        screen.getByRole("button", { name: "Apply cleaning" }),
      ).toBeDisabled();
      fireEvent.click(
        screen.getByRole("button", { name: "Check cleaning status" }),
      );
      await screen.findByText(/Cleaning running/);
      expect(
        get.mock.calls.filter(([path]) => path.startsWith("/api/tasks/")),
      ).toEqual([["/api/tasks/clean"]]);
      expect(profileGet).toHaveBeenCalledTimes(1);
      get.mockImplementation(async (path: string) =>
        path === "/api/tasks/clean"
          ? { id: "clean", state: "succeeded" }
          : path.endsWith("/quality")
            ? profileGet()
            : path === "/api/snapshots"
              ? []
              : path.startsWith("/api/datasets/example?")
                ? { meta: { buckets: {} }, total: 3, rows: [] }
                : Promise.reject(Error(`Unexpected request: ${path}`)),
      );
      fireEvent.click(
        screen.getByRole("button", { name: "Check cleaning status" }),
      );
      await waitFor(() => expect(profileGet).toHaveBeenCalledTimes(2));
      expect(
        get.mock.calls.filter(([path]) => path.startsWith("/api/tasks/")),
      ).toEqual([["/api/tasks/clean"], ["/api/tasks/clean"]]);
      await screen.findByRole("button", { name: "Apply cleaning" });
      expect(screen.queryByText(/Profile captured before/)).toBeNull();
    },
  );
  it("offers a retry for profile failure", async () => {
    const profileGet = vi
      .fn()
      .mockRejectedValueOnce(Error("Unavailable"))
      .mockResolvedValue(previewQuality);
    const { actions } = mount(profileGet);
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Quality",
        exact: true,
      } as any),
    );
    await screen.findByText("Unavailable");
    expect(
      actions
        .inspect()
        .actions.filter((item) => item.id.startsWith("dataset.quality.")),
    ).toEqual([
      expect.objectContaining({ id: "dataset.quality.view", disabled: true }),
    ]);
    fireEvent.click(screen.getByRole("button", { name: "Retry profile" }));
    await screen.findByText("Conversation structure");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(
      actions
        .inspect()
        .actions.find((item) => item.id === "dataset.quality.view")?.disabled,
    ).toBe(false);
  });
  it.each(["success", "failure", "queued"])(
    "ignores late %s from a dismissed profile request",
    async (outcome) => {
      const old = deferred();
      const profileGet = vi
        .fn()
        .mockImplementationOnce(() => old.promise)
        .mockResolvedValue(previewQuality);
      const { studio } = mount(profileGet);
      fireEvent.click(
        await screen.findByRole("button", {
          name: "Quality",
          exact: true,
        } as any),
      );
      await waitFor(() => expect(profileGet).toHaveBeenCalledTimes(1));
      fireEvent.click(screen.getByRole("button", { name: "Close dialog" }));
      await openQuality();
      await act(async () => {
        if (outcome === "failure") old.reject(Error("Old error"));
        else
          old.resolve(
            outcome === "queued"
              ? { queued: true, task: { id: "old", state: "queued" } }
              : { ...previewQuality, count: 999 },
          );
      });
      expect(screen.queryByText("999")).toBeNull();
      expect(screen.queryByText("Old error")).toBeNull();
      expect(studio.trackTask).not.toHaveBeenCalled();
    },
  );
  it("resets quality drafts on project change and rejects late scoped profile results", async () => {
    const old = deferred();
    const profileGet = vi
      .fn()
      .mockImplementationOnce(() => old.promise)
      .mockResolvedValue(previewQuality);
    const view = mount(profileGet);
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Quality",
        exact: true,
      } as any),
    );
    await waitFor(() => expect(profileGet).toHaveBeenCalledTimes(1));
    view.changeProject();
    expect(screen.queryByRole("dialog")).toBeNull();
    await openQuality();
    await act(async () => old.resolve({ ...previewQuality, count: 999 }));
    expect(screen.queryByText("999")).toBeNull();
    cleaning();
    expect(
      screen.getByRole("checkbox", { name: "Remove empty rows" }),
    ).not.toBeChecked();
  });
});
